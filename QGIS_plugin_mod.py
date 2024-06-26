
__revision__ = "$Format:%H$"

import os
import sys
import tempfile
import inspect
import processing
import codecs

from tempfile import NamedTemporaryFile
from area_weighted_average.processing.config import PLUGIN_VERSION, REGISTRATION_FORM_ENRIES, REGISTRATION_FORM_LINK


from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsFeatureSink,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFileDestination,
    QgsVectorFileWriter,
    QgsProcessingOutputHtml,
    QgsCoordinateReferenceSystem,
)

from area_weighted_average.processing.utils import (
    checkPluginUptodate,
    displayUsageMessage,
    getRegistrationStatus,
    incrementUsageCounter,
    getAndUpdateMessage,
)

from area_weighted_average.processing.registration import RegisterForm


cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]
sys.path.append(cmd_folder)


class AreaWeightedAverageAlgorithm(QgsProcessingAlgorithm):
    """ """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    OUTPUT = "OUTPUT"
    INPUT = "INPUT"

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                "inputlayer",
                "Input Layer",
                types=[QgsProcessing.TypeVectorPolygon],
                defaultValue=None,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                "overlaylayer",
                "Overlay Layer (Data Source)",
                types=[QgsProcessing.TypeVectorPolygon],
                defaultValue=None,
            )
        )

        param = QgsProcessingParameterField(
            "identifierfieldforreport",
            "Identifier Field for Report",
            optional=True,
            type=QgsProcessingParameterField.Any,
            parentLayerParameterName="inputlayer",
            allowMultiple=False,
            defaultValue="",
        )
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        self.addParameter(
            QgsProcessingParameterField(
                "fieldtoaverage",
                "Field to Average",
                type=QgsProcessingParameterField.Numeric,
                parentLayerParameterName="overlaylayer",
                allowMultiple=False,
                defaultValue=None,
            )
        )

        param = QgsProcessingParameterField(
            "additionalfields",
            "Additional Fields to Keep for Report",
            optional=True,
            type=QgsProcessingParameterField.Any,
            parentLayerParameterName="overlaylayer",
            allowMultiple=True,
        )
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "result",
                "Result",
                type=QgsProcessing.TypeVectorAnyGeometry,
                createByDefault=True,
                defaultValue=None,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "reportaslayer",
                "Report as Layer",
                type=QgsProcessing.TypeVectorAnyGeometry,
                createByDefault=True,
                defaultValue=None,
            )
        )

        self.addParameter(
            QgsProcessingParameterFileDestination(
                "reportasHTML",
                self.tr("Report as HTML"),
                self.tr("HTML files (*.html)"),
                None,
                True,
            )
        )

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        feedback = QgsProcessingMultiStepFeedback(13, model_feedback)
        results = {}
        outputs = {}

        input_layer = self.parameterAsVectorLayer(parameters, "inputlayer", context)
        overlay_layer = self.parameterAsVectorLayer(parameters, "overlaylayer", context)

        input_epsg_code = input_layer.crs().authid()
        overlay_epsg_code = overlay_layer.crs().authid()

        crs_input = QgsCoordinateReferenceSystem(input_epsg_code)
        crs_overlay = QgsCoordinateReferenceSystem(overlay_epsg_code)

        if crs_input.isGeographic():
            feedback.reportError(
                "CRS of the Input Layer is Geographic. Results accuracy may get impacted. For most accurate results, both input and overlay layers should be in the same Projected CRS\n"
            )

        if crs_overlay.isGeographic():
            feedback.reportError(
                "CRS of the Input Layer is Geographic. Results accuracy may get impacted. For most accurate results, both input and overlay layers should be in the same Projected CRS\n"
            )

        if input_epsg_code == overlay_epsg_code:
            pass
        else:
            feedback.reportError(
                "Input and Overlay Layers are in different CRS. For most accurate results, both input and overlay layers should be in the same Projected CRS\n"
            )

        # add_ID_field to input layer
        alg_params = {
            "FIELD_NAME": "input_feat_id",
            "GROUP_FIELDS": [""],
            "INPUT": parameters["inputlayer"],
            "SORT_ASCENDING": True,
            "SORT_EXPRESSION": "",
            "SORT_NULLS_FIRST": False,
            "START": 1,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Add_id_field"] = processing.run(
            "native:addautoincrementalfield",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # add_area_field to input layer
        alg_params = {
            "FIELD_LENGTH": 0,
            "FIELD_NAME": "area_awa",
            "FIELD_PRECISION": 0,
            "FIELD_TYPE": 0,
            "FORMULA": "area($geometry)",
            "INPUT": outputs["Add_id_field"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Add_area_field"] = processing.run(
            "qgis:fieldcalculator",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # dissolve all overlay fields so as not to repeat record in reporting
        alg_params = {
            "FIELD": [parameters["fieldtoaverage"]]
            + [field for field in parameters["additionalfields"] if field != str(parameters["fieldtoaverage"])],
            "INPUT": parameters["overlaylayer"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Dissolve"] = processing.run(
            "native:dissolve",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # intersection between input and overlay layer
        # delete no field in input layer and all fields in overlay layer
        # except field to average and additional fields
        alg_params = {
            "INPUT": outputs["Add_area_field"]["OUTPUT"],
            "INPUT_FIELDS": [""],
            "OVERLAY": outputs["Dissolve"]["OUTPUT"],
            "OVERLAY_FIELDS": [str(parameters["fieldtoaverage"])] + parameters["additionalfields"],
            "OVERLAY_FIELDS_PREFIX": "",
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Intersection"] = processing.run(
            "native:intersection",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # add_Weight
        alg_params = {
            "FIELD_LENGTH": 0,
            "FIELD_NAME": parameters["fieldtoaverage"] + "_area",
            "FIELD_PRECISION": 0,
            "FIELD_TYPE": 0,
            "FORMULA": ' "' + parameters["fieldtoaverage"] + '"  *  area($geometry)',
            "INPUT": outputs["Intersection"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Add_Weight"] = processing.run(
            "qgis:fieldcalculator",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # area_average
        weighted_field = "weighted_" + parameters["fieldtoaverage"]
        alg_params = {
            "FIELD_LENGTH": 0,
            "FIELD_NAME": weighted_field,
            "FIELD_PRECISION": 0,
            "FIELD_TYPE": 0,
            "FORMULA": ' sum("' + parameters["fieldtoaverage"] + "_area" '","input_feat_id")/"area_awa"',
            "INPUT": outputs["Add_Weight"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["area_average"] = processing.run(
            "qgis:fieldcalculator",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # remerge input layer elements
        alg_params = {
            "FIELD": ["input_feat_id"],
            "INPUT": outputs["area_average"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Dissolve2"] = processing.run(
            "native:dissolve",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(7)
        if feedback.isCanceled():
            return {}

        input_layer = self.parameterAsVectorLayer(parameters, "inputlayer", context)
        result_name = input_layer.name() + "_" + parameters["fieldtoaverage"]
        parameters["result"].destinationName = result_name

        # drop field(s) for Result
        alg_params = {
            "COLUMN": ["input_feat_id", "area_awa"]
            + [parameters["fieldtoaverage"]]
            + [field for field in parameters["additionalfields"] if field != str(parameters["fieldtoaverage"])]
            + [parameters["fieldtoaverage"] + "_area"],
            "INPUT": outputs["Dissolve2"]["OUTPUT"],
            "OUTPUT": parameters["result"],
        }
        outputs["Drop1"] = processing.run(
            "qgis:deletecolumn",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(8)
        if feedback.isCanceled():
            return {}

        results["result"] = outputs["Drop1"]["OUTPUT"]

        # Reporting

        # Drop field(s) for Report
        int_layer = context.takeResultLayer(outputs["area_average"]["OUTPUT"])
        all_fields = [f.name() for f in int_layer.fields()]
        fields_to_keep = (
            ["input_feat_id", weighted_field]
            + [field for field in parameters["additionalfields"] if field != str(parameters["fieldtoaverage"])]
            + [parameters["fieldtoaverage"]]
            + [parameters["identifierfieldforreport"]]
        )
        fields_to_drop = [f for f in all_fields if f not in fields_to_keep]
        alg_params = {
            "COLUMN": fields_to_drop,
            "INPUT": int_layer,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Drop2"] = processing.run(
            "qgis:deletecolumn",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(9)
        if feedback.isCanceled():
            return {}

        # update area
        alg_params = {
            "FIELD_LENGTH": 20,
            "FIELD_NAME": "area_crs_units",
            "FIELD_PRECISION": 5,
            "FIELD_TYPE": 0,
            "FORMULA": "round(area($geometry),5)",
            "INPUT": outputs["Drop2"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["update_area"] = processing.run(
            "qgis:fieldcalculator",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(10)
        if feedback.isCanceled():
            return {}

        parameters["reportaslayer"].destinationName = "Report as Layer"
        # add area %
        alg_params = {
            "FIELD_LENGTH": 9,
            "FIELD_NAME": "area_prcnt",
            "FIELD_PRECISION": 5,
            "FIELD_TYPE": 0,
            "FORMULA": ' round("area_crs_units" *100/  sum(  "area_crs_units" ,  "input_feat_id" ),5)',
            "INPUT": outputs["update_area"]["OUTPUT"],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["area_prcnt"] = processing.run(
            "qgis:fieldcalculator",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(11)
        if feedback.isCanceled():
            return {}

        # Order by expression
        alg_params = {
            "ASCENDING": True,
            "EXPRESSION": ' "input_feat_id" + area_prcnt" ',
            "INPUT": outputs["area_prcnt"]["OUTPUT"],
            "NULLS_FIRST": False,
            "OUTPUT": parameters["reportaslayer"],
        }
        outputs["OrderByExpression"] = processing.run(
            "native:orderbyexpression",
            alg_params,
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

        feedback.setCurrentStep(12)
        if feedback.isCanceled():
            return {}

        results["reportaslayer"] = outputs["OrderByExpression"]["OUTPUT"]

        output_file = self.parameterAsFileOutput(parameters, "reportasHTML", context)

        # create HTML report
        if output_file:
            try:
                try:
                    import pandas as pd
                except ImportError:
                    feedback.pushInfo("Python library pandas was not found. Installing pandas to QGIS python ...")
                    import pathlib as pl
                    import subprocess

                    qgis_Path = pl.Path(sys.executable)
                    qgis_python_path = (qgis_Path.parent / "python3.exe").as_posix()

                    subprocess.check_call([qgis_python_path, "-m", "pip", "install", "--user", "pandas"])
                    import pandas as pd

                    feedback.pushInfo("Python library pandas was successfully installed for QGIS python")
            except:
                feedback.reportError(
                    "Failed to import pandas. Tried installing pandas but failed.\nPlease manually install pandas for the python that comes with your QGIS.",
                    True,
                )
                return results

            # Drop geometries
            alg_params = {
                "INPUT": outputs["area_prcnt"]["OUTPUT"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            }
            outputs["DropGeometries"] = processing.run(
                "native:dropgeometries",
                alg_params,
                context=context,
                feedback=feedback,
                is_child_algorithm=True,
            )

            feedback.setCurrentStep(13)
            if feedback.isCanceled():
                return {}

            with tempfile.TemporaryDirectory() as td:
                f_name = os.path.join(td, "report_df.csv")

                report_layer = context.takeResultLayer(outputs["DropGeometries"]["OUTPUT"])

                QgsVectorFileWriter.writeAsVectorFormat(
                    report_layer,
                    f_name,
                    fileEncoding="utf-8",
                    driverName="CSV",
                )

                df = pd.read_csv(f_name)

            total_FIDs = df["input_feat_id"].max()
            ident_name = parameters["identifierfieldforreport"]
            html = ""
            df.sort_values(by="area_prcnt", ascending=False, inplace=True)
            pd.set_option("display.float_format", "{:.5f}".format)

            for i in range(1, total_FIDs + 1):
                df_sub = df.loc[df["input_feat_id"] == i]
                df_sub.reset_index(inplace=True, drop=True)
                avg_value = df_sub.at[0, weighted_field]
                if ident_name:
                    feature_name = df_sub.at[0, ident_name]
                    df_sub.drop(
                        columns=["input_feat_id", ident_name, weighted_field],
                        inplace=True,
                    )
                    html += f"<p><b>{i}. {feature_name}</b><br>{weighted_field}: {avg_value}<br>count of distinct intersecting features: {len(df_sub.index)}<br></p>\n"
                else:
                    df_sub.drop(columns=["input_feat_id", weighted_field], inplace=True)
                    html += f"<p><b>Feature ID: {i}</b><br>{weighted_field}: {avg_value}<br>count of distinct intersecting features: {len(df_sub.index)}<br></p>\n"
                html += f"{df_sub.to_html(bold_rows=False, index=False, na_rep='Null',justify='left')}<br>\n"

                with codecs.open(output_file, "w", encoding="utf-8") as f:
                    f.write("<html><head>\n")
                    f.write(
                        '<meta http-equiv="Content-Type" content="text/html; \
                            charset=utf-8" /></head><body>\n'
                    )
                    f.write(html)
                    f.write("</body></html>\n")

                results["reportasHTML"] = output_file

        return results

    def name(self):
        """
        Returns the algorithm name, used for identifying the algorithm. This
        string should be fixed for the algorithm, and must not be localised.
        The name should be unique within each provider. Names should contain
        lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return "Area Weighted Average"

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
        return self.tr(self.name())

    def group(self):
        """
        Returns the name of the group this algorithm belongs to. This string
        should be localised.
        """
        return self.tr(self.groupId())

    def groupId(self):
        """
        Returns the unique ID of the group this algorithm belongs to. This
        string should be fixed for the algorithm, and must not be localised.
        The group id should be unique within each provider. Group id should
        contain lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return ""

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return AreaWeightedAverageAlgorithm()

    def icon(self):
        cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]
        icon = QIcon(os.path.join(os.path.join(os.path.dirname(cmd_folder), "icon.png")))
        return icon

    def shortHelpString(self):
        msg = ""
        try:
            msg = getAndUpdateMessage()
        except Exception as e:
            print(e)

        return (
            msg
            + f"""<html><body><h3><a "href"="https://github.com/ar-siddiqui/area_weighted_average/wiki/Tutorials">Video Tutorials</a></h3>
<h2>Algorithm Description</h2>
<p>This algorithm calculates attribute value by performing spatial area weighted average analysis on an input polygon layer given an attribute in the overlay polygon layer. Each feature in the input layer will be assigned a spatial area weighted average value of the overlay field. A report of the analysis is generated as a GIS Layer and as HTML.</p>
<h2>Input Parameters</h2>
<h3>Input Layer</h3>
<p>Polygon layer for which area weighted average will be calculated.</p>
<h3>Overlay Layer</h3>
<p>Polygon layer with source data. Must overlap the Input Layer.</p>
<h3>Field to Average</h3>
<p>Single numeric field in the Overlay Layer.</p>
<h3>Identifier Field for Report [optional]</h3>
<p>Name or ID field in the Input Layer. This field will be used to identify features in the report.</p>
<h3>Additional Fields to Keep for Report [optional]</h3>
<p>Fields in the Overlay Layer that will be included in the reports.</p>
<h2>Outputs</h2>
<h3>Result</h3>
<p>Input layer but with the additional attribute of field to average.</p>
<h3>Report as Layer</h3>
<p>Report of the analysis as a GIS layer.</p>
<h3>Report as HTML [optional]</h3>
<p>Report of the analysis as text tables.</p>
<p align="right">Algorithm author: Abdul Raheem Siddiqui</p>
<p align="right">Help author: Abdul Raheem Siddiqui</p>
<p align="right">Algorithm version: {PLUGIN_VERSION}</p>
<p align="right">Contact email: ars.work.ce@gmail.com</p>
<p>** If the python library pandas is not installed on the QGIS installation of python; this algorithm will try to install pandas library to the QGIS installation of python.</p>
</body></html>"""
        )

    def helpUrl(self):
        return "mailto:ars.work.ce@gmail.com"

    def postProcessAlgorithm(self, context, feedback):
        try:  # try-except because trivial features
            counter = incrementUsageCounter()

            # check if counter is milestone for plugin version check
            if (counter) % 4 == 0:
                checkPluginUptodate("Area Weighted Average")

            # check if counter is milestone for usage message
            if (counter) % 25 == 0:
                displayUsageMessage(counter)

            # check if plugin is registered
            if not getRegistrationStatus():
                form = RegisterForm("Register Area Weighted Average", REGISTRATION_FORM_LINK, REGISTRATION_FORM_ENRIES)
                form.show()

        except Exception as e:
            feedback.reportError(
                f"Algorithm finished successfully but post processing failed. {e}",
                False,
            )

        return {}

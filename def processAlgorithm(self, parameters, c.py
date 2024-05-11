def processAlgorithm(self, parameters, context, model_feedback):
    #...
    input_layer = self.parameterAsVectorLayer(parameters, "inputlayer", context)
    overlay_layer = self.parameterAsVectorLayer(parameters, "overlaylayer", context)

    input_epsg_code = input_layer.crs().authid()
    overlay_epsg_code = overlay_layer.crs().authid()

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
        "INPUT": parameters["inputlayer"], ## parameter["result"]
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
        + [field for field in parameters["additionalfields"] if field!= str(parameters["fieldtoaverage"])],
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
    weighted
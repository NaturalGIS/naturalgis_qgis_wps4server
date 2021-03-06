# -*- coding: utf-8 -*-

"""
***************************************************************************
    QGIS Server Plugin Filters: add OGC Web Processing Service capabilities
    ---------------------
    Date                 : August 2015
    copyright            : (C) 2015 by DHONT René-Luc - 3Liz
    email                : rldhont@3liz.com
***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************
"""

from qgis.server import *
from qgis.core import *

from PyQt4.QtCore import *

import os, types
import sys
import re
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(
    os.path.realpath(__file__)), 'PyWPS'))
import pywps
from pywps import config as pywpsConfig
from pywps.Exceptions import *
from xml.dom import minidom
from xml.sax.saxutils import escape

from processing.core.Processing import Processing
from processing.core.ProcessingConfig import ProcessingConfig, Setting
from processing.core.parameters import *
from processing.tools.general import *
# Note: SilentProgress used to be GUI
try:
    from processing.core.SilentProgress import SilentProgress
except:
    from processing.gui.SilentProgress import SilentProgress


def get_processing_algs():
    """Return a list of algs, abstracting from the processing version"""
    try:
        return Processing.algs()
    except TypeError:
        return Processing.algs


def get_qgis_server_address(serverInterface):
    """Return the QGIS base server address"""
    qgisaddress = pywpsConfig.getConfigValue("qgis", "qgisserveraddress")
    if qgisaddress:
        return qgisaddress

    server_port = serverInterface.getEnv('SERVER_PORT')
    qgisaddress = serverInterface.getEnv('SERVER_NAME')
    wpsaddress += ":%s" % server_port if server_port and server_port != '80' else ''
    qgisaddress += serverInterface.getEnv('SCRIPT_NAME')
    if serverInterface.getEnv('HTTPS'):
        qgisaddress = 'https://' + qgisaddress
    else:
        qgisaddress = 'http://' + qgisaddress
    return qgisaddress

def get_wps_server_address(serverInterface, params):
    wpsaddress = pywpsConfig.getConfigValue("wps", "serveraddress")
    if wpsaddress:
        if wpsaddress.count('?') == 0:
            wpsaddress += '?'
        elif wpsaddress[-1] != '&':
            wpsaddress += '&'
        return wpsaddress

    server_port = serverInterface.getEnv('SERVER_PORT')
    wpsaddress = serverInterface.getEnv('SERVER_NAME')
    wpsaddress += ":%s" % server_port if server_port and server_port != '80' else ''
    wpsaddress += serverInterface.getEnv('SCRIPT_NAME')
    if serverInterface.getEnv('HTTPS'):
        wpsaddress = 'https://' + wpsaddress
    else:
        wpsaddress = 'http://' + wpsaddress
    wpsaddress += '?'
    mapParam = ''
    if 'map' in params:
        mapParam = params['map']
    elif 'MAP' in params:
        mapParam = params['MAP']

    if mapParam and os.environ.has_key("QGIS_PROJECT_FILE") and mapParam != os.environ["QGIS_PROJECT_FILE"]:
        wpsaddress += 'MAP=' + mapParam + '&'

    if 'config' in params:
        wpsaddress += 'config=' + params['config'] + '&'
    elif 'CONFIG' in params:
        wpsaddress += 'CONFIG=' + params['CONFIG'] + '&'

    return wpsaddress


class QGISProgress(SilentProgress):

    def __init__(self, algname=None):
        self.msg = []

    def error(self, msg):
        self.msg.append(msg)


def QGISProcessFactory(alg_name, project='', vectors=[], rasters=[], crss=[], wpsserver=''):
    """This is the bridge between SEXTANTE and PyWPS:
    it creates PyWPS processes based on SEXTANTE alg name"""
    from pywps.Process import WPSProcess
    from new import classobj
    import types

    # Sanitize name
    class_name = alg_name.replace(':', '_')
    alg = Processing.getAlgorithm(alg_name)

    algDesc = alg.shortHelp()
    if not algDesc:
        algDesc = ''
    else:
        algDesc = re.sub(r'[^\x00-\x7F]', '@', algDesc)
        algDesc = algDesc.replace('<p></p>', '')
        algDesc = '<![CDATA[' + algDesc + ']]>'

    # layer inputs
    rasterLayers = rasters
    vectorLayers = vectors

    def process_init(self):
        # Automatically init the process attributes
        WPSProcess.__init__(self,
                            identifier=alg_name,  # must be same, as filename
                            title=escape(alg.name).replace('\\', ''),
                            version="0.1",
                            storeSupported="true",
                            statusSupported="true",
                            abstract=algDesc,
                            grassLocation=False)
        self.alg = alg

        # Test parameters
        if not len(self.alg.parameters):
            self.alg.defineCharacteristics()

        # Get parameters description
        algParamDescs = alg.getParameterDescriptions()

        # Add I/O
        i = 1
        for parm in alg.parameters:
            minOccurs = 1
            if getattr(parm, 'optional', False):
                minOccurs = 0

            parmDesc = ''
            if algParamDescs and parm.name in algParamDescs:
                parmDesc = algParamDescs[parm.name]
                parmDesc = '<![CDATA[' + parmDesc + ']]>'
            # TODO: create "LiteralValue", "ComplexValue" or "BoundingBoxValue"
            # this can be done checking the class:
            # parm.__class__, one of
            # ['Parameter', 'ParameterBoolean', 'ParameterCrs', 'ParameterDataObject', 'ParameterExtent', 'ParameterFile', 'ParameterFixedTable', 'ParameterMultipleInput', 'ParameterNumber', 'ParameterRange', 'ParameterRaster', 'ParameterSelection', 'ParameterString', 'ParameterTable','ParameterTableField', 'ParameterVector']
            if parm.__class__.__name__ == 'ParameterVector':
                values = []
                schema = wpsserver
                schema += 'SERVICE=WPS&REQUEST=GetSchema&VERSION=1.0.0&OUTPUTFORMAT=XMLSCHEMA'
                if vectorLayers and ParameterVector.VECTOR_TYPE_ANY in parm.shapetype:
                    values = [l['name'] for l in vectorLayers]
                elif vectorLayers:
                    if ParameterVector.VECTOR_TYPE_POINT in parm.shapetype:
                        values += [l['name']
                                   for l in vectorLayers if l['geometry'] == 'Point']
                    if ParameterVector.VECTOR_TYPE_LINE in parm.shapetype:
                        values += [l['name']
                                   for l in vectorLayers if l['geometry'] == 'Line']
                    if ParameterVector.VECTOR_TYPE_POLYGON in parm.shapetype:
                        values += [l['name']
                                   for l in vectorLayers if l['geometry'] == 'Polygon']
                else:
                    if ParameterVector.VECTOR_TYPE_POINT in parm.shapetype:
                        schema += '&GEOMETRYNAME=Point'
                    if ParameterVector.VECTOR_TYPE_LINE in parm.shapetype:
                        schema += '&GEOMETRYNAME=Line'
                    if ParameterVector.VECTOR_TYPE_POLYGON in parm.shapetype:
                        schema += '&GEOMETRYNAME=Polygon'
                if values:
                    self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                                       escape(parm.description).replace(
                                                                           '\\', ''),
                                                                       parmDesc,
                                                                       minOccurs=minOccurs,
                                                                       type=types.StringType)
                    self._inputs['Input%s' % i].values = values
                else:
                    self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name),
                                                    escape(parm.description).replace('\\',''),
                                                    parmDesc,
                                                    minOccurs=minOccurs,
                                                    formats = [{
                        'mimeType':'text/xml',
                        'encoding': 'utf-8',
                        'schema': escape(schema)
                    },{
                        'mimeType':'text/xml; subtype=gml/2.1.2',
                        'encoding': 'utf-8',
                        'schema': escape(schema)
                    },{
                        'mimeType':'text/xml; subtype=gml/3.1.1',
                        'encoding': 'utf-8'
                    },{
                        'mimeType':'application/gml+xml',
                        'encoding': 'utf-8',
                        'schema': escape(schema)
                    },{
                        'mimeType':'application/gml+xml; version=2.1.2',
                        'encoding': 'utf-8',
                        'schema': escape(schema)
                    },{
                        'mimeType':'application/gml+xml; version=3.1.1',
                        'encoding': 'utf-8'
                    }])

            elif parm.__class__.__name__ == 'ParameterRaster':
                if rasterLayers:
                    self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                                       escape(parm.description).replace(
                                                                           '\\', ''),
                                                                       parmDesc,
                                                                       minOccurs=minOccurs,
                                                                       type=types.StringType)
                    self._inputs['Input%s' % i].values = [l['name']
                                                          for l in rasterLayers]
                else:
                    self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name),
                                                    escape(parm.description).replace('\\',''),
                                                    parmDesc,
                                                    minOccurs=minOccurs,
                                                    formats = [{'mimeType':'image/tiff'}])

            elif parm.__class__.__name__ == 'ParameterMultipleInput':
                if parm.datatype == ParameterMultipleInput.TYPE_VECTOR_ANY or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POINT or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_LINE or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POLYGON:
                    if vectorLayers :
                        self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                        escape(parm.description).replace('\\',''),
                                                        parmDesc,
                                                        minOccurs=minOccurs,
                                                        maxOccurs=len(vectorLayers),
                                                        type=types.StringType)
                        if parm.datatype == ParameterMultipleInput.TYPE_VECTOR_ANY:
                            self._inputs['Input%s' % i].values = [l['name'] for l in vectorLayers]
                        elif parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POINT:
                            self._inputs['Input%s' % i].values = [l['name'] for l in vectorLayers if l['geometry'] == 'Point']
                        elif parm.datatype == ParameterMultipleInput.TYPE_VECTOR_LINE:
                            self._inputs['Input%s' % i].values = [l['name'] for l in vectorLayers if l['geometry'] == 'Line']
                        elif parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POLYGON:
                            self._inputs['Input%s' % i].values = [l['name'] for l in vectorLayers if l['geometry'] == 'Polygon']
                    else :
                        self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name),
                                                        escape(parm.description).replace('\\',''),
                                                        parmDesc,
                                                        minOccurs=minOccurs,
                                                        maxOccurs=10,
                                                        formats = [{
                            'mimeType':'text/xml',
                            'encoding': 'utf-8'
                        },{
                            'mimeType':'text/xml; subtype=gml/2.1.2',
                            'encoding': 'utf-8'
                        },{
                            'mimeType':'text/xml; subtype=gml/3.1.1',
                            'encoding': 'utf-8'
                        },{
                            'mimeType':'application/gml+xml',
                            'encoding': 'utf-8'
                        },{
                            'mimeType':'application/gml+xml; version=2.1.2',
                            'encoding': 'utf-8'
                        },{
                            'mimeType':'application/gml+xml; version=3.1.1',
                            'encoding': 'utf-8'
                        }])

                elif parm.datatype == ParameterMultipleInput.TYPE_RASTER :
                    if rasterLayers :
                        self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                        escape(parm.description).replace('\\',''),
                                                        parmDesc,
                                                        minOccurs=minOccurs,
                                                        maxOccurs=len(rasterLayers),
                                                        type=types.StringType)
                        self._inputs['Input%s' % i].values = [l['name'] for l in rasterLayers]
                    else :
                        self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name),
                                                        escape(parm.description).replace('\\',''),
                                                        parmDesc,
                                                        minOccurs=minOccurs,
                                                        maxOccurs=10,
                                                        formats = [{'mimeType':'image/tiff'}])

            elif parm.__class__.__name__ == 'ParameterTable':
                self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name),
                                                                   escape(parm.description).replace(
                                                                       '\\', ''),
                                                                   parmDesc,
                                                                   minOccurs=minOccurs, formats=[{'mimeType': 'text/csv'}])

            elif parm.__class__.__name__ == 'ParameterExtent':
                self._inputs['Input%s' % i] = self.addBBoxInput(escape(parm.name),
                                                                escape(parm.description).replace(
                                                                    '\\', ''),
                                                                parmDesc,
                                                                minOccurs=minOccurs)
                # Add supported CRSs from project or config
                if crss:
                    self._inputs['Input%s' % i].crss = crss

            elif parm.__class__.__name__ == 'ParameterSelection':
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                                   escape(parm.description).replace(
                                                                       '\\', ''),
                                                                   parmDesc,
                                                                   minOccurs=minOccurs,
                                                                   type=types.StringType,
                                                                   default=getattr(parm, 'default', None))
                self._inputs['Input%s' % i].values = parm.options

            elif parm.__class__.__name__ == 'ParameterRange':
                # parm.default can be None!!!!
                try:
                    tokens = parm.default.split(',')
                    n1 = float(tokens[0])
                    n2 = float(tokens[1])
                except AttributeError:
                    n1, n2 = 0, 0
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                                   escape(parm.description).replace(
                                                                       '\\', ''),
                                                                   parmDesc,
                                                                   minOccurs=minOccurs,
                                                                   type=types.FloatType,
                                                                   default=n1)
                self._inputs['Input%s' % i].values = ((n1, n2))

            else:
                type = types.StringType
                if parm.__class__.__name__ == 'ParameterBoolean':
                    type = types.BooleanType
                elif parm.__class__.__name__ == 'ParameterNumber':
                    type = types.FloatType
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name),
                                                                   escape(parm.description).replace(
                                                                       '\\', ''),
                                                                   parmDesc,
                                                                   minOccurs=minOccurs,
                                                                   type=type,
                                                                   default=getattr(parm, 'default', None))
                if parm.__class__.__name__ == 'ParameterBoolean':
                    self._inputs['Input%s' % i].values = (True, False)
            i += 1
        i = 1
        for parm in alg.outputs:
            if parm.hidden:
                continue
            # TODO: create "LiteralOutput", "ComplexOutput" or "BoundingBoxOutput"
            # this can be done checking the class:
            # parm.__class__, one of
            # ['Output', 'OutputDirectory', 'OutputExtent', 'OutputFile', 'OutputHtml', 'OutputNumber', 'OutputRaster', 'OutputString', 'OutputTable', 'OutputVector']
            if parm.__class__.__name__ == 'OutputVector':
                outputFormats = [{
                    'mimeType': 'text/xml',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'text/xml; subtype=gml/2.1.2',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'text/xml; subtype=gml/3.1.1',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/gml+xml',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/gml+xml; version=2.1.2',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/gml+xml; version=3.1.1',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/json',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/x-zipped-shp',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/x-zipped-tab',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/x-ogc-wms',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/x-ogc-wfs',
                    'encoding': 'utf-8'
                }]
                if pywpsConfig.config.has_option('qgis', 'outputs_mimetypes_vector'):
                    outputsMimetypes = pywpsConfig.getConfigValue(
                        'qgis', 'outputs_mimetypes_vector').strip()
                    if outputsMimetypes:
                        outputsMimetypes = outputsMimetypes.split(',')
                        outputFormats = [
                            {'mimeType': m.strip(), 'encoding': 'utf-8'} for m in outputsMimetypes]
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                      formats=outputFormats
                                                                      )
                if pywpsConfig.getConfigValue("qgis", "qgisserveraddress"):
                    self._outputs['Output%s' % i].useQgisServer = True
            elif parm.__class__.__name__ == 'OutputRaster':
                outputFormats = [{
                    'mimeType': 'image/tiff'
                }, {
                    'mimeType': 'application/x-ogc-wms',
                    'encoding': 'utf-8'
                }, {
                    'mimeType': 'application/x-ogc-wcs',
                    'encoding': 'utf-8'
                }]
                if pywpsConfig.config.has_option('qgis', 'outputs_mimetypes_raster'):
                    outputsMimetypes = pywpsConfig.getConfigValue(
                        'qgis', 'outputs_mimetypes_raster').strip()
                    if outputsMimetypes:
                        outputsMimetypes = outputsMimetypes.split(',')
                        outputFormats = [
                            {'mimeType': m.strip(), 'encoding': 'utf-8'} for m in outputsMimetypes]
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                      formats=outputFormats
                                                                      )
                if pywpsConfig.getConfigValue("qgis", "qgisserveraddress"):
                    self._outputs['Output%s' % i].useQgisServer = True
            elif parm.__class__.__name__ == 'OutputTable':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                      formats=[{'mimeType': 'text/csv'}])
            elif parm.__class__.__name__ == 'OutputHtml':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                      formats=[{'mimeType': 'text/html'}])
            elif parm.__class__.__name__ == 'OutputFile':
                if parm.ext == 'png':
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'image/png'}])
                elif parm.ext in ['jpg', 'jpeg']:
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'image/jpeg'}])
                elif parm.ext in ['pdf', 'json', 'zip']:
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'application/' + parm.ext}])
                elif parm.ext in ['txt', 'text']:
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'text/plain'}])
                elif parm.ext in ['xml', 'html', 'csv']:
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'text/' + parm.ext}])
                else:
                    self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                                                                          formats=[{'mimeType': 'application/octet-stream'}])
            elif parm.__class__.__name__ == 'OutputExtent':
                self._outputs['Output%s' % i] = self.addBBoxOutput(
                    parm.name, parm.description)
            else:
                type = types.StringType
                if parm.__class__.__name__ == 'OutputNumber':
                    type = types.FloatType
                self._outputs['Output%s' % i] = self.addLiteralOutput(parm.name, parm.description,
                                                                      type=type)
            i += 1

        for k in self._inputs:
            setattr(self, k, self._inputs[k])

        for k in self._outputs:
            setattr(self, k, self._outputs[k])

    def execute(self):
        # create a project
        p = QgsProject.instance()
        mlr = QgsMapLayerRegistry.instance()
        # Run alg with params
        # TODO: get args
        args = {}
        # get vector and raster inputs
        inputCrs = None
        for k in self._inputs:
            v = getattr(self, k)
            parm = self.alg.getParameterFromName(v.identifier)
            # vector layers
            if parm.__class__.__name__ == 'ParameterVector':
                values = []
                if vectorLayers and ParameterVector.VECTOR_TYPE_ANY in parm.shapetype:
                    values = [l for l in vectorLayers]
                elif vectorLayers:
                    if ParameterVector.VECTOR_TYPE_POINT in parm.shapetype:
                        values += [l for l in vectorLayers if l['geometry'] == 'Point']
                    if ParameterVector.VECTOR_TYPE_LINE in parm.shapetype:
                        values += [l for l in vectorLayers if l['geometry'] == 'Line']
                    if ParameterVector.VECTOR_TYPE_POLYGON in parm.shapetype:
                        values += [l for l in vectorLayers if l['geometry']
                                   == 'Polygon']
                if values:
                    layerName = v.getValue()
                    values = [l for l in values if l['name'] == layerName]
                    l = values[0]
                    layer = QgsVectorLayer(
                        l['datasource'], l['name'], l['provider'])
                    crs = l['crs']
                    qgsCrs = None
                    if str(crs).startswith('USER:'):
                        qgsCrs = QgsCoordinateReferenceSystem()
                        qgsCrs.createFromProj4(str(l['proj4']))
                    else:
                        qgsCrs = QgsCoordinateReferenceSystem(str(crs))
                    if qgsCrs:
                        layer.setCrs(qgsCrs)
                    mlr.addMapLayer(layer, False)
                    args[v.identifier] = layer
                    inputCrs = layer.crs()
                else:
                    fileName = v.getValue()
                    fileInfo = QFileInfo(fileName)
                    # move fileName to fileName.gml for ogr
                    with open(fileName, 'r') as f:
                        o = open(fileName + '.gml', 'w')
                        o.write(f.read())
                        o.close()
                    #import shutil
                    #shutil.copy2(fileName+'.gml', '/tmp/test.gml' )
                    # get layer
                    layer = QgsVectorLayer(
                        fileName + '.gml', fileInfo.baseName(), 'ogr')
                    pr = layer.dataProvider()
                    e = layer.extent()
                    mlr.addMapLayer(layer, False)
                    args[v.identifier] = layer
                    inputCrs = layer.crs()
            # raster layers
            elif parm.__class__.__name__ == 'ParameterRaster':
                if rasterLayers:
                    layerName = v.getValue()
                    values = [l for l in rasterLayers if l[
                        'name'] == layerName]
                    l = values[0]
                    layer = QgsRasterLayer(
                        l['datasource'], l['name'], l['provider'])
                    crs = l['crs']
                    qgsCrs = None
                    if str(crs).startswith('USER:'):
                        qgsCrs = QgsCoordinateReferenceSystem()
                        qgsCrs.createFromProj4(str(l['proj4']))
                    else:
                        qgsCrs = QgsCoordinateReferenceSystem(str(crs))
                    if qgsCrs:
                        layer.setCrs(qgsCrs)
                    mlr.addMapLayer(layer, False)
                    args[v.identifier] = layer
                    inputCrs = layer.crs()
                else:
                    fileName = v.getValue()
                    fileInfo = QFileInfo(fileName)
                    layer = QgsRasterLayer(
                        fileName, fileInfo.baseName(), 'gdal')
                    mlr.addMapLayer(layer, False)
                    args[v.identifier] = layer
                    inputCrs = layer.crs()

            elif parm.__class__.__name__ == 'ParameterMultipleInput':
                args[v.identifier] = []
                fileNames = v.getValue()
                if fileNames and not isinstance(fileNames, list):
                    fileNames = [fileNames]
                if not fileNames:
                    fileNames = []

                if parm.datatype == ParameterMultipleInput.TYPE_VECTOR_ANY or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POINT or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_LINE or \
                   parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POLYGON:
                    if vectorLayers :
                        values = [l for l in vectorLayers if l['name'] in fileNames]
                        if parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POINT:
                            values = [l for l in values if l['geometry'] == 'Point']
                        elif parm.datatype == ParameterMultipleInput.TYPE_VECTOR_LINE:
                            values = [l for l in values if l['geometry'] == 'Line']
                        elif parm.datatype == ParameterMultipleInput.TYPE_VECTOR_POLYGON:
                            values = [l for l in values if l['geometry'] == 'Polygon']
                        for l in values:
                            layer = QgsVectorLayer( l['datasource'], l['name'], l['provider'] )
                            crs = l['crs']
                            qgsCrs = None
                            if str(crs).startswith('USER:') :
                                qgsCrs = QgsCoordinateReferenceSystem()
                                qgsCrs.createFromProj4( str(l['proj4']) )
                            else :
                                qgsCrs = QgsCoordinateReferenceSystem( str(crs) )
                            if qgsCrs :
                                layer.setCrs( qgsCrs )
                            mlr.addMapLayer( layer, False )
                            args[v.identifier].append(layer)
                    else :
                        for fileName in fileNames:
                            fileInfo = QFileInfo( fileName )
                            # move fileName to fileName.gml for ogr
                            with open( fileName, 'r' ) as f :
                                o = open( fileName+'.gml', 'w' )
                                o.write( f.read() )
                                o.close()
                            #import shutil
                            #shutil.copy2(fileName+'.gml', '/tmp/test.gml' )
                            # get layer
                            layer = QgsVectorLayer( fileName+'.gml', fileInfo.baseName(), 'ogr' )
                            pr = layer.dataProvider()
                            e = layer.extent()
                            mlr.addMapLayer( layer, False )
                            args[v.identifier].append(layer)

                if parm.datatype == ParameterMultipleInput.TYPE_RASTER :
                    if rasterLayers :
                        values = [l for l in rasterLayers if l['name'] in fileNames]
                        for l in values:
                            layer = QgsRasterLayer( l['datasource'], l['name'], l['provider'] )
                            crs = l['crs']
                            qgsCrs = None
                            if str(crs).startswith('USER:') :
                                qgsCrs = QgsCoordinateReferenceSystem()
                                qgsCrs.createFromProj4( str(l['proj4']) )
                            else :
                                qgsCrs = QgsCoordinateReferenceSystem( str(crs) )
                            if qgsCrs :
                                layer.setCrs( qgsCrs )
                            mlr.addMapLayer( layer, False )
                            args[v.identifier].append(layer)
                    else :
                        for fileName in fileNames:
                            fileInfo = QFileInfo( fileName )
                            layer = QgsRasterLayer( fileName, fileInfo.baseName(), 'gdal' )
                            mlr.addMapLayer( layer, False )
                            args[v.identifier].append(layer)

            elif parm.__class__.__name__ == 'ParameterExtent':
                if v.getValue():
                    coords = v.getValue().coords
                    args[v.identifier] = str(coords[0][
                                             0]) + ',' + str(coords[1][0]) + ',' + str(coords[0][1]) + ',' + str(coords[1][1])
                else:
                    args[v.identifier] = None
            elif parm.__class__.__name__ == 'ParameterSelection':
                s = v.getValue()
                if s:
                    args[v.identifier] = parm.options.index(s)
                else:
                    args[v.identifier] = None
            else:
                args[v.identifier] = v.getValue()

        # if extent in inputs, transform it to the alg CRS
        if inputCrs:
            for k in self._inputs:
                v = getattr(self, k)
                parm = self.alg.getParameterFromName(v.identifier)
                if parm.__class__.__name__ == 'ParameterExtent':
                    extent = v.getValue()
                    if extent:
                        coords = extent.coords
                        coordCrs = extent.crs
                        if coordCrs:
                            coordCrs = QgsCoordinateReferenceSystem(
                                str(coordCrs))
                        elif crss:
                            coordCrs = QgsCoordinateReferenceSystem(
                                str(crss[0]))
                        else:
                            coordCrs = QgsCoordinateReferenceSystem(
                                'EPSG:4326')
                        if coordCrs:
                            coordExtent = QgsRectangle(coords[0][0], coords[0][
                                                       1], coords[1][0], coords[1][1])
                            xform = QgsCoordinateTransform(coordCrs, inputCrs)
                            coordExtent = xform.transformBoundingBox(
                                coordExtent)
                            args[v.identifier] = str(coordExtent.xMinimum()) + ',' + str(coordExtent.xMaximum(
                            )) + ',' + str(coordExtent.yMinimum()) + ',' + str(coordExtent.yMaximum())

        # Adds None for output parameter(s)
        for k in self._outputs:
            v = getattr(self, k)
            args[v.identifier] = None

        if not len(self.alg.parameters):
            self.alg.defineCharacteristics()

        progress = QGISProgress()
        tAlg = Processing.runAlgorithm(self.alg, None, args, progress=progress)
        # if runalg failed return exception message
        if not tAlg:
            return 'Error in processing'
        if len(progress.msg):
            return ', '.join(progress.msg)
        # clear map layer registry
        mlr.removeAllMapLayers()
        # get result
        result = tAlg.getOutputValuesAsDictionary()
        for k in self._outputs:
            v = getattr(self, k)
            parm = self.alg.getOutputFromName(v.identifier)

            # Output Vector
            if parm.__class__.__name__ == 'OutputVector':
                outputName = result.get(v.identifier, None)
                if not outputName:
                    return 'No output file'
                # get output file info
                outputInfo = QFileInfo(outputName)
                # get the output QGIS vector layer
                outputLayer = QgsVectorLayer(
                    outputName, outputInfo.baseName(), 'ogr')
                # Update input CRS
                if inputCrs and not inputCrs.authid():
                    inputCrs.saveAsUserCRS('')
                    crs = QgsCoordinateReferenceSystem()
                    crs.createFromProj4(inputCrs.toProj4())
                    inputCrs = crs
                # Update CRS
                if inputCrs and not outputLayer.dataProvider().crs().authid():
                    outputLayer.setCrs(inputCrs)
                # define destination CRS
                destCrs = None
                if outputLayer.crs().authid().startswith('USER:'):
                    if crss:
                        destCrs = QgsCoordinateReferenceSystem(str(crss[0]))
                        v.projection = str(crss[0])
                    else:
                        destCrs = QgsCoordinateReferenceSystem('EPSG:4326')
                        v.projection = 'EPSG:4326'
                # define the file extension
                outputExt = 'gml'
                if v.format['mimetype'] == 'application/json':
                    outputExt = 'geojson'
                elif v.format['mimetype'] == 'application/x-zipped-shp':
                    outputExt = 'shp'
                elif v.format['mimetype'] == 'application/x-zipped-tab':
                    outputExt = 'tab'
                # define the output file path
                outputFile = os.path.join(outputInfo.absolutePath(
                ), outputInfo.baseName() + '.' + outputExt)
                # write the output GML file
                if v.format['mimetype'] == 'application/x-zipped-shp':
                    if destCrs:
                        outputFile = os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '_' + str(destCrs.srsid()) + '.' + outputExt)
                        outputInfo = QFileInfo(outputFile)
                        error = QgsVectorFileWriter.writeAsVectorFormat(
                            outputLayer, outputFile, 'utf-8', destCrs, 'ESRI Shapefile', False, None)
                    # compress files
                    import zipfile
                    try:
                        import zlib
                        compression = zipfile.ZIP_DEFLATED
                    except:
                        compression = zipfile.ZIP_STORED
                    zFile = os.path.join(
                        outputInfo.absolutePath(), outputInfo.baseName() + '.zip')
                    with zipfile.ZipFile(zFile, 'w') as zf:
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.shp'), compress_type=compression, arcname=outputInfo.baseName() + '.shp')
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.shx'), compress_type=compression, arcname=outputInfo.baseName() + '.shx')
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.dbf'), compress_type=compression, arcname=outputInfo.baseName() + '.dbf')
                        if os.path.exists(os.path.join(outputInfo.absolutePath(), outputInfo.baseName() + '.prj')):
                            zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                            ) + '.prj'), compress_type=compression, arcname=outputInfo.baseName() + '.prj')
                        zf.close()
                    outputFile = zFile
                elif v.format['mimetype'] == 'application/x-zipped-tab':
                    error = QgsVectorFileWriter.writeAsVectorFormat(
                        outputLayer, outputFile, 'utf-8', destCrs, 'Mapinfo File', False, None)
                    # compress files
                    import zipfile
                    try:
                        import zlib
                        compression = zipfile.ZIP_DEFLATED
                    except:
                        compression = zipfile.ZIP_STORED
                    zFile = os.path.join(
                        outputInfo.absolutePath(), outputInfo.baseName() + '.zip')
                    with zipfile.ZipFile(zFile, 'w') as zf:
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.tab'), compress_type=compression, arcname=outputInfo.baseName() + '.tab')
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.dat'), compress_type=compression, arcname=outputInfo.baseName() + '.dat')
                        zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                        ) + '.map'), compress_type=compression, arcname=outputInfo.baseName() + '.map')
                        if os.path.exists(os.path.join(outputInfo.absolutePath(), outputInfo.baseName() + '.id')):
                            zf.write(os.path.join(outputInfo.absolutePath(), outputInfo.baseName(
                            ) + '.id'), compress_type=compression, arcname=outputInfo.baseName() + '.id')
                        zf.close()
                    outputFile = zFile
                elif v.format['mimetype'] == 'application/json':
                    error = QgsVectorFileWriter.writeAsVectorFormat(
                        outputLayer, outputFile, 'utf-8', destCrs, 'GeoJSON', False, None)
                elif v.format['mimetype'] in ('text/xml; subtype=gml/3.1.1', 'application/gml+xml; version=3.1.1'):
                    error = QgsVectorFileWriter.writeAsVectorFormat(outputLayer, outputFile, 'utf-8', destCrs, 'GML', False, None, [
                                                                    'XSISCHEMAURI=http://schemas.opengis.net/gml/3.1.1/base/feature.xsd', 'FORMAT=GML3'])
                else:
                    error = QgsVectorFileWriter.writeAsVectorFormat(outputLayer, outputFile, 'utf-8', destCrs, 'GML', False, None, [
                                                                    'XSISCHEMAURI=http://schemas.opengis.net/gml/2.1.2/feature.xsd'])
                args[v.identifier] = outputFile

                # get OWS getCapabilities URL
                if not v.asReference and v.format['mimetype'] in ('application/x-ogc-wms', 'application/x-ogc-wfs'):
                    from pywps.Wps.Execute import QGIS
                    qgis = QGIS.QGIS(self, self.pywps.UUID)
                    v.setValue(outputFile)
                    outputFile = qgis.getReference(v)
                    args[v.identifier] = outputFile

            # Output Raster
            elif parm.__class__.__name__ == 'OutputRaster':
                outputName = result.get(v.identifier, None)
                if not outputName:
                    return 'No output file'
                # get output file info
                outputInfo = QFileInfo(outputName)
                # get the output QGIS vector layer
                outputLayer = QgsRasterLayer(
                    outputName, outputInfo.baseName(), 'gdal')
                # Update input CRS
                if inputCrs and not inputCrs.authid():
                    inputCrs.saveAsUserCRS('')
                    crs = QgsCoordinateReferenceSystem()
                    crs.createFromProj4(inputCrs.toProj4())
                    inputCrs = crs
                # Update CRS
                if inputCrs and not outputLayer.dataProvider().crs().authid():
                    outputLayer.setCrs(inputCrs)
                    v.projection = 'proj4:' + inputCrs.toProj4()
                args[v.identifier] = outputName

                # get OWS getCapabilities URL
                if not v.asReference and v.format['mimetype'] in ('application/x-ogc-wms', 'application/x-ogc-wcs'):
                    from pywps.Wps.Execute import QGIS
                    qgis = QGIS.QGIS(self, self.pywps.UUID)
                    v.setValue(outputName)
                    outputFile = qgis.getReference(v)
                    args[v.identifier] = outputFile
            else:
                args[v.identifier] = result.get(v.identifier, None)
        for k in self._outputs:
            v = getattr(self, k)
            v.setValue(args[v.identifier])
        return

    try:
        new_class = classobj(str('%sProcess' % class_name), (WPSProcess, ), {
            '__init__':  process_init,
            'execute': execute,
            'params': [],
            'alg': alg,
            '_inputs': {},
            '_outputs': {}
        })
        return new_class
    except TypeError, e:
        QgsMessageLog.logMessage("QGISProcessFactory " + e.__str__())
        return None


class wpsFilter(QgsServerFilter):

    def __init__(self, serverIface):
        super(wpsFilter, self).__init__(serverIface)

    def requestReady(self):
        """request ready"""
        QgsMessageLog.logMessage("wpsFilter.requestReady")

    def sendResponse(self):
        """send response"""
        QgsMessageLog.logMessage("wpsFilter.sendResponse")

    def responseComplete(self):
        QgsMessageLog.logMessage("wpsFilter.responseComplete")
        request = self.serverInterface().requestHandler()
        # Inject env vars from the server env to the WPS env
        for k in ('SERVER_PORT', 'HTTPS', 'HTTP_HOST'):
            os.environ[k] = self.serverInterface().getEnv(k)
        params = request.parameterMap()
        service = params.get('SERVICE', '')
        # pdb.set_trace()
        if service and service.upper() == 'WPS':
            if params.get('REQUEST', '').upper() == 'GETSCHEMA':
                self.processGetSchema(request, params)
            else:
                self.processWpsRequest(request, params)

    def processGetSchema(self, request, params):
        outputformat = params.get('OUTPUTFORMAT', '').upper()
        if outputformat not in ('XMLSCHEMA', 'JSON'):
            return

        geometryname = params.get('GEOMETRYNAME', '').upper()

        infoformat = 'text/plain'
        spath = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'schemas')
        if outputformat == 'XMLSCHEMA':
            infoformat = 'text/xml; subtype=gml/2.1.2'
            spath = os.path.join(spath, 'gml')
            spath = os.path.join(spath, '2.1.2')
            if geometryname == 'POINT':
                spath = os.path.join(spath, 'feature-point.xsd')
            elif geometryname == 'LINE':
                spath = os.path.join(spath, 'feature-line.xsd')
            elif geometryname == 'POLYGON':
                spath = os.path.join(spath, 'feature-polygon.xsd')
            else:
                spath = os.path.join(spath, 'feature.xsd')
        elif outputformat == 'JSON':
            infoformat = 'application/json'
            spath = os.path.join(spath, 'geojson')
            if geometryname == 'POINT':
                spath = os.path.join(spath, 'schema-geojson-point.json')
            elif geometryname == 'LINE':
                spath = os.path.join(spath, 'schema-geojson-line.json')
            elif geometryname == 'POLYGON':
                spath = os.path.join(spath, 'schema-geojson-polygon.json')
            else:
                spath = os.path.join(spath, 'schema-geojson.json')

        if not os.path.exists(spath):
            return

        read_data = ''
        with open(spath, 'r') as f:
            read_data = f.read()

        request.clearHeaders()
        request.clearBody()
        request.setInfoFormat(infoformat)
        request.appendBody(read_data)

    def processWpsRequest(self, request, params):
        # prepare query
        inputQuery = '&'.join(["%s=%s" % (k, params[k]) for k in params if k.lower(
        ) != 'map' and k.lower() != 'config' and k.lower != 'request_body'])
        request_body = params.get('REQUEST_BODY', '')

        # get config
        configPath = os.getenv("PYWPS_CFG")
        if not configPath and 'config' in params:
            configPath = params['config']
        elif not configPath and 'CONFIG' in params:
            configPath = params['CONFIG']
        QgsMessageLog.logMessage("configPath " + str(configPath))

        if configPath:
            os.environ["PYWPS_CFG"] = configPath
        pywpsConfig.loadConfiguration()

        try:
            providerList = ''
            algList = ''
            algsFilter = ''
            if pywpsConfig.config.has_section('qgis'):
                # get the providers to publish
                if pywpsConfig.config.has_option('qgis', 'providers'):
                    providerList = pywpsConfig.getConfigValue(
                        'qgis', 'providers')
                    if providerList:
                        providerList = providerList.split(',')
                # get the algorithm list to publish
                if pywpsConfig.config.has_option('qgis', 'algs'):
                    algList = pywpsConfig.getConfigValue('qgis', 'algs')
                    if algList:
                        algList = algList.split(',')
                # get the algorithm filter
                if pywpsConfig.config.has_option('qgis', 'algs_filter'):
                    algsFilter = pywpsConfig.getConfigValue(
                        'qgis', 'algs_filter')

            # init Processing
            Processing.initialize()
            # load QGIS Processing config
            if pywpsConfig.config.has_section('qgis_processing'):
                for opt in pywpsConfig.config.options('qgis_processing'):
                    opt_val = pywpsConfig.getConfigValue(
                        'qgis_processing', opt)
                    ProcessingConfig.setSettingValue(opt.upper(), opt_val)
                # Reload algorithms
                Processing.updateAlgsList()
            # modify processes path and reload algorithms
            if pywpsConfig.config.has_section('qgis') and pywpsConfig.config.has_option('qgis', 'processing_folder'):
                processingPath = pywpsConfig.getConfigValue(
                    'qgis', 'processing_folder')
                if not os.path.exists(processingPath):
                    if configPath and os.path.exists(configPath):
                        processingPath = os.path.join(
                            os.path.dirname(configPath),
                            processingPath
                        )
                        processingPath = os.path.abspath(processingPath)
                    else:
                        configFilesLocation = pywpsConfig._getDefaultConfigFilesLocation()
                        for configFileLocation in configFilesLocation:
                            if os.path.exists(configFileLocation):
                                processingPath = os.path.join(
                                    os.path.dirname(configFileLocation),
                                    processingPath
                                )
                                processingPath = os.path.abspath(
                                    processingPath)
                QgsMessageLog.logMessage(
                    "processing_folder: " + processingPath)
                if os.path.exists(processingPath) and os.path.isdir(processingPath):
                    ProcessingConfig.setSettingValue(
                        'MODELS_FOLDER', os.path.join(processingPath, 'models'))
                    ProcessingConfig.setSettingValue(
                        'SCRIPTS_FOLDER', os.path.join(processingPath, 'scripts'))
                    ProcessingConfig.setSettingValue(
                        'R_SCRIPTS_FOLDER', os.path.join(processingPath, 'rscripts'))
                    # Reload algorithms
                    Processing.updateAlgsList()

            crsList = []
            if pywpsConfig.config.has_section('qgis') and pywpsConfig.config.has_option('qgis', 'input_bbox_crss'):
                inputBBoxCRSs = pywpsConfig.getConfigValue(
                    'qgis', 'input_bbox_crss')
                inputBBoxCRSs = inputBBoxCRSs.split(',')
                crsList = [proj.strip().upper() for proj in inputBBoxCRSs]

            # get QGIS project path
            projectPath = os.getenv("QGIS_PROJECT_FILE")
            if not projectPath and 'map' in params:
                projectPath = params['map']
            elif not projectPath and 'MAP' in params:
                projectPath = params['MAP']
            # projectFolder
            projectFolder = ''
            if projectPath and os.path.exists(projectPath):
                projectFolder = os.path.dirname(projectPath)
            QgsMessageLog.logMessage("projectPath " + str(projectPath))

            rasterLayers = []
            vectorLayers = []

            if projectPath and os.path.exists(projectPath):
                p_dom = minidom.parse(projectPath)
                for ml in p_dom.getElementsByTagName('maplayer'):
                    l = {'type': ml.attributes["type"].value,
                         'name': ml.getElementsByTagName('layername')[0].childNodes[0].data,
                         'datasource': ml.getElementsByTagName('datasource')[0].childNodes[0].data,
                         'provider': ml.getElementsByTagName('provider')[0].childNodes[0].data,
                         'crs': ml.getElementsByTagName('srs')[0].getElementsByTagName('authid')[0].childNodes[0].data,
                         'proj4': ml.getElementsByTagName('srs')[0].getElementsByTagName('proj4')[0].childNodes[0].data
                         }
                    # Update relative path
                    if l['provider'] in ['ogr', 'gdal'] and str(l['datasource']).startswith('.'):
                        l['datasource'] = os.path.abspath(
                            os.path.join(projectFolder, l['datasource']))
                        if not os.path.exists(l['datasource']):
                            continue
                    elif l['provider'] in ['gdal'] and str(l['datasource']).startswith('NETCDF:'):
                        theURIParts = l['datasource'].split(":")
                        src = theURIParts[1]
                        src = src.replace('"', '')
                        if src.startswith('.'):
                            src = os.path.abspath(
                                os.path.join(projectFolder, src))
                        theURIParts[1] = '"' + src + '"'
                        l['datasource'] = ':'.join(theURIParts)

                    if l['type'] == "raster":
                        rasterLayers.append(l)
                    elif l['type'] == "vector":
                        l['geometry'] = ml.attributes["geometry"].value
                        vectorLayers.append(l)

                defaultCrs = ''
                for mapcanvas in p_dom.getElementsByTagName('mapcanvas'):
                    for destinationsrs in mapcanvas.getElementsByTagName('destinationsrs'):
                        for authid in destinationsrs.getElementsByTagName('authid'):
                            defaultCrs = authid.childNodes[0].data
                            crsList.append(defaultCrs)
                for wmsCrsList in p_dom.getElementsByTagName('WMSCrsList'):
                    for wmsCrs in wmsCrsList.getElementsByTagName('value'):
                        wmsCrsValue = wmsCrs.childNodes[0].data
                        if wmsCrsValue and wmsCrsValue != defaultCrs:
                            crsList.append(wmsCrsValue)

            # if no processes found no processes return (deactivate default
            # pywps process)
            processes = [None]
            identifier = params.get('IDENTIFIER', '').lower()
            for i in get_processing_algs():
                if providerList and i not in providerList:
                    continue
                QgsMessageLog.logMessage(
                    "provider " + i + " " + str(len(get_processing_algs()[i])))
                for m in get_processing_algs()[i]:
                    if identifier and identifier != m:
                        continue
                    if algList and m not in algList:
                        continue
                    if algsFilter:
                        alg = Processing.getAlgorithm(m)
                        if algsFilter.lower() not in alg.name.lower() and algsFilter.lower() not in m.lower():
                            continue
                    QgsMessageLog.logMessage("provider " + i + " " + m)
                    processes.append(QGISProcessFactory(
                        m, projectPath, vectorLayers, rasterLayers, crsList,
                        get_wps_server_address(self.serverInterface(), params)))

            #pywpsConfig.setConfigValue("server","outputPath", '/tmp/wpsoutputs')
            #pywpsConfig.setConfigValue("server","logFile", '/tmp/pywps.log')
            wpsaddress = get_wps_server_address(self.serverInterface(), params)
            QgsMessageLog.logMessage("wpsaddress " + get_wps_server_address(self.serverInterface(), params))

            # init wps
            method = 'GET'
            if request_body:
                method = 'POST'
            QgsMessageLog.logMessage("method " + method)
            root = logging.getLogger()
            if root.handlers:
                for handler in root.handlers:
                    root.removeHandler(handler)
            wps = pywps.Pywps(method)
            logging.info("method " + method)

            # create the request file for POST request
            if request_body:
                tmpPath = pywpsConfig.getConfigValue("server", "tempPath")
                requestFile = open(os.path.join(
                    tmpPath, "request-" + str(wps.UUID)), "w")
                requestFile.write(str(request_body))
                requestFile.close()
                requestFile = open(os.path.join(
                    tmpPath, "request-" + str(wps.UUID)), "r")
                inputQuery = requestFile

            if wps.parseRequest(inputQuery):
                try:
                    response = wps.performRequest(processes=processes)
                    if response:
                        request.clearHeaders()
                        request.clearBody()
                        #request.setHeader('Content-type', 'text/xml')
                        QgsMessageLog.logMessage(
                            "contentType " + wps.request.contentType)
                        request.setInfoFormat(wps.request.contentType)
                        resp = wps.response
                        if not pywpsConfig.getConfigValue("wps", "serveraddress") and wps.request.contentType == 'application/xml':
                            import re
                            import xml.sax.saxutils as saxutils
                            resp = re.sub(
                                r'Get xlink:href=".*"', 'Get xlink:href="' + saxutils.escape(wpsaddress) + '"', resp)
                            resp = re.sub(
                                r'Post xlink:href=".*"', 'Post xlink:href="' + saxutils.escape(wpsaddress) + '"', resp)
                        elif pywpsConfig.getConfigValue("wps", "serveraddress") and wps.request.contentType == 'application/xml':
                            import re
                            m = re.search(r'Get xlink:href="(.*)"', resp)
                            if m and m.group(1).count('?') == 2:
                                import xml.sax.saxutils as saxutils
                                resp = re.sub(r'Get xlink:href=".*"', 'Get xlink:href="' + m.group(1)[
                                              :-1] + saxutils.escape('&') + '"', resp)
                        # test response type
                        if isinstance(resp, file):
                            resp = resp.read()
                        request.appendBody(resp)
                        # Debug output useful for development
                        # QgsMessageLog.logMessage(
                        #    "WPS Response:\n%s" % resp)
                    else:
                        QgsMessageLog.logMessage("No response")
                except Exception as e:
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    QgsMessageLog.logMessage("Exception: %s\n%s" % (
                        e,
                        ''.join(traceback.format_tb(exc_traceback)) + str(e)))
                    QgsMessageLog.logMessage(
                        "Exception performing request")
            else:
                QgsMessageLog.logMessage("parseRequest False")
        except WPSException as e:
            QgsMessageLog.logMessage("WPSException: " + str(e))
            request.clearHeaders()
            #request.setHeader('Content-type', 'text/xml')
            request.clearBody()
            request.setInfoFormat('text/xml')
            request.appendBody(str(e))
        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            QgsMessageLog.logMessage("Exception: %s\n%s" % (
                e,
                ''.join(traceback.format_tb(exc_traceback)) + str(e)))
            request.clearHeaders()
            #request.setHeader('Content-type', 'text/xml')
            request.clearBody()
            request.setInfoFormat('text/xml')
            request.appendBody(str(e))

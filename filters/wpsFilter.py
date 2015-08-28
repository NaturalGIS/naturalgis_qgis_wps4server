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

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'PyWPS'))
import pywps
from pywps import config as pywpsConfig
from pywps.Exceptions import *
from xml.dom import minidom
from xml.sax.saxutils import escape

from processing.core.Processing import Processing
from processing.core.ProcessingConfig import ProcessingConfig, Setting
from processing.core.parameters import *
from processing.tools.general import *

def QGISProcessFactory(alg_name, project='', vectors=[], rasters=[]):
    """This is the bridge between SEXTANTE and PyWPS:
    it creates PyWPS processes based on SEXTANTE alg name"""
    from pywps.Process import WPSProcess
    from new import classobj
    import types
    
    # Sanitize name
    class_name = alg_name.replace(':', '_')
    alg = Processing.getAlgorithm(alg_name)

    # layer inputs
    rasterLayers = rasters
    vectorLayers = vectors
    
    def process_init(self):
        # Automatically init the process attributes
        WPSProcess.__init__(self,
            identifier=alg_name, # must be same, as filename
            title=escape(alg.name).replace('\\',''),
            version = "0.1",
            storeSupported = "true",
            statusSupported = "true",
            abstract= '<![CDATA[' + str(alg) + ']]>',
            grassLocation=False)
        self.alg = alg
        
        # Test parameters
        if not len( self.alg.parameters ):
            self.alg.defineCharacteristics()
        
        # Add I/O
        i = 1
        for parm in alg.parameters:
            minOccurs = 1
            if getattr(parm, 'optional', False):
                minOccurs = 0
                
            # TODO: create "LiteralValue", "ComplexValue" or "BoundingBoxValue"
            # this can be done checking the class:
            # parm.__class__, one of
            # ['Parameter', 'ParameterBoolean', 'ParameterCrs', 'ParameterDataObject', 'ParameterExtent', 'ParameterFile', 'ParameterFixedTable', 'ParameterMultipleInput', 'ParameterNumber', 'ParameterRange', 'ParameterRaster', 'ParameterSelection', 'ParameterString', 'ParameterTable','ParameterTableField', 'ParameterVector']
            if parm.__class__.__name__ == 'ParameterVector':
                values = []
                if vectorLayers and ParameterVector.VECTOR_TYPE_ANY in parm.shapetype :
                    values = [l['name'] for l in vectorLayers]
                elif vectorLayers :
                    if ParameterVector.VECTOR_TYPE_POINT in parm.shapetype :
                        values += [l['name'] for l in vectorLayers if l['geometry'] == 'Point']
                    if ParameterVector.VECTOR_TYPE_LINE in parm.shapetype :
                        values += [l['name'] for l in vectorLayers if l['geometry'] == 'Line']
                    if ParameterVector.VECTOR_TYPE_POLYGON in parm.shapetype :
                        values += [l['name'] for l in vectorLayers if l['geometry'] == 'Polygon']
                if values :
                    self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                                                    minOccurs=minOccurs,
                                                    type=types.StringType)
                    self._inputs['Input%s' % i].values = values
                else :
                    self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                        minOccurs=minOccurs, formats = [{'mimeType':'text/xml'}])
                        
            elif parm.__class__.__name__ == 'ParameterRaster':
                if rasterLayers :
                    self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                                                    minOccurs=minOccurs,
                                                    type=types.StringType)
                    self._inputs['Input%s' % i].values = [l['name'] for l in rasterLayers]
                else :
                    self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                        minOccurs=minOccurs, formats = [{'mimeType':'image/tiff'}])
                        
            elif parm.__class__.__name__ == 'ParameterTable':
                self._inputs['Input%s' % i] = self.addComplexInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                    minOccurs=minOccurs, formats = [{'mimeType':'text/csv'}])
                    
            elif parm.__class__.__name__ == 'ParameterExtent':
                self._inputs['Input%s' % i] = self.addBBoxInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                    minOccurs=minOccurs)
                    
            elif parm.__class__.__name__ == 'ParameterSelection':
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                                                minOccurs=minOccurs,
                                                type=types.StringType,
                                                default=getattr(parm, 'default', None))
                self._inputs['Input%s' % i].values = parm.options
                
            elif parm.__class__.__name__ == 'ParameterRange':
                tokens = parm.default.split(',')
                n1 = float(tokens[0])
                n2 = float(tokens[1])
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                                                minOccurs=minOccurs,
                                                type=types.FloatType,
                                                default=n1)
                self._inputs['Input%s' % i].values = ((n1,n2))
                
            else:
                type = types.StringType
                if parm.__class__.__name__ == 'ParameterBoolean':
                    type = types.BooleanType
                elif  parm.__class__.__name__ =='ParameterNumber':
                    type = types.FloatType
                self._inputs['Input%s' % i] = self.addLiteralInput(escape(parm.name), '<![CDATA[' + parm.description + ']]>',
                                                minOccurs=minOccurs,
                                                type=type,
                                                default=getattr(parm, 'default', None))
                if parm.__class__.__name__ == 'ParameterBoolean':
                    self._inputs['Input%s' % i].values=(True,False)
            i += 1
        i = 1
        for parm in alg.outputs:
            # TODO: create "LiteralOutput", "ComplexOutput" or "BoundingBoxOutput"
            # this can be done checking the class:
            # parm.__class__, one of
            # ['Output', 'OutputDirectory', 'OutputExtent', 'OutputFile', 'OutputHtml', 'OutputNumber', 'OutputRaster', 'OutputString', 'OutputTable', 'OutputVector']
            if parm.__class__.__name__ == 'OutputVector':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
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
                    },{
                        'mimeType':'application/json',
                        'encoding': 'utf-8'
                    }]
                )
                if pywpsConfig.getConfigValue("qgis","qgisserveraddress") :
                    self._outputs['Output%s' % i].useQgisServer = True
            elif parm.__class__.__name__ == 'OutputRaster':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                    formats = [{'mimeType':'image/tiff'}])
                if pywpsConfig.getConfigValue("qgis","qgisserveraddress") :
                    self._outputs['Output%s' % i].useQgisServer = True
            elif parm.__class__.__name__ == 'OutputTable':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                    formats = [{'mimeType':'text/csv'}])
            elif parm.__class__.__name__ == 'OutputHtml':
                self._outputs['Output%s' % i] = self.addComplexOutput(parm.name, parm.description,
                    formats = [{'mimeType':'text/html'}])
            elif parm.__class__.__name__ == 'OutputExtent':
                self._outputs['Output%s' % i] = self.addBBoxOutput(parm.name, parm.description)
            else:
                type = types.StringType
                if  parm.__class__.__name__ =='OutputNumber':
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
        for k in self._inputs:
            v = getattr(self, k)
            parm = self.alg.getParameterFromName( v.identifier )
            if parm.__class__.__name__ == 'ParameterVector':
                values = []
                if vectorLayers and ParameterVector.VECTOR_TYPE_ANY in parm.shapetype :
                    values = [l for l in vectorLayers]
                elif vectorLayers :
                    if ParameterVector.VECTOR_TYPE_POINT in parm.shapetype :
                        values += [l for l in vectorLayers if l['geometry'] == 'Point']
                    if ParameterVector.VECTOR_TYPE_LINE in parm.shapetype :
                        values += [l for l in vectorLayers if l['geometry'] == 'Line']
                    if ParameterVector.VECTOR_TYPE_POLYGON in parm.shapetype :
                        values += [l for l in vectorLayers if l['geometry'] == 'Polygon']
                if values :
                    layerName = v.getValue()
                    values = [l for l in values if l['name'] == layerName]
                    l = values[0]
                    layer = QgsVectorLayer( l['datasource'], l['name'], l['provider'] )
                    mlr.addMapLayer( layer, False )
                    args[v.identifier] = layer
                else :
                    fileName = v.getValue()
                    fileInfo = QFileInfo( fileName )
                    # move fileName to fileName.gml for ogr
                    with open( fileName, 'r' ) as f :
                        o = open( fileName+'.gml', 'w' )
                        o.write( f.read() )
                        o.close()
                    import shutil
                    shutil.copy2(fileName+'.gml', '/tmp/test.gml' )
                    # get layer
                    layer = QgsVectorLayer( fileName+'.gml', fileInfo.baseName(), 'ogr' )
                    pr = layer.dataProvider()
                    e = layer.extent()
                    mlr.addMapLayer( layer, False )
                    args[v.identifier] = layer
                    
            elif parm.__class__.__name__ == 'ParameterRaster':
                if rasterLayers :
                    layerName = v.getValue() 
                    values = [l for l in rasterLayers if l['name'] == layerName]
                    l = values[0]
                    layer = QgsRasterLayer( l['datasource'], l['name'], l['provider'] )
                    crs = l['crs']
                    qgsCrs = None
                    if str(crs).startswith('USER:') :
                        qgsCrs = crs = QgsCoordinateReferenceSystem()
                        qgsCrs.createFromProj4( str(l['proj4']) )
                    else :
                        qgsCrs = QgsCoordinateReferenceSystem(crs, QgsCoordinateReferenceSystem.EpsgCrsId)
                    if qgsCrs :
                        layer.setCrs( qgsCrs )
                    mlr.addMapLayer( layer, False )
                    args[v.identifier] = layer
                else :
                    fileName = v.getValue()
                    fileInfo = QFileInfo( fileName )
                    layer = QgsRasterLayer( fileName, fileInfo.baseName(), 'gdal' )
                    mlr.addMapLayer( layer, False )
                    args[v.identifier] = layer
                    
            elif parm.__class__.__name__ == 'ParameterExtent':
                coords = v.getValue().coords
                args[v.identifier] = str(coords[0][0])+','+str(coords[1][0])+','+str(coords[0][1])+','+str(coords[1][1])
            else:
                args[v.identifier] = v.getValue()
        # Adds None for output parameter(s)
        for k in self._outputs:
            v = getattr(self, k)
            args[v.identifier] = None
        
        if not len( self.alg.parameters ):
            self.alg.defineCharacteristics()

        tAlg = Processing.runAlgorithm(self.alg, None, args)
        # if runalg failed return exception message
        if not tAlg:
            return 'Error in processing'
        # clear map layer registry
        mlr.removeAllMapLayers()
        # get result
        result = tAlg.getOutputValuesAsDictionary()
        for k in self._outputs:
            v = getattr(self, k)
            parm = self.alg.getOutputFromName( v.identifier )
            if parm.__class__.__name__ == 'OutputVector':
                outputName = result.get(v.identifier, None)
                if not outputName :
                  return 'No output file'
                # get output file info
                outputInfo = QFileInfo( outputName )
                # get the output QGIS vector layer
                outputLayer = QgsVectorLayer( outputName, outputInfo.baseName(), 'ogr' )
                # Update CRS
                outputLayer.setCrs( tAlg.crs )
                # create the output GML file for pywps
                # define the output GML file path
                outputFile = os.path.join( outputInfo.absolutePath(), outputInfo.baseName()+'.gml' )
                # write the output GML file
                error = QgsVectorFileWriter.writeAsVectorFormat( outputLayer, outputFile, 'utf-8', None, 'GML', False, None, ['XSISCHEMAURI=http://schemas.opengis.net/gml/2.1.2/feature.xsd'] )
                args[v.identifier] = outputFile
                # add output layer to map layer registry
                #outputLayer = QgsVectorLayer( outputFile, v.identifier, 'ogr' )
                #mlr.addMapLayer( outputLayer )
            elif parm.__class__.__name__ == 'OutputRaster':
                outputName = result.get(v.identifier, None)
                if not outputName :
                  return 'No output file'
                args[v.identifier] = outputName
            else:
                args[v.identifier] = result.get(v.identifier, None)
        for k in self._outputs:
            v = getattr(self, k)
            v.setValue( args[v.identifier] )
        return

    try:
        new_class = classobj( str('%sProcess' % class_name), (WPSProcess, ), {
            '__init__' :  process_init,
            'execute' : execute,
            'params' : [],
            'alg' : alg,
            '_inputs' : {},
            '_outputs' : {}
        })
        return new_class
    except TypeError, e:
        QgsMessageLog.logMessage("QGISProcessFactory "+e.__str__())
        return None



class wpsFilter(QgsServerFilter):

    def __init__(self, serverIface):
        super(wpsFilter, self).__init__(serverIface)

    def requestReady(self):
        QgsMessageLog.logMessage("wpsFilter.requestReady")
        

    def sendResponse(self):
        QgsMessageLog.logMessage("wpsFilter.sendResponse")

    def responseComplete(self):
        QgsMessageLog.logMessage("wpsFilter.responseComplete")
        request = self.serverInterface().requestHandler()
        params = request.parameterMap()
        service = params.get('SERVICE', '')
        if service and service.upper() == 'WPS':
            # prepare query
            inputQuery = '&'.join(["%s=%s" % (k, params[k]) for k in params if k.lower() != 'map' and k.lower() != 'config' and k.lower != 'request_body'])
            request_body = params.get('REQUEST_BODY', '')
            
            # get config
            configPath = os.getenv("PYWPS_CFG")
            if not configPath and 'config' in params :
                configPath = params['config']
            elif not configPath and 'CONFIG' in params :
                configPath = params['CONFIG']
            
            if configPath :
                os.environ["PYWPS_CFG"] = configPath
            pywpsConfig.loadConfiguration()
                
            try:
                providerList = ''
                algList = ''
                algsFilter = ''
                if pywpsConfig.config.has_section( 'qgis' ) :
                    # get the providers to publish
                    if pywpsConfig.config.has_option( 'qgis', 'providers' ) :
                        providerList = pywpsConfig.getConfigValue( 'qgis', 'providers' )
                        if providerList :
                            providerList = providerList.split(',')
                    # get the algorithm list to publish
                    if pywpsConfig.config.has_option( 'qgis', 'algs' ) :
                        algList = pywpsConfig.getConfigValue( 'qgis', 'algs' )
                        if algList :
                            algList = algList.split(',')
                    # get the algorithm filter
                    if pywpsConfig.config.has_option( 'qgis', 'algs_filter' ) :
                        algsFilter = pywpsConfig.getConfigValue( 'qgis', 'algs_filter' )
                    
                
                # init Processing
                Processing.initialize()
                # modify processes path and reload algorithms
                if pywpsConfig.config.has_section( 'qgis' ) and pywpsConfig.config.has_option( 'qgis', 'processing_folder' ) :
                    processingPath = pywpsConfig.getConfigValue( 'qgis', 'processing_folder' )
                    if os.path.exists( processingPath ) and os.path.isdir( processingPath ) :
                        ProcessingConfig.setSettingValue( 'MODELS_FOLDER', os.path.join( processingPath, 'models' ) )
                        ProcessingConfig.setSettingValue( 'SCRIPTS_FOLDER', os.path.join( processingPath, 'scripts' ) )
                        ProcessingConfig.setSettingValue( 'R_FOLDER', os.path.join( processingPath, 'rscripts' ) )
                        # Reload algorithms
                        Processing.loadAlgorithms()

                # get QGIS project path
                projectPath = os.getenv("QGIS_PROJECT_FILE")
                if not projectPath and 'map' in params :
                    projectPath = params['map']
                elif not projectPath and 'MAP' in params :
                    projectPath = params['MAP']
                #projectFolder
                projectFolder = ''
                if projectPath and os.path.exists( projectPath ) :
                    projectFolder = os.path.dirname( projectPath )
                QgsMessageLog.logMessage("projectPath "+str(projectPath))
                rasterLayers = []
                vectorLayers = []
                if projectPath and os.path.exists( projectPath ) :
                    p_dom = minidom.parse( projectPath )
                    for ml in p_dom.getElementsByTagName('maplayer') :
                        l= {'type':ml.attributes["type"].value,
                            'name':ml.getElementsByTagName('layername')[0].childNodes[0].data,
                            'datasource':ml.getElementsByTagName('datasource')[0].childNodes[0].data,
                            'provider':ml.getElementsByTagName('provider')[0].childNodes[0].data,
                            'crs':ml.getElementsByTagName('srs')[0].getElementsByTagName('authid')[0].childNodes[0].data,
                            'proj4':ml.getElementsByTagName('srs')[0].getElementsByTagName('proj4')[0].childNodes[0].data
                        }
                        # Update relative path
                        if l['provider'] in ['ogr','gdal'] and str(l['datasource']).startswith('.'):
                            l['datasource'] = os.path.abspath( os.path.join( projectFolder, l['datasource'] ) )
                            if not os.path.exists( l['datasource'] ) :
                                continue
                        elif l['provider'] in ['gdal'] and str(l['datasource']).startswith('NETCDF:'):
                            theURIParts = l['datasource'].split( ":" );
                            src = theURIParts[1]
                            src = src.replace( '"', '' );
                            if src.startswith('.') :
                                src = os.path.abspath( os.path.join( projectFolder, src ) )
                            theURIParts[1] = '"' + src + '"'
                            l['datasource'] = ':'.join( theURIParts )
                            
                        if l['type'] == "raster" :
                            rasterLayers.append( l )
                        elif l['type'] == "vector" :
                            l['geometry'] = ml.attributes["geometry"].value
                            vectorLayers.append( l )
                
                        
                processes = [None] # if no processes found no processes return (deactivate default pywps process)
                identifier = params.get('IDENTIFIER', '').lower()
                for i in Processing.algs :
                    if providerList and i not in providerList :
                        continue
                    QgsMessageLog.logMessage("provider "+i+" "+str(len(Processing.algs[i])))
                    for m in Processing.algs[i]:
                        if identifier and identifier != m :
                            continue
                        if algList and m not in algList :
                            continue
                        if algsFilter :
                            alg = Processing.getAlgorithm( m )
                            if algsFilter.lower() not in alg.name.lower() and algsFilter.lower() not in m.lower():
                                continue
                        #QgsMessageLog.logMessage("provider "+i+" "+m)
                        processes.append(QGISProcessFactory(m, projectPath, vectorLayers, rasterLayers))
                
                #pywpsConfig.setConfigValue("server","outputPath", '/tmp/wpsoutputs')
                #pywpsConfig.setConfigValue("server","logFile", '/tmp/pywps.log')
                
                qgisaddress = self.serverInterface().getEnv('SERVER_NAME')+self.serverInterface().getEnv('SCRIPT_NAME')
                if self.serverInterface().getEnv('HTTPS') :
                    qgisaddress = 'https://'+qgisaddress
                else :
                    qgisaddress = 'http://'+qgisaddress
                #pywpsConfig.setConfigValue("wps","serveraddress", qgisaddress)
                #QgsMessageLog.logMessage("qgisaddress "+qgisaddress)
                #pywpsConfig.setConfigValue("qgis","qgisserveraddress", qgisaddress)
                
                # init wps
                method = 'GET'
                if request_body :
                    method = 'POST'
                wps = pywps.Pywps(method)
                
                # create the request file for POST request
                if request_body :
                    tmpPath=pywpsConfig.getConfigValue("server","tempPath")
                    requestFile = open(os.path.join(tmpPath, "request-"+str(wps.UUID)),"w")
                    requestFile.write(str(request_body))
                    requestFile.close()
                    requestFile = open(os.path.join(tmpPath, "request-"+str(wps.UUID)),"r")
                    inputQuery = requestFile
                    
                if wps.parseRequest(inputQuery):
                    response = wps.performRequest(processes=processes)
                    if response:
                        request.clearHeaders()
                        request.clearBody()
                        #request.setHeader('Content-type', 'text/xml')
                        request.setInfoFormat(wps.request.contentType)
                        request.appendBody(wps.response)
            except WPSException,e:
                        request.clearHeaders()
                        #request.setHeader('Content-type', 'text/xml')
                        request.clearBody()
                        request.setInfoFormat('text/xml')
                        request.appendBody(e.__str__())
"""
Implémentation des opérations WFS (Web Feature Service).

Fonctions privées utilisées par les routes WFS pour générer GetCapabilities,
GetFeature et DescribeFeatureType, en s'appuyant sur pygeoapi ou directement
sur MapServer selon le contexte.
"""
from typing import Dict, List, Optional, Tuple
from flask import Response
import logging
import os
import re
import requests
import xml.etree.ElementTree as ET
import html
from ckan.plugins import toolkit
import ckanext.ogc.helpers as ogc_helpers

from ckanext.ogc.views_utils import (
    _fix_xml_invalid_tag_names,
    _create_wfs_exception,
    get_pygeoapi_url,
)
# _find_dataset_for_layer_in_org est défini dans views_wms.py (résolution layer -> dataset)
from ckanext.ogc.views_wms import _find_dataset_for_layer_in_org

try:
    from pygeoapi import util as pygeoapi_util
except Exception:  # pragma: no cover
    pygeoapi_util = None

log = logging.getLogger(__name__)


def _generate_wfs_capabilities(org_id, collections):
    """Génère un document WFS GetCapabilities pour l'organisation
    Liste uniquement les datasets géographiques avec mapfile MapServer valide
    Génère des liens directs vers MapServer au lieu du proxy CKAN
    """
    # Utiliser l'URL publique de MapServer pour les liens directs
    mapserver_public_url = ogc_helpers.get_mapserver_url()
    # Utiliser l'URL CKAN pour GetCapabilities (qui reste via le proxy)
    ckan_site_url = ogc_helpers.get_ogc_base_url()
    
    # Helper function to escape XML special characters
    def escape_xml(text):
        """Escape XML special characters in text content"""
        if text is None:
            return ''
        text_str = str(text)
        # First, unescape any already-escaped entities to avoid double-escaping
        text_str = text_str.replace('&amp;', '&')
        text_str = text_str.replace('&lt;', '<')
        text_str = text_str.replace('&gt;', '>')
        text_str = text_str.replace('&quot;', '"')
        text_str = text_str.replace('&apos;', "'")
        # Now escape all special characters properly
        return html.escape(text_str)
    
    # Échapper l'URL MapServer pour XML
    mapserver_public_url_escaped = escape_xml(mapserver_public_url)
    
    # Helper function to get BBOX from collection
    def get_collection_bbox(collection):
        """Extract BBOX from collection extents"""
        extents = collection.get('extents', {})
        spatial = extents.get('spatial', {})
        bbox_list = spatial.get('bbox', [])
        
        if bbox_list and len(bbox_list) > 0:
            # bbox is [[minx, miny, maxx, maxy]] or [minx, miny, maxx, maxy]
            if isinstance(bbox_list[0], list):
                bbox = bbox_list[0]
            else:
                bbox = bbox_list
            if len(bbox) >= 4:
                return bbox[0], bbox[1], bbox[2], bbox[3]
        
        # Default BBOX for France
        return -5.0, 41.0, 10.0, 51.0
    
    # Filtrer les collections pour ne garder que celles avec mapfile MapServer valide
    # OPTIMISATION: Vérifier d'abord via le système de fichiers (rapide), puis via MapServer si nécessaire
    mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
    valid_collections = []
    
    log.debug(f"Vérification des mapfiles pour {len(collections)} collections (optimisé)...")
    
    # Vérifier d'abord via le système de fichiers (beaucoup plus rapide)
    collections_to_check_via_http = []
    for collection in collections:
        collection_id = collection.get('id')
        if not collection_id:
            continue
        
        mapfile_path_local = os.path.join(mapfiles_dir, f"{collection_id}.map")
        
        # Vérifier si le fichier existe localement (rapide)
        if os.path.exists(mapfiles_dir) and os.path.exists(mapfile_path_local):
            # Le fichier existe, vérifier rapidement sa taille (un fichier vide n'est pas valide)
            try:
                if os.path.getsize(mapfile_path_local) > 100:  # Au moins 100 bytes (un mapfile valide fait au moins quelques KB)
                    valid_collections.append(collection)
                    log.debug(f"Mapfile trouvé localement pour {collection_id}")
                else:
                    log.debug(f"Mapfile trop petit pour {collection_id}, vérification HTTP nécessaire")
                    collections_to_check_via_http.append(collection)
            except OSError as e:
                # Logging détaillé de l'erreur OSError
                log.warning(f"OSError lors de l'accès au mapfile {mapfile_path_local} pour {collection_id}")
                log.warning(f"   Type d'erreur: {type(e).__name__}")
                log.warning(f"   Message: {str(e)}")
                log.warning(f"   Errno: {e.errno if hasattr(e, 'errno') else 'N/A'}")
                log.warning(f"   Strerror: {e.strerror if hasattr(e, 'strerror') else 'N/A'}")
                log.warning(f"   Filename: {e.filename if hasattr(e, 'filename') else 'N/A'}")
                collections_to_check_via_http.append(collection)
        else:
            # Le répertoire mapfiles n'est pas accessible localement, vérifier via HTTP
            collections_to_check_via_http.append(collection)
    
    # Si on a accès au système de fichiers, on a déjà filtré la plupart
    # Sinon, ou pour les cas douteux, vérifier via HTTP (mais seulement ceux qui n'ont pas été validés)
    if collections_to_check_via_http:
        log.debug(f"Vérification HTTP pour {len(collections_to_check_via_http)} collections...")
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        
        # Utiliser ThreadPoolExecutor pour paralléliser les vérifications HTTP
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        def check_mapfile_http(collection):
            """Vérifie un mapfile via HTTP"""
            collection_id = collection.get('id')
            mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
            
            try:
                test_params = {'map': mapfile_path, 'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetCapabilities'}
                test_response = requests.get(f"{mapserver_url}/wfs", params=test_params, timeout=3)
                if test_response.status_code == 200 and 'text/xml' in test_response.headers.get('Content-Type', '').lower():
                    if b'wfs:WFS_Capabilities' in test_response.content or b'WFS_Capabilities' in test_response.content:
                        return collection, True
            except requests.exceptions.RequestException:
                pass
            return collection, False
        
        # Vérifier en parallèle (max 10 threads pour éviter de surcharger MapServer)
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_collection = {executor.submit(check_mapfile_http, col): col for col in collections_to_check_via_http}
            for future in as_completed(future_to_collection):
                collection, exists = future.result()
                if exists:
                    valid_collections.append(collection)
                    log.debug(f"Mapfile valide (HTTP) pour {collection.get('id')}")
    
    log.info(f"{len(valid_collections)}/{len(collections)} collections géographiques avec mapfile valide")
    
    # Récupérer tous les FeatureTypes (layers res_xxx) depuis MapServer pour chaque dataset
    # Un mapfile par dataset, un layer par ressource : MapServer expose un FeatureType par layer
    mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
    feature_types_by_dataset = []  # [(dataset_name, [(layer_name, title, bbox), ...]), ...]
    
    def _extract_wfs_feature_types(collection):
        """Interroge MapServer WFS GetCapabilities pour extraire les FeatureTypes (layers) du mapfile."""
        collection_id = collection.get('id')
        mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
        try:
            test_params = {'map': mapfile_path, 'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetCapabilities'}
            resp = requests.get(f"{mapserver_url}/wfs", params=test_params, timeout=5)
            if resp.status_code != 200 or b'wfs:WFS_Capabilities' not in resp.content and b'WFS_Capabilities' not in resp.content:
                return collection_id, []
            root = ET.fromstring(resp.content)
            # WFS 2.0 FeatureTypeList
            ns = {'wfs': 'http://www.opengis.net/wfs/2.0', 'ows': 'http://www.opengis.net/ows/1.1'}
            ft_list = root.find('.//wfs:FeatureTypeList', ns) or root.find('.//FeatureTypeList')
            if ft_list is None:
                return collection_id, []
            types_found = []
            for ft in (ft_list.findall('wfs:FeatureType', ns) or ft_list.findall('FeatureType') or []):
                name_el = ft.find('wfs:Name', ns) or ft.find('Name')
                title_el = ft.find('ows:Title', ns) or ft.find('Title') or ft.find('ows:Title')
                bbox_el = ft.find('ows:WGS84BoundingBox', ns) or ft.find('WGS84BoundingBox')
                layer_name = name_el.text.strip() if name_el is not None and name_el.text else None
                if not layer_name:
                    continue
                layer_title = (title_el.text or layer_name).strip() if title_el is not None else layer_name
                bbox = None
                if bbox_el is not None:
                    lc = bbox_el.find('ows:LowerCorner', ns) or bbox_el.find('LowerCorner')
                    uc = bbox_el.find('ows:UpperCorner', ns) or bbox_el.find('UpperCorner')
                    if lc is not None and uc is not None and lc.text and uc.text:
                        try:
                            low = [float(x) for x in lc.text.split()]
                            up = [float(x) for x in uc.text.split()]
                            if len(low) >= 2 and len(up) >= 2:
                                bbox = (low[0], low[1], up[0], up[1])
                        except (ValueError, TypeError):
                            pass
                types_found.append((layer_name, layer_title, bbox))
            return collection_id, types_found
        except Exception as e:
            log.debug(f"Extraction FeatureTypes WFS pour {collection_id}: {e}")
            return collection_id, []
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_extract_wfs_feature_types, c): c for c in valid_collections}
        for future in as_completed(futures):
            dataset_id, types_list = future.result()
            coll = next((c for c in valid_collections if c.get('id') == dataset_id), None)
            if coll is None:
                continue
            if types_list:
                feature_types_by_dataset.append((dataset_id, types_list, coll))
            else:
                # Fallback: dataset comme FeatureType unique (rétrocompat mapfiles anciens)
                feature_types_by_dataset.append((dataset_id, [(dataset_id, coll.get('title', dataset_id), get_collection_bbox(coll))], coll))
    
    feature_types_flat = []
    for _dataset_id, types_list, coll in feature_types_by_dataset:
        for ln, lt, bb in types_list:
            feature_types_flat.append((ln, lt, bb, coll))
    
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities version="2.0.0" xmlns:wfs="http://www.opengis.net/wfs/2.0"
                     xmlns:ows="http://www.opengis.net/ows/1.1"
                     xmlns:xlink="http://www.w3.org/1999/xlink"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     xsi:schemaLocation="http://www.opengis.net/wfs/2.0 http://schemas.opengis.net/wfs/2.0/wfs.xsd">
  <ows:ServiceIdentification>
    <ows:Title>WFS Service for Organization {org_id}</ows:Title>
    <ows:Abstract>Web Feature Service for CKAN organization - Direct MapServer links</ows:Abstract>
    <ows:ServiceType>WFS</ows:ServiceType>
    <ows:ServiceTypeVersion>2.0.0</ows:ServiceTypeVersion>
  </ows:ServiceIdentification>
  <ows:OperationsMetadata>
    <ows:Operation name="GetCapabilities">
      <ows:DCP>
        <ows:HTTP>
          <ows:Get xlink:href="{ckan_site_url}/maps/{org_id}?SERVICE=WFS&amp;REQUEST=GetCapabilities"/>
        </ows:HTTP>
      </ows:DCP>
    </ows:Operation>
    <ows:Operation name="GetFeature">
      <ows:Parameter name="outputFormat">
        <ows:Value>application/gml+xml; version=3.2</ows:Value>
        <ows:Value>application/json</ows:Value>
        <ows:Value>geojson</ows:Value>
      </ows:Parameter>
      <ows:DCP>
        <ows:HTTP>
          <ows:Get xlink:href="{ckan_site_url}/maps/{org_id}?SERVICE=WFS&amp;VERSION=2.0.0&amp;REQUEST=GetFeature"/>
        </ows:HTTP>
      </ows:DCP>
    </ows:Operation>
    <ows:Operation name="DescribeFeatureType">
      <ows:Parameter name="outputFormat">
        <ows:Value>application/gml+xml; version=3.2</ows:Value>
        <ows:Value>application/xml</ows:Value>
      </ows:Parameter>
      <ows:DCP>
        <ows:HTTP>
          <ows:Get xlink:href="{ckan_site_url}/maps/{org_id}?SERVICE=WFS&amp;VERSION=2.0.0&amp;REQUEST=DescribeFeatureType"/>
        </ows:HTTP>
      </ows:DCP>
    </ows:Operation>
  </ows:OperationsMetadata>
  <FeatureTypeList>'''
    
    getfeature_base_url = f"{ckan_site_url}/maps/{org_id}"
    for layer_name, layer_title, bbox_tuple, _coll in feature_types_flat:
        if bbox_tuple is None or not (isinstance(bbox_tuple, (list, tuple)) and len(bbox_tuple) >= 4):
            minx, miny, maxx, maxy = -5.0, 41.0, 10.0, 51.0
        else:
            minx, miny, maxx, maxy = bbox_tuple[0], bbox_tuple[1], bbox_tuple[2], bbox_tuple[3]
        collection_title = escape_xml(layer_title)
        
        xml += f'''
    <FeatureType>
      <Name>{escape_xml(layer_name)}</Name>
      <Title>{collection_title}</Title>
      <DefaultCRS>urn:ogc:def:crs:EPSG::4326</DefaultCRS>
      <ows:WGS84BoundingBox>
        <ows:LowerCorner>{minx:.6f} {miny:.6f}</ows:LowerCorner>
        <ows:UpperCorner>{maxx:.6f} {maxy:.6f}</ows:UpperCorner>
      </ows:WGS84BoundingBox>
      <ows:Metadata>
        <ows:MetadataURL xlink:type="simple" xlink:href="{escape_xml(getfeature_base_url)}?SERVICE=WFS&amp;VERSION=2.0.0&amp;REQUEST=GetCapabilities"/>
      </ows:Metadata>
      <ows:Metadata>
        <ows:MetadataURL xlink:type="simple" xlink:href="{escape_xml(getfeature_base_url)}" role="GetFeature"/>
      </ows:Metadata>
      <ows:Metadata>
        <ows:MetadataURL xlink:type="simple" xlink:href="{escape_xml(getfeature_base_url)}" role="DescribeFeatureType"/>
      </ows:Metadata>
    </FeatureType>'''
    
    xml += '''
  </FeatureTypeList>
</wfs:WFS_Capabilities>'''
    
    # Headers pour améliorer la compatibilité avec QGIS
    headers = {
        'Content-Type': 'application/xml; charset=utf-8',
        'Content-Length': str(len(xml.encode('utf-8'))),
        'Cache-Control': 'public, max-age=3600',
        'X-Content-Type-Options': 'nosniff'
    }
    
    return Response(xml, mimetype='application/xml', headers=headers)


def _handle_wfs_getfeature_direct(org_id: str, params: Dict) -> Response:
    """
    Gère les requêtes WFS GetFeature directement via MapServer (sans analyser toutes les collections)
    Se comporte comme le lien direct /wfs?map=... qui fonctionne bien
    
    Args:
        org_id: ID de l'organisation
        params: Paramètres de la requête WFS
        
    Returns:
        Réponse avec les features en GML ou GeoJSON
    """
    try:
        # NETTOYER les paramètres pour éviter les duplications (client peut avoir des paramètres dupliqués dans l'URL)
        # S'assurer qu'on utilise seulement la première valeur de chaque paramètre
        clean_params = {}
        for key, value in params.items():
            if isinstance(value, list) and len(value) > 0:
                clean_params[key.upper()] = value[0]
            else:
                clean_params[key.upper()] = value
        params = clean_params
        
        # Récupérer les paramètres WFS
        typenames = params.get('TYPENAMES') or params.get('TYPENAME') or params.get('TYPENAMES') or params.get('TYPENAME')
        
        if not typenames:
            return _create_wfs_exception('MissingParameterValue', 
                                       'TYPENAMES parameter is required', 
                                       'TYPENAMES')
        
        # TYPENAMES peut être un nom de layer (res_xxx) ou un nom de dataset (rétrocompat)
        # Un mapfile par dataset, un layer par ressource : MapServer attend le nom du layer
        collection_id = typenames
        dataset_name = None
        layer_name_for_mapserver = collection_id
        
        context = {'ignore_auth': True, 'use_cache': True}
        try:
            dataset = toolkit.get_action('package_show')(context, {'id': collection_id})
            dataset_org = dataset.get('organization')
            if not dataset_org or dataset_org.get('name') != org_id:
                return _create_wfs_exception('InvalidParameterValue', 
                                           f'Feature type {collection_id} does not belong to organization {org_id}', 
                                           'TYPENAMES')
            dataset_name = collection_id
        except Exception:
            # collection_id n'est pas un dataset : peut-être un layer (res_xxx)
            dataset_name = _find_dataset_for_layer_in_org(org_id, collection_id)
            if dataset_name is None:
                log.warning(f"Feature type {collection_id} not found (ni dataset, ni layer dans l'org)")
                return _create_wfs_exception('InvalidParameterValue', 
                                           f'Feature type {collection_id} not found', 
                                           'TYPENAMES')
            layer_name_for_mapserver = collection_id
            try:
                dataset = toolkit.get_action('package_show')(context, {'id': dataset_name})
            except Exception as e:
                log.warning(f"Dataset {dataset_name} not found: {e}")
                return _create_wfs_exception('InvalidParameterValue', 
                                           f'Feature type {collection_id} not found', 
                                           'TYPENAMES')
        
        # Mapfile = dataset_name.map, TYPENAMES = layer_name (res_xxx)
        mapfile_dataset = dataset_name
        
        # IMPORTANT: Pour WFS, le format par défaut doit être GML XML, pas JSON
        # QGIS s'attend à recevoir du XML GML pour WFS GetFeature
        outputformat = params.get('OUTPUTFORMAT') or params.get('outputformat', 'application/gml+xml')
        # QGIS utilise souvent MAXFEATURES au lieu de COUNT
        count = params.get('COUNT') or params.get('count') or params.get('MAXFEATURES') or params.get('maxFeatures')
        startindex = params.get('STARTINDEX') or params.get('startindex', '0')
        bbox = params.get('BBOX') or params.get('bbox')
        srsname = params.get('SRSNAME') or params.get('srsname') or params.get('SRS') or params.get('srs', 'EPSG:4326')
        
        # TOUTES les requêtes WFS passent par MapServer (pas pygeoapi)
        # MapServer est la source unique pour WFS/WMS
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        mapfile_path = f"/mapserver/mapfiles/{mapfile_dataset}.map"
        
        # Construire directement la requête MapServer (comme le lien direct /wfs?map=...)
        # MapServer n'accepte pas 'application/gml+xml' seul, il faut utiliser 'application/gml+xml; version=3.2' ou 'text/xml; subtype=gml/3.2.1'
        mapserver_outputformat = outputformat
        if outputformat:
            outputformat_lower = outputformat.lower()
            if outputformat_lower == 'application/gml+xml':
                mapserver_outputformat = 'application/gml+xml; version=3.2'
            elif 'application/gml+xml' in outputformat_lower and 'version' not in outputformat_lower:
                mapserver_outputformat = 'application/gml+xml; version=3.2'
            elif outputformat_lower in ['gml', 'xml']:
                mapserver_outputformat = 'text/xml; subtype=gml/3.2.1'
        else:
            mapserver_outputformat = 'text/xml; subtype=gml/3.2.1'
        
        # Construire les paramètres MapServer WFS
        wfs_version = params.get('VERSION') or params.get('version', '2.0.0')
        
        mapserver_params = {
            'map': mapfile_path,
            'SERVICE': 'WFS',
            'VERSION': wfs_version,
            'REQUEST': 'GetFeature',
            'OUTPUTFORMAT': mapserver_outputformat,
        }
        
        # WFS 2.0.0 utilise TYPENAMES (pluriel), WFS 1.0/1.1 utilise TYPENAME (singulier)
        # MapServer attend le nom du layer (res_xxx), pas le nom du dataset
        if wfs_version.startswith('2.'):
            mapserver_params['TYPENAMES'] = layer_name_for_mapserver
        else:
            mapserver_params['TYPENAME'] = layer_name_for_mapserver
        
        # Ajouter COUNT si présent
        if count:
            try:
                count_int = int(count)
                if count_int > 0:
                    mapserver_params['COUNT'] = str(count_int)
            except (ValueError, TypeError):
                log.warning(f"Invalid COUNT parameter: {count}, ignoring")
        
        # Ajouter STARTINDEX si présent
        if startindex and startindex != '0':
            try:
                startindex_int = int(startindex)
                if startindex_int > 0:
                    mapserver_params['STARTINDEX'] = str(startindex_int)
            except (ValueError, TypeError):
                log.warning(f"Invalid STARTINDEX parameter: {startindex}, ignoring")
        
        # Ajouter BBOX si présent
        if bbox:
            bbox_parts = bbox.split(',')
            if len(bbox_parts) >= 4:
                try:
                    [float(x) for x in bbox_parts[:4]]
                    if len(bbox_parts) == 4 and srsname:
                        mapserver_params['BBOX'] = f"{bbox},{srsname}"
                    else:
                        mapserver_params['BBOX'] = bbox
                except ValueError:
                    log.warning(f"Invalid BBOX format: {bbox}, ignoring")
        
        # Ajouter SRSNAME si présent
        if srsname:
            if srsname.upper().startswith('EPSG:'):
                mapserver_params['SRSNAME'] = srsname.upper()
            elif srsname.upper().startswith('EPSG'):
                mapserver_params['SRSNAME'] = srsname.upper().replace('EPSG', 'EPSG:', 1)
            else:
                try:
                    int(srsname)
                    mapserver_params['SRSNAME'] = f'EPSG:{srsname}'
                except ValueError:
                    mapserver_params['SRSNAME'] = 'EPSG:4326'
        
        log.debug(f"Proxying WFS GetFeature directly to MapServer for {collection_id} (org: {org_id})")
        log.debug(f"MapServer params: {mapserver_params}")
        
        # Appeler MapServer directement (comme le lien /wfs?map=...)
        mapserver_response = requests.get(f"{mapserver_url}/wfs", params=mapserver_params, timeout=300)
        
        # Vérifier si la réponse est une erreur HTTP
        if mapserver_response.status_code != 200:
            error_content = mapserver_response.content[:2000] if mapserver_response.content else "No content"
            log.error(f"MapServer WFS error {mapserver_response.status_code} for {collection_id}")
            log.error(f"Error content (first 2000 chars): {error_content}")
            
            # Essayer d'extraire le message d'erreur si c'est du XML
            error_msg = f"MapServer WFS error: {mapserver_response.status_code}"
            if mapserver_response.content and mapserver_response.content.startswith(b'<?xml'):
                if b'ServiceExceptionReport' in mapserver_response.content:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(mapserver_response.content)
                        for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                            error_text = exception.text or exception.get('code', '')
                            if error_text:
                                error_msg = error_text.strip()
                                break
                        if error_msg == f"MapServer WFS error: {mapserver_response.status_code}":
                            for exception in root.findall('.//ServiceException'):
                                error_text = exception.text or exception.get('code', '')
                                if error_text:
                                    error_msg = error_text.strip()
                                    break
                    except Exception:
                        pass
            
            return _create_wfs_exception('NoApplicableCode', error_msg, 'GetFeature')
        
        # Corriger les problèmes XML (noms de balises invalides avec espaces, etc.)
        xml_content = mapserver_response.content
        if xml_content:
            # Vérifier si c'est du XML avant de le corriger
            is_xml = (xml_content.startswith(b'<?xml') or 
                     xml_content.startswith(b'<wfs:') or 
                     xml_content.startswith(b'<gml:') or
                     b'<wfs:FeatureCollection' in xml_content[:200] or
                     b'<FeatureCollection' in xml_content[:200])
            
            if is_xml:
                try:
                    # Valider que le XML est bien formé avant de le corriger
                    import xml.etree.ElementTree as ET
                    try:
                        ET.fromstring(xml_content)
                        log.debug(f"XML is well-formed for {collection_id}")
                    except ET.ParseError as parse_err:
                        log.warning(f"XML parsing error for {collection_id}: {parse_err}, attempting to fix...")
                        # Essayer de corriger les problèmes XML
                        xml_content = _fix_xml_invalid_tag_names(xml_content)
                        # Vérifier à nouveau après correction
                        try:
                            ET.fromstring(xml_content)
                            log.info(f"XML fixed successfully for {collection_id}")
                        except ET.ParseError as parse_err2:
                            log.error(f"XML still invalid after fix for {collection_id}: {parse_err2}")
                            # Retourner une erreur WFS valide au lieu d'un XML invalide
                            return _create_wfs_exception('NoApplicableCode', 
                                                       f'Invalid XML response from MapServer: {str(parse_err2)}', 
                                                       'GetFeature')
                except Exception as e:
                    log.warning(f"Error fixing XML content: {e}, returning original content")
        
        # Retourner la réponse MapServer directement
        content_type = mapserver_response.headers.get('Content-Type', 'application/xml')
        if not content_type or 'xml' not in content_type.lower():
            # Si MapServer ne retourne pas le bon Content-Type, le forcer pour XML GML
            if xml_content and (xml_content.startswith(b'<?xml') or xml_content.startswith(b'<wfs:') or xml_content.startswith(b'<gml:')):
                content_type = 'application/xml; charset=utf-8'
        
        return Response(
            xml_content,
            mimetype=content_type,
            headers={
                'Content-Type': content_type,
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'public, max-age=3600'
            }
        )
        
    except Exception as e:
        log.error(f"Error in WFS GetFeature direct handler: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', f'Internal server error: {str(e)}', 'GetFeature')


def _handle_wfs_describefeaturetype_direct(org_id: str, params: Dict) -> Response:
    """
    Gère les requêtes WFS DescribeFeatureType directement via MapServer (sans analyser toutes les collections)
    Se comporte comme le lien direct /wfs?map=... qui fonctionne bien
    
    Args:
        org_id: ID de l'organisation
        params: Paramètres de la requête WFS
        
    Returns:
        Réponse XML avec le schéma de la feature type
    """
    try:
        # Récupérer les paramètres WFS
        typenames = params.get('TYPENAMES') or params.get('TYPENAME') or params.get('typenames') or params.get('typename')
        
        if not typenames:
            return _create_wfs_exception('MissingParameterValue', 
                                       'TYPENAMES parameter is required', 
                                       'TYPENAMES')
        
        collection_id = typenames
        dataset_name = None
        layer_name_for_mapserver = collection_id
        
        context = {'ignore_auth': True, 'use_cache': True}
        try:
            dataset = toolkit.get_action('package_show')(context, {'id': collection_id})
            dataset_org = dataset.get('organization')
            if not dataset_org or dataset_org.get('name') != org_id:
                return _create_wfs_exception('InvalidParameterValue', 
                                           f'Feature type {collection_id} does not belong to organization {org_id}', 
                                           'TYPENAMES')
            dataset_name = collection_id
        except Exception:
            dataset_name = _find_dataset_for_layer_in_org(org_id, collection_id)
            if dataset_name is None:
                log.warning(f"Feature type {collection_id} not found")
                return _create_wfs_exception('InvalidParameterValue', 
                                       f'Feature type {collection_id} not found', 
                                       'TYPENAMES')
            layer_name_for_mapserver = collection_id
        
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
        
        wfs_version = params.get('VERSION') or params.get('version', '2.0.0')
        mapserver_params = {
            'map': mapfile_path,
            'SERVICE': 'WFS',
            'VERSION': wfs_version,
            'REQUEST': 'DescribeFeatureType',
        }
        if wfs_version.startswith('2.'):
            mapserver_params['TYPENAMES'] = layer_name_for_mapserver
        else:
            mapserver_params['TYPENAME'] = layer_name_for_mapserver
        
        # Ajouter OUTPUTFORMAT si présent
        outputformat = params.get('OUTPUTFORMAT') or params.get('outputformat')
        if outputformat:
            mapserver_params['OUTPUTFORMAT'] = outputformat
        
        log.debug(f"Proxying DescribeFeatureType to MapServer: {mapserver_url}/wfs with params {mapserver_params}")
        
        # Faire la requête à MapServer
        mapserver_response = requests.get(f"{mapserver_url}/wfs", params=mapserver_params, timeout=30)
        
        if mapserver_response.status_code != 200:
            log.error(f"MapServer returned status {mapserver_response.status_code} for DescribeFeatureType")
            # Essayer de parser l'erreur MapServer
            try:
                error_xml = ET.fromstring(mapserver_response.content)
                error_msg = "Unknown MapServer error"
                for elem in error_xml.iter():
                    if elem.tag.endswith('ExceptionText') or elem.text:
                        error_msg = elem.text or error_msg
                        break
                return _create_wfs_exception('NoApplicableCode', 
                                           f'MapServer error: {error_msg}', 
                                           'DescribeFeatureType')
            except Exception:
                return _create_wfs_exception('NoApplicableCode', 
                                           f'MapServer returned status {mapserver_response.status_code}', 
                                           'DescribeFeatureType')
        
        # Vérifier et corriger le XML si nécessaire
        xml_content = mapserver_response.content
        try:
            # Vérifier que le XML est valide
            ET.fromstring(xml_content)
        except ET.ParseError as parse_err:
            log.warning(f"XML parsing error for {collection_id}: {parse_err}, attempting to fix...")
            # Essayer de corriger les problèmes XML
            xml_content = _fix_xml_invalid_tag_names(xml_content)
            # Vérifier à nouveau après correction
            try:
                ET.fromstring(xml_content)
                log.info(f"XML fixed successfully for {collection_id}")
            except ET.ParseError as parse_err2:
                log.error(f"XML still invalid after fix for {collection_id}: {parse_err2}")
                return _create_wfs_exception('NoApplicableCode', 
                                           f'Invalid XML response from MapServer: {str(parse_err2)}', 
                                           'DescribeFeatureType')
        
        # Retourner la réponse MapServer directement
        content_type = mapserver_response.headers.get('Content-Type', 'application/xml')
        if not content_type or 'xml' not in content_type.lower():
            content_type = 'application/xml; charset=utf-8'
        
        return Response(
            xml_content,
            mimetype=content_type,
            headers={
                'Content-Type': f'{content_type}; charset=utf-8',
                'Content-Disposition': 'inline',  # Permet l'affichage dans le navigateur
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
        
    except Exception as e:
        log.error(f"Error in WFS DescribeFeatureType direct: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', str(e), 'DescribeFeatureType')


def _handle_wfs_getfeature(org_id: str, collections: List[Dict], params: Dict) -> Response:
    """
    Gère les requêtes WFS GetFeature
    
    Args:
        org_id: ID de l'organisation
        collections: Liste des collections de l'organisation
        params: Paramètres de la requête WFS
        
    Returns:
        Réponse avec les features en GML ou GeoJSON
    """
    try:
        # Récupérer les paramètres WFS
        typenames = params.get('TYPENAMES') or params.get('TYPENAME') or params.get('typenames') or params.get('typename')
        # IMPORTANT: Pour WFS, le format par défaut doit être GML XML, pas JSON
        # QGIS s'attend à recevoir du XML GML pour WFS GetFeature
        outputformat = params.get('OUTPUTFORMAT') or params.get('outputformat', 'application/gml+xml')
        # QGIS utilise souvent MAXFEATURES au lieu de COUNT
        count = params.get('COUNT') or params.get('count') or params.get('MAXFEATURES') or params.get('maxFeatures')
        startindex = params.get('STARTINDEX') or params.get('startindex', '0')
        bbox = params.get('BBOX') or params.get('bbox')
        srsname = params.get('SRSNAME') or params.get('srsname') or params.get('SRS') or params.get('srs', 'EPSG:4326')
        
        # Trouver la collection demandée
        collection = None
        if typenames:
            for col in collections:
                if col.get('id') == typenames:
                    collection = col
                    break
        
        if not collection:
            available_collections = [col.get('id') for col in collections]
            log.warning(f"TypeName '{typenames}' not found in organization collections. Available: {available_collections}")
            return _create_wfs_exception('InvalidParameterValue', 
                                       f'Feature type {typenames} not found', 
                                       'TYPENAMES')
        
        collection_id = collection.get('id')
        
        # TOUTES les requêtes WFS passent par MapServer (pas pygeoapi)
        # MapServer est la source unique pour WFS/WMS
        # Utiliser l'URL interne du conteneur MapServer
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
        
        # Vérifier si le mapfile existe via une requête test à MapServer
        # Cette méthode est plus fiable que de vérifier le système de fichiers local
        mapfile_exists = False
        try:
            test_params = {'map': mapfile_path, 'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetCapabilities'}
            test_response = requests.get(f"{mapserver_url}/wfs", params=test_params, timeout=10)
            if test_response.status_code == 200 and 'text/xml' in test_response.headers.get('Content-Type', '').lower():
                # Vérifier que la réponse contient bien des capacités WFS (pas une erreur)
                if b'wfs:WFS_Capabilities' in test_response.content or b'WFS_Capabilities' in test_response.content:
                    mapfile_exists = True
                    log.info(f"Mapfile exists for {collection_id}")
        except requests.exceptions.RequestException as e:
            log.warning(f"Could not verify mapfile existence for {collection_id}: {e}")
        
        if not mapfile_exists:
            # Le mapfile n'existe pas, essayer de le générer
            log.warning(f"Mapfile not found for {collection_id} (path: {mapfile_path}), attempting to generate...")
            try:
                from ckanext.ogc.plugin import OGCPlugin
                plugin = OGCPlugin()
                plugin._generate_mapfile_async(collection_id)
                log.info(f"Mapfile generation triggered for {collection_id}")
                
                # Attendre que le mapfile soit généré (avec plusieurs tentatives)
                import time
                max_wait = 30  # Attendre jusqu'à 30 secondes
                wait_interval = 2  # Vérifier toutes les 2 secondes
                waited = 0
                while waited < max_wait:
                    time.sleep(wait_interval)
                    waited += wait_interval
                    # Vérifier à nouveau via MapServer
                    try:
                        test_response = requests.get(f"{mapserver_url}/wfs", params=test_params, timeout=5)
                        if test_response.status_code == 200 and 'text/xml' in test_response.headers.get('Content-Type', '').lower():
                            if b'wfs:WFS_Capabilities' in test_response.content or b'WFS_Capabilities' in test_response.content:
                                log.debug(f"Mapfile created successfully after {waited}s: {mapfile_path}")
                                mapfile_exists = True
                                break
                    except Exception:
                        pass
                
                if not mapfile_exists:
                    log.warning(f"Mapfile still not found after {max_wait}s generation attempt: {mapfile_path}")
                    return _create_wfs_exception('NoApplicableCode', 
                                               f'MapServer error: msLoadMap(): Unable to access file. ({mapfile_path}). Mapfile generation may still be in progress.',
                                               'GetFeature')
            except Exception as gen_error:
                log.error(f"Failed to generate mapfile for {collection_id}: {gen_error}")
                import traceback
                log.error(traceback.format_exc())
                return _create_wfs_exception('NoApplicableCode', 
                                           f'MapServer error: msLoadMap(): Unable to access file. ({mapfile_path})',
                                           'GetFeature')
        
        # Construire l'URL MapServer WFS
        # MapServer attend les paramètres dans l'URL, pas dans le body
        # MapServer n'accepte pas 'application/gml+xml' seul, il faut utiliser 'application/gml+xml; version=3.2' ou 'text/xml; subtype=gml/3.2.1'
        mapserver_outputformat = outputformat
        if outputformat:
            outputformat_lower = outputformat.lower()
            if outputformat_lower == 'application/gml+xml':
                # Convertir vers un format accepté par MapServer
                mapserver_outputformat = 'application/gml+xml; version=3.2'
            elif 'application/gml+xml' in outputformat_lower and 'version' not in outputformat_lower:
                # Ajouter la version si manquante
                mapserver_outputformat = 'application/gml+xml; version=3.2'
            elif outputformat_lower in ['gml', 'xml']:
                mapserver_outputformat = 'text/xml; subtype=gml/3.2.1'
        else:
            # Par défaut, utiliser un format accepté par MapServer
            mapserver_outputformat = 'text/xml; subtype=gml/3.2.1'
        
        # Construire les paramètres MapServer WFS
        # MapServer WFS 2.0.0 attend des paramètres spécifiques
        wfs_version = params.get('VERSION') or params.get('version', '2.0.0')
        
        mapserver_params = {
            'map': mapfile_path,
            'SERVICE': 'WFS',
            'VERSION': wfs_version,
            'REQUEST': 'GetFeature',
            'OUTPUTFORMAT': mapserver_outputformat,
        }
        
        # WFS 2.0.0 utilise TYPENAMES (pluriel), WFS 1.0/1.1 utilise TYPENAME (singulier)
        # MapServer accepte généralement les deux, mais utilisons le bon selon la version
        if wfs_version.startswith('2.'):
            mapserver_params['TYPENAMES'] = collection_id  # Le nom du dataset correspond au nom de la couche dans le mapfile
        else:
            mapserver_params['TYPENAME'] = collection_id  # WFS 1.0/1.1 utilise TYPENAME
        
        # Ajouter COUNT si présent (MapServer utilise COUNT, pas MAXFEATURES)
        # MapServer attend COUNT comme entier, pas comme chaîne
        if count:
            try:
                count_int = int(count)
                if count_int > 0:
                    mapserver_params['COUNT'] = str(count_int)
            except (ValueError, TypeError):
                log.warning(f"Invalid COUNT parameter: {count}, ignoring")
        
        # Ajouter STARTINDEX si présent (pour la pagination)
        # MapServer attend STARTINDEX comme entier, pas comme chaîne
        if startindex and startindex != '0':
            try:
                startindex_int = int(startindex)
                if startindex_int > 0:
                    mapserver_params['STARTINDEX'] = str(startindex_int)
            except (ValueError, TypeError):
                log.warning(f"Invalid STARTINDEX parameter: {startindex}, ignoring")
        
        # Ajouter BBOX si présent
        # MapServer attend BBOX au format: minx,miny,maxx,maxy[,srs]
        if bbox:
            # Vérifier que BBOX est bien formaté
            bbox_parts = bbox.split(',')
            if len(bbox_parts) >= 4:
                try:
                    # Valider que ce sont des nombres
                    [float(x) for x in bbox_parts[:4]]
                    # Si SRSNAME est présent et différent de celui dans BBOX, l'ajouter
                    if len(bbox_parts) == 4 and srsname:
                        mapserver_params['BBOX'] = f"{bbox},{srsname}"
                    else:
                        mapserver_params['BBOX'] = bbox
                except ValueError:
                    log.warning(f"Invalid BBOX format: {bbox}, ignoring")
            else:
                log.warning(f"Invalid BBOX format (expected minx,miny,maxx,maxy): {bbox}, ignoring")
        
        # Ajouter SRSNAME si présent (MapServer utilise SRSNAME pour WFS)
        # SRSNAME doit être au format EPSG:XXXX
        if srsname:
            # Normaliser le format SRSNAME (EPSG:XXXX)
            if srsname.upper().startswith('EPSG:'):
                mapserver_params['SRSNAME'] = srsname.upper()
            elif srsname.upper().startswith('EPSG'):
                mapserver_params['SRSNAME'] = srsname.upper().replace('EPSG', 'EPSG:', 1)
            else:
                # Essayer d'ajouter EPSG: si ce n'est qu'un nombre
                try:
                    int(srsname)
                    mapserver_params['SRSNAME'] = f'EPSG:{srsname}'
                except ValueError:
                    log.warning(f"Invalid SRSNAME format: {srsname}, using default EPSG:4326")
                    mapserver_params['SRSNAME'] = 'EPSG:4326'
        
        log.debug(f"Proxying WFS GetFeature to MapServer for {collection_id}")
        log.info(f"MapServer URL: {mapserver_url}/wfs")
        log.info(f"MapServer params: {mapserver_params}")
        
        # Augmenter le timeout pour les grandes requêtes (QGIS peut demander beaucoup de features)
        mapserver_response = requests.get(f"{mapserver_url}/wfs", params=mapserver_params, timeout=300)
        
        # Vérifier si la réponse est une erreur HTTP
        if mapserver_response.status_code != 200:
            # Logger le contenu de l'erreur pour debug
            error_content = mapserver_response.content[:2000] if mapserver_response.content else "No content"
            log.error(f"MapServer WFS error {mapserver_response.status_code}")
            log.error(f"Request params: {mapserver_params}")
            log.error(f"Error content (first 2000 chars): {error_content}")
            
            # Essayer d'extraire le message d'erreur si c'est du XML
            error_msg = f"MapServer WFS error: {mapserver_response.status_code}"
            if mapserver_response.content and mapserver_response.content.startswith(b'<?xml'):
                if b'ServiceExceptionReport' in mapserver_response.content:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(mapserver_response.content)
                        # Chercher dans le namespace OGC
                        for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                            error_text = exception.text or exception.get('code', '')
                            if error_text:
                                error_msg = error_text.strip()
                                break
                        # Si pas trouvé, chercher sans namespace
                        if error_msg == f"MapServer WFS error: {mapserver_response.status_code}":
                            for exception in root.findall('.//ServiceException'):
                                error_text = exception.text or exception.get('code', '')
                                if error_text:
                                    error_msg = error_text.strip()
                                    break
                    except Exception as parse_error:
                        log.warning(f"Could not parse ServiceExceptionReport: {parse_error}")
                # Essayer d'extraire le message d'erreur HTML si c'est du HTML
                elif b'<HTML>' in mapserver_response.content or b'<html>' in mapserver_response.content:
                    try:
                        import re
                        # Chercher le message d'erreur dans le HTML
                        match = re.search(r'<BODY[^>]*>(.*?)</BODY>', mapserver_response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                        if match:
                            error_msg = match.group(1).strip()[:500]  # Limiter à 500 caractères
                        else:
                            # Chercher dans le titre ou le contenu
                            match = re.search(r'<TITLE[^>]*>(.*?)</TITLE>', mapserver_response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                            if match:
                                error_msg = match.group(1).strip()
                    except Exception as parse_error:
                        log.warning(f"Could not parse HTML error: {parse_error}")
            
            # Si c'est une erreur 400, essayer de donner plus de détails
            if mapserver_response.status_code == 400:
                error_msg = f"Bad Request (400): {error_msg}. Check that the mapfile exists and parameters are valid."
            
            return _create_wfs_exception('NoApplicableCode', error_msg, 'GetFeature')
        
        # Vérifier si la réponse est une erreur (HTML ou XML ServiceExceptionReport)
        content_type = mapserver_response.headers.get('Content-Type', '').lower()
        is_html_error = 'text/html' in content_type or mapserver_response.content.startswith(b'<HTML>') or mapserver_response.content.startswith(b'<html>')
        is_xml_error = mapserver_response.content.startswith(b'<?xml') and b'ServiceExceptionReport' in mapserver_response.content
        
        if is_html_error:
            # C'est une erreur HTML de MapServer, extraire le message
            error_msg = "MapServer error"
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(mapserver_response.content, 'html.parser')
                body = soup.find('body')
                if body:
                    error_msg = body.get_text(strip=True)
            except Exception:
                import re
                match = re.search(r'<BODY[^>]*>(.*?)</BODY>', mapserver_response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                if match:
                    error_msg = match.group(1).strip()
            
            log.error(f"MapServer returned HTML error: {error_msg}")
            return _create_wfs_exception('NoApplicableCode', 
                                       f'MapServer error: {error_msg}',
                                       'GetFeature')
        
        if is_xml_error:
            # C'est une erreur XML ServiceExceptionReport de MapServer
            error_msg = "MapServer error"
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(mapserver_response.content)
                # Chercher le message d'erreur dans ServiceExceptionReport
                for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                    error_msg = exception.text or "Unknown error"
                    break
                if error_msg == "MapServer error":
                    # Essayer sans namespace
                    for exception in root.findall('.//ServiceException'):
                        error_msg = exception.text or "Unknown error"
                        break
            except Exception as parse_error:
                log.warning(f"Could not parse ServiceExceptionReport: {parse_error}")
                # Essayer d'extraire le texte brut
                try:
                    import re
                    match = re.search(r'<ServiceException[^>]*>(.*?)</ServiceException>', mapserver_response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                    if match:
                        error_msg = match.group(1).strip()
                except Exception:
                    pass
            
            log.error(f"MapServer returned XML error: {error_msg}")
            return _create_wfs_exception('NoApplicableCode', 
                                       f'MapServer error: {error_msg}',
                                       'GetFeature')
        
        # Post-traiter le XML pour corriger les noms de balises invalides (colonnes avec espaces)
        xml_content = _fix_xml_invalid_tag_names(mapserver_response.content)
        
        # Vérifier que le contenu XML est valide et contient des features
        # Si le XML est vide ou ne contient pas de features, c'est peut-être une erreur
        try:
            import xml.etree.ElementTree as ET
            xml_root = ET.fromstring(xml_content)
            # Vérifier si c'est un ServiceExceptionReport (erreur)
            if xml_root.tag.endswith('ServiceExceptionReport') or 'ServiceExceptionReport' in xml_root.tag:
                error_msg = "MapServer error"
                for exception in xml_root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                    error_msg = exception.text or "Unknown error"
                    break
                if error_msg == "MapServer error":
                    for exception in xml_root.findall('.//ServiceException'):
                        error_msg = exception.text or "Unknown error"
                        break
                log.error(f"MapServer returned XML error in response: {error_msg}")
                return _create_wfs_exception('NoApplicableCode', f'MapServer error: {error_msg}', 'GetFeature')
        except ET.ParseError:
            # Si le XML n'est pas valide, c'est peut-être une erreur HTML
            log.warning(f"Invalid XML response from MapServer, returning as-is")
        except Exception as e:
            log.warning(f"Error checking XML content: {e}")
        
        # Retourner la réponse (post-traitée ou originale)
        log.info(f"MapServer WFS response: {len(xml_content)} bytes")
        
        # Utiliser le content-type de MapServer si disponible, sinon utiliser application/gml+xml
        response_content_type = mapserver_response.headers.get('Content-Type', 'application/gml+xml')
        if 'text/html' not in response_content_type.lower():
            # Utiliser le content-type de MapServer pour préserver le format exact
            final_content_type = response_content_type if 'charset' in response_content_type else f'{response_content_type}; charset=utf-8'
        else:
            # Fallback si c'est du HTML (ne devrait pas arriver ici)
            final_content_type = 'application/gml+xml; charset=utf-8'
        
        return Response(
            xml_content,
            mimetype=final_content_type.split(';')[0],
            headers={
                'Content-Type': final_content_type,
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*'
            }
        )
            
    except Exception as e:
        log.error(f"Error in WFS GetFeature: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', str(e), 'GetFeature')


def _handle_wfs_describefeaturetype(org_id: str, collections: List[Dict], params: Dict) -> Response:
    """
    Gère les requêtes WFS DescribeFeatureType
    
    Args:
        org_id: ID de l'organisation
        collections: Liste des collections de l'organisation
        params: Paramètres de la requête WFS
        
    Returns:
        Réponse XML avec le schéma de la feature type
    """
    try:
        # Récupérer les paramètres WFS
        typename = params.get('TYPENAME') or params.get('TYPENAMES') or params.get('typename') or params.get('typenames')
        
        if not typename:
            return _create_wfs_exception('MissingParameterValue', 
                                       'TYPENAME parameter is required',
                                       'DescribeFeatureType')
        
        # Trouver la collection demandée
        collection = None
        for col in collections:
            if col.get('id') == typename:
                collection = col
                break
        
        if not collection:
            return _create_wfs_exception('InvalidParameterValue',
                                       f'Feature type {typename} not found',
                                       'TYPENAME')
        
        collection_id = collection.get('id')
        collection_title = collection.get('title', collection_id)
        
        # Récupérer un exemple de feature pour déterminer le schéma
        pygeoapi_url = get_pygeoapi_url()
        items_url = f"{pygeoapi_url}/collections/{collection_id}/items"
        items_params = {'limit': 1}
        
        items_response = requests.get(items_url, params=items_params, timeout=10)
        
        # Déterminer les propriétés depuis le schéma ou un exemple
        properties = {}
        if items_response.status_code == 200:
            items_data = items_response.json()
            features = items_data.get('features', [])
            if features:
                props = features[0].get('properties', {})
                for key, value in props.items():
                    # Déterminer le type XSD
                    if isinstance(value, bool):
                        xsd_type = 'xsd:boolean'
                    elif isinstance(value, int):
                        xsd_type = 'xsd:integer'
                    elif isinstance(value, float):
                        xsd_type = 'xsd:double'
                    elif isinstance(value, str):
                        xsd_type = 'xsd:string'
                    else:
                        xsd_type = 'xsd:string'
                    properties[key] = xsd_type
        
        # Générer le schéma XML
        schema_xml = _generate_feature_type_schema(collection_id, collection_title, properties)
        
        return Response(
            schema_xml,
            mimetype='application/xml',
            headers={
                'Content-Type': 'application/xml; charset=utf-8',
                'Content-Disposition': 'inline',  # Permet l'affichage dans le navigateur
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
        
    except Exception as e:
        log.error(f"Error in WFS DescribeFeatureType: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', str(e), 'DescribeFeatureType')


def _features_to_gml(features: List[Dict], collection_id: str, 
                     number_matched: int, number_returned: int, 
                     srsname: str = 'EPSG:4326', collection_bbox: Optional[Tuple[float, float, float, float]] = None) -> str:
    """
    Convertit une liste de features GeoJSON en GML XML
    
    Args:
        features: Liste de features GeoJSON
        collection_id: ID de la collection
        number_matched: Nombre total de features correspondantes
        number_returned: Nombre de features retournées
        srsname: CRS (ex: EPSG:4326)
        collection_bbox: BBOX de la collection complète (minx, miny, maxx, maxy) - optionnel
        
    Returns:
        XML GML string
    """
    try:
        # Créer la racine XML
        root = ET.Element('wfs:FeatureCollection')
        root.set('xmlns:wfs', 'http://www.opengis.net/wfs/2.0')
        root.set('xmlns:gml', 'http://www.opengis.net/gml/3.2')
        root.set('xmlns:dataizen', 'http://dataizen.eu/wfs')
        root.set('numberMatched', str(number_matched))
        root.set('numberReturned', str(number_returned))
        root.set('timeStamp', '2025-01-01T00:00:00Z')
        
        # Utiliser le BBOX de la collection si fourni, sinon calculer à partir des features
        if collection_bbox:
            # Utiliser le BBOX de la collection (emprise complète)
            minx, miny, maxx, maxy = collection_bbox
            has_geometry = True
        else:
            # Calculer le BBOX global à partir de toutes les features retournées
            minx, miny, maxx, maxy = 180, 90, -180, -90
            has_geometry = False
            
            for feature in features:
                geometry = feature.get('geometry')
                if geometry and geometry.get('type'):
                    coords = geometry.get('coordinates', [])
                    if geometry.get('type') == 'Point' and len(coords) >= 2:
                        lon, lat = coords[0], coords[1]
                        minx = min(minx, lon)
                        miny = min(miny, lat)
                        maxx = max(maxx, lon)
                        maxy = max(maxy, lat)
                        has_geometry = True
                    elif geometry.get('type') == 'LineString' and coords:
                        for coord in coords:
                            if len(coord) >= 2:
                                lon, lat = coord[0], coord[1]
                                minx = min(minx, lon)
                                miny = min(miny, lat)
                                maxx = max(maxx, lon)
                                maxy = max(maxy, lat)
                                has_geometry = True
                    elif geometry.get('type') == 'Polygon' and coords:
                        for ring in coords:
                            for coord in ring:
                                if len(coord) >= 2:
                                    lon, lat = coord[0], coord[1]
                                    minx = min(minx, lon)
                                    miny = min(miny, lat)
                                    maxx = max(maxx, lon)
                                    maxy = max(maxy, lat)
                                    has_geometry = True
        
        # Ajouter le BBOX global au FeatureCollection si on a des géométries
        if has_geometry:
            # Utiliser l'emprise calculée ou une emprise par défaut si invalide
            if minx > maxx or miny > maxy:
                # Emprise invalide, utiliser une emprise par défaut pour la France
                minx, miny, maxx, maxy = -5.0, 41.0, 10.0, 51.0
            bounded_by = ET.SubElement(root, 'gml:boundedBy')
            envelope = ET.SubElement(bounded_by, 'gml:Envelope')
            envelope.set('srsName', f'urn:ogc:def:crs:EPSG::{srsname.split(":")[-1] if ":" in srsname else "4326"}')
            lower_corner = ET.SubElement(envelope, 'gml:lowerCorner')
            lower_corner.text = f'{minx} {miny}'
            upper_corner = ET.SubElement(envelope, 'gml:upperCorner')
            upper_corner.text = f'{maxx} {maxy}'
        
        # Ajouter chaque feature
        for idx, feature in enumerate(features):
            member = ET.SubElement(root, 'wfs:member')
            feature_elem = ET.SubElement(member, f'dataizen:{collection_id}')
            feature_elem.set('gml:id', f'{collection_id}.{feature.get("id", idx)}')
            
            # Ajouter la géométrie
            geometry = feature.get('geometry')
            if geometry and geometry.get('type'):
                geom_elem = ET.SubElement(feature_elem, 'dataizen:geometry')
                geom_type = geometry.get('type')
                coords = geometry.get('coordinates', [])
                
                if geom_type == 'Point' and len(coords) >= 2:
                    point_elem = ET.SubElement(geom_elem, 'gml:Point')
                    point_elem.set('srsName', f'urn:ogc:def:crs:EPSG::{srsname.split(":")[-1] if ":" in srsname else "4326"}')
                    pos_elem = ET.SubElement(point_elem, 'gml:pos')
                    pos_elem.text = f'{coords[0]} {coords[1]}'
                elif geom_type == 'LineString' and coords:
                    line_elem = ET.SubElement(geom_elem, 'gml:LineString')
                    line_elem.set('srsName', f'urn:ogc:def:crs:EPSG::{srsname.split(":")[-1] if ":" in srsname else "4326"}')
                    pos_list = ET.SubElement(line_elem, 'gml:posList')
                    pos_list.text = ' '.join([f'{c[0]} {c[1]}' for c in coords if len(c) >= 2])
                elif geom_type == 'Polygon' and coords:
                    polygon_elem = ET.SubElement(geom_elem, 'gml:Polygon')
                    polygon_elem.set('srsName', f'urn:ogc:def:crs:EPSG::{srsname.split(":")[-1] if ":" in srsname else "4326"}')
                    exterior = ET.SubElement(polygon_elem, 'gml:exterior')
                    linear_ring = ET.SubElement(exterior, 'gml:LinearRing')
                    pos_list = ET.SubElement(linear_ring, 'gml:posList')
                    ring_coords = coords[0] if coords else []
                    pos_list.text = ' '.join([f'{c[0]} {c[1]}' for c in ring_coords if len(c) >= 2])
            
            # Ajouter les propriétés
            properties = feature.get('properties', {})
            for key, value in properties.items():
                if value is not None and not key.startswith('_'):
                    prop_elem = ET.SubElement(feature_elem, f'dataizen:{key}')
                    # Échapper les caractères XML dans les valeurs de texte
                    prop_elem.text = html.escape(str(value)) if value is not None else ''
        
        # Convertir en string XML
        ET.indent(root, space='  ')
        xml_str = ET.tostring(root, encoding='unicode', xml_declaration=True)
        return xml_str
        
    except Exception as e:
        log.error(f"Error converting features to GML: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', str(e), 'GetFeature')


def _generate_feature_type_schema(collection_id: str, collection_title: str, 
                                  properties: Dict[str, str]) -> str:
    """
    Génère un schéma XML DescribeFeatureType pour une collection
    
    Args:
        collection_id: ID de la collection
        collection_title: Titre de la collection
        properties: Dictionnaire des propriétés avec leurs types XSD
        
    Returns:
        XML schema string
    """
    try:
        root = ET.Element('xsd:schema')
        root.set('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema')
        root.set('xmlns:gml', 'http://www.opengis.net/gml/3.2')
        root.set('xmlns:dataizen', 'http://dataizen.eu/wfs')
        root.set('targetNamespace', 'http://dataizen.eu/wfs')
        root.set('elementFormDefault', 'qualified')
        
        # Importer GML
        import_elem = ET.SubElement(root, 'xsd:import')
        import_elem.set('namespace', 'http://www.opengis.net/gml/3.2')
        import_elem.set('schemaLocation', 'http://schemas.opengis.net/gml/3.2.1/gml.xsd')
        
        # Définir le type complexe
        complex_type = ET.SubElement(root, 'xsd:complexType')
        complex_type.set('name', f'{collection_id}Type')
        
        complex_content = ET.SubElement(complex_type, 'xsd:complexContent')
        extension = ET.SubElement(complex_content, 'xsd:extension')
        extension.set('base', 'gml:AbstractFeatureType')
        
        sequence = ET.SubElement(extension, 'xsd:sequence')
        
        # Ajouter la géométrie
        geom_elem = ET.SubElement(sequence, 'xsd:element')
        geom_elem.set('name', 'geometry')
        geom_elem.set('type', 'gml:GeometryPropertyType')
        geom_elem.set('minOccurs', '0')
        geom_elem.set('maxOccurs', '1')
        
        # Ajouter les propriétés
        for prop_name, prop_type in properties.items():
            if not prop_name.startswith('_'):
                prop_elem = ET.SubElement(sequence, 'xsd:element')
                prop_elem.set('name', prop_name)
                prop_elem.set('type', prop_type)
                prop_elem.set('minOccurs', '0')
                prop_elem.set('maxOccurs', '1')
        
        # Définir l'élément
        element = ET.SubElement(root, 'xsd:element')
        element.set('name', collection_id)
        element.set('type', f'dataizen:{collection_id}Type')
        element.set('substitutionGroup', 'gml:AbstractFeature')
        
        ET.indent(root, space='  ')
        xml_str = ET.tostring(root, encoding='unicode', xml_declaration=True)
        return xml_str
        
    except Exception as e:
        log.error(f"Error generating feature type schema: {e}")
        import traceback
        log.error(traceback.format_exc())
        return _create_wfs_exception('NoApplicableCode', str(e), 'DescribeFeatureType')



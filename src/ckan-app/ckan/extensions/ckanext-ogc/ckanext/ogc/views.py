"""
Flask Blueprint pour les endpoints OGC par organisation
"""
from typing import Any, Dict, List, Optional, Tuple
from flask import Blueprint, request, jsonify, Response
from werkzeug.wrappers import Response as WerkzeugResponse
import logging
import os
import re
import requests
import json
import xml.etree.ElementTree as ET
import html
from ckan.plugins import toolkit
import ckanext.ogc.helpers as ogc_helpers

try:
    from pygeoapi import util as pygeoapi_util
except Exception:  # pragma: no cover - pygeoapi may not be importable in some contexts
    pygeoapi_util = None

log = logging.getLogger(__name__)

# Utilitaires, helpers WFS et WMS déplacés dans des sous-modules dédiés
from ckanext.ogc.views_utils import (
    _safe_response,
    _fix_xml_invalid_tag_names,
    _normalize_crs_to_uri,
    _normalize_crs_identifier,
    _normalize_srsname,
    _create_wfs_exception,
    _get_org_collections,
    _get_dataset_bbox,
    _transform_bbox_to_wgs84,
    get_pygeoapi_url,
    get_ckan_api_key,
)
from ckanext.ogc.views_wfs import (
    _generate_wfs_capabilities,
    _handle_wfs_getfeature_direct,
    _handle_wfs_describefeaturetype_direct,
    _handle_wfs_getfeature,
    _handle_wfs_describefeaturetype,
    _features_to_gml,
    _generate_feature_type_schema,
)
from ckanext.ogc.views_wms import (
    _validate_mapfile_for_wms,
    _replace_mapserver_url_in_getcapabilities,
    _rewrite_wms_getcapabilities_for_ckan,
    _find_dataset_for_layer_in_org,
    _generate_wms_capabilities,
)

ogc = Blueprint('ogc', __name__, url_prefix='/maps')
# WMS blueprint pour proxy vers MapServer
wms = Blueprint('wms', __name__, url_prefix='/wms')
# WFS blueprint pour proxy vers MapServer
wfs = Blueprint('wfs', __name__, url_prefix='/wfs')

# CSW blueprint pour le service de catalogue
try:
    from ckanext.ogc.csw_views import csw
except ImportError:
    log.warning("CSW views non disponibles (pycsw peut ne pas être installé)")
    csw = None

if pygeoapi_util and not getattr(pygeoapi_util, '_ckan_epsg_patch', False):
    original_get_crs_from_uri = pygeoapi_util.get_crs_from_uri

    def _patched_get_crs_from_uri(crs_uri):
        if isinstance(crs_uri, str):
            trimmed = crs_uri.strip()
            upper_value = trimmed.upper()
            if upper_value.startswith("EPSG:"):
                epsg_code = upper_value.split(":")[1]
                trimmed = f"http://www.opengis.net/def/crs/EPSG/0/{epsg_code}"
            elif upper_value.startswith("URN:OGC:DEF:CRS:EPSG::"):
                epsg_code = upper_value.split("::")[-1]
                trimmed = f"http://www.opengis.net/def/crs/EPSG/0/{epsg_code}"
            crs_uri = trimmed
        return original_get_crs_from_uri(crs_uri)

    pygeoapi_util.get_crs_from_uri = _patched_get_crs_from_uri
    pygeoapi_util._ckan_epsg_patch = True


@ogc.route('/<org_id>/collections/<collection_id>/items')
def ogc_collection_items(org_id, collection_id):
    """
    Endpoint pour accéder aux items d'une collection en GeoJSON via le Blueprint CKAN
    IMPORTANT: Pour les datasets géospatiaux, on redirige vers WFS MapServer au lieu de pygeoapi
    MapServer est la source unique pour toutes les données géospatiales (WFS/WMS)
    
    Args:
        org_id: ID de l'organisation CKAN
        collection_id: ID de la collection (nom du dataset CKAN)
        
    Returns:
        GeoJSON FeatureCollection depuis MapServer (via WFS) ou pygeoapi (pour les datasets non-géospatiaux)
    """
    try:
        # Vérifier que la collection appartient à l'organisation
        context = {'ignore_auth': True, 'use_cache': False}
        organization = None
        
        try:
            organization = toolkit.get_action('organization_show')(context, {'id': org_id})
        except Exception as e:
            log.warning(f"Error fetching organization {org_id}: {e}")
            return jsonify({'error': 'Organization not found', 'message': str(e)}), 404
        
        # Vérifier que le dataset existe et appartient à l'organisation
        try:
            dataset = toolkit.get_action('package_show')(context, {'id': collection_id})
            dataset_org = dataset.get('organization')
            if not dataset_org or dataset_org.get('name') != organization.get('name'):
                return jsonify({
                    'error': 'Collection not found in organization',
                    'message': f'Collection {collection_id} does not belong to organization {org_id}'
                }), 404
        except Exception as e:
            log.error(f"Error fetching dataset {collection_id}: {e}")
            return jsonify({'error': 'Collection not found', 'message': str(e)}), 404
        
        # Vérifier si le dataset a un mapfile MapServer (donc géospatial)
        # Si oui, rediriger vers WFS MapServer au lieu de pygeoapi
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
        
        # Vérifier si le mapfile existe (via une requête test à MapServer)
        try:
            test_response = requests.get(
                f"{mapserver_url}/wfs",
                params={
                    'map': mapfile_path,
                    'SERVICE': 'WFS',
                    'VERSION': '2.0.0',
                    'REQUEST': 'GetCapabilities'
                },
                timeout=5
            )
            if test_response.status_code == 200:
                # Le mapfile existe, utiliser MapServer WFS pour récupérer les données
                log.debug(f"Mapfile trouvé pour {collection_id}, utilisation de MapServer WFS au lieu de pygeoapi")
                
                # Convertir la requête OGC API Features en requête WFS GetFeature
                items_params = dict(request.args)
                limit = items_params.get('limit', '100')
                startindex = items_params.get('startindex', '0')
                bbox = items_params.get('bbox')
                
                # Construire la requête WFS GetFeature
                wfs_params = {
                    'map': mapfile_path,
                    'SERVICE': 'WFS',
                    'VERSION': '2.0.0',
                    'REQUEST': 'GetFeature',
                    'TYPENAMES': collection_id,
                    'OUTPUTFORMAT': 'application/json',  # GeoJSON pour OGC API Features
                    'COUNT': limit,
                    'STARTINDEX': startindex
                }
                
                if bbox:
                    wfs_params['BBOX'] = bbox
                
                wfs_response = requests.get(f"{mapserver_url}/wfs", params=wfs_params, timeout=60)
                
                if wfs_response.status_code == 200:
                    # MapServer retourne du GeoJSON si OUTPUTFORMAT=application/json
                    return Response(
                        wfs_response.content,
                        mimetype='application/geo+json',
                        headers={
                            'Content-Type': 'application/geo+json; charset=utf-8',
                            'Access-Control-Allow-Origin': '*'
                        }
                    )
                else:
                    log.warning(f"MapServer WFS error {wfs_response.status_code}, fallback vers pygeoapi")
        except Exception as e:
            log.warning(f"Erreur vérification mapfile pour {collection_id}: {e}, fallback vers pygeoapi")
        
        # Si pas de mapfile ou erreur, fallback vers pygeoapi (pour les datasets non-géospatiaux)
        pygeoapi_url = get_pygeoapi_url()
        items_url = f"{pygeoapi_url}/collections/{collection_id}/items"
        
        # Transférer tous les paramètres de la requête (bbox, limit, startindex, f, etc.)
        items_params = dict(request.args)
        
        log.debug(f"Proxying GeoJSON request to pygeoapi: {items_url} with params {items_params}")
        items_response = requests.get(items_url, params=items_params, timeout=60)
        
        if items_response.status_code != 200:
            log.error(f"Pygeoapi request failed: {items_response.status_code}, {items_response.text[:200]}")
            return jsonify({
                'error': 'Failed to fetch items',
                'message': f'Pygeoapi returned status {items_response.status_code}'
            }), items_response.status_code
        
        # Déterminer le Content-Type selon le format demandé
        format_param = items_params.get('f', 'json')
        if format_param.lower() == 'jsonld':
            content_type = 'application/ld+json'
        elif format_param.lower() == 'html':
            content_type = 'text/html'
        elif format_param.lower() == 'csv':
            content_type = 'text/csv'
        else:
            content_type = 'application/json'
        
        # Retourner la réponse de pygeoapi
        return Response(
            items_response.content,
            mimetype=content_type,
            headers={
                'Content-Type': f'{content_type}; charset=utf-8',
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        log.error(f"Error in collection items endpoint: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500


@ogc.route('/<org_id>')
def ogc_organization_endpoint(org_id):
    """
    Endpoint proxy OGC par organisation
    Redirige les requêtes WMS/WFS vers pygeoapi avec les collections de l'organisation
    
    Args:
        org_id: ID de l'organisation CKAN
        
    Returns:
        Réponse de pygeoapi ou erreur
    """
    try:
        # Récupérer les paramètres de la requête (utiliser to_dict() pour éviter les doublons)
        # QGIS peut parfois envoyer des paramètres dupliqués, on prend la première valeur
        request_args = request.args.to_dict(flat=False)
        service = None
        request_type = None
        if 'SERVICE' in request_args:
            service = request_args['SERVICE'][0] if isinstance(request_args['SERVICE'], list) else request_args['SERVICE']
        elif 'service' in request_args:
            service = request_args['service'][0] if isinstance(request_args['service'], list) else request_args['service']
        
        if 'REQUEST' in request_args:
            request_type = request_args['REQUEST'][0] if isinstance(request_args['REQUEST'], list) else request_args['REQUEST']
        elif 'request' in request_args:
            request_type = request_args['request'][0] if isinstance(request_args['request'], list) else request_args['request']
        
        # Si VERSION est présent mais pas SERVICE/REQUEST, supposer WFS GetCapabilities (comportement QGIS)
        if not service and not request_type:
            version = request_args.get('VERSION', request_args.get('version', ['']))
            if isinstance(version, list) and len(version) > 0:
                version = version[0]
            if version and ('2.0' in str(version) or '1.1' in str(version) or '1.0' in str(version)):
                # QGIS envoie parfois juste VERSION sans SERVICE/REQUEST, supposer WFS GetCapabilities
                service = 'WFS'
                request_type = 'GetCapabilities'
                log.debug(f"QGIS-style request detected: VERSION={version} without SERVICE/REQUEST, assuming WFS GetCapabilities")
        
        if not service or not request_type:
            return jsonify({
                'error': 'Missing SERVICE or REQUEST parameter',
                'message': 'Valid parameters: SERVICE=WMS|WFS, REQUEST=GetCapabilities|GetMap|GetFeature'
            }), 400
        
        # Nettoyer les paramètres pour éviter les doublons (QGIS peut envoyer des paramètres dupliqués)
        # Utiliser request.args.to_dict() qui gère mieux les doublons que request.args.items()
        clean_params = {}
        request_args_dict = request.args.to_dict(flat=False)
        for key, value_list in request_args_dict.items():
            # Prendre la première valeur si plusieurs valeurs sont présentes
            if isinstance(value_list, list) and len(value_list) > 0:
                clean_params[key.upper()] = value_list[0]  # Normaliser en majuscules
            elif value_list:
                clean_params[key.upper()] = value_list
        
        # Si c'est une requête GetCapabilities, on a besoin de toutes les collections
        if request_type.upper() == 'GETCAPABILITIES':
            log.debug(f"GetCapabilities request for org {org_id} - analyzing all datasets (this is normal)")
            org_collections, error = _get_org_collections(org_id)
            if error:
                return error
            log.debug(f"GetCapabilities: found {len(org_collections)} collections")
            
            if service.upper() == 'WMS':
                # WMS est maintenant fourni par MapServer
                return jsonify({
                    'error': 'WMS service moved',
                    'message': 'WMS is now provided by MapServer. Use /wms endpoint via reverse proxy.',
                    'info': 'For OGC API services, use /maps endpoint'
                }), 410  # 410 Gone - service moved
            elif service.upper() == 'WFS':
                return _generate_wfs_capabilities(org_id, org_collections)
            else:
                return jsonify({'error': 'Invalid SERVICE', 'message': f'Service {service} not supported'}), 400
        
        # Gérer les requêtes WFS GetFeature (SANS analyser toutes les collections)
        if service and service.upper() == 'WFS':
            log.debug(f"WFS request detected: {request_type}")
            
            if request_type and request_type.upper() == 'GETFEATURE':
                log.debug(f"GetFeature request for org {org_id} - SKIPPING full collection analysis (direct mode)")
                # Pour GetFeature, on n'a pas besoin d'analyser toutes les collections
                # On vérifie directement que le dataset appartient à l'organisation et on utilise MapServer
                return _handle_wfs_getfeature_direct(org_id, clean_params)
            elif request_type and request_type.upper() == 'DESCRIBEFEATURETYPE':
                log.debug(f"DescribeFeatureType request for org {org_id} - SKIPPING full collection analysis (direct mode)")
                # Pour DescribeFeatureType, on n'a pas besoin d'analyser toutes les collections
                # On vérifie directement que le dataset appartient à l'organisation et on utilise MapServer
                return _handle_wfs_describefeaturetype_direct(org_id, clean_params)
            else:
                log.warning(f"Unsupported WFS request: {request_type}")
                return jsonify({'error': 'Invalid REQUEST', 'message': f'Request {request_type} not supported for WFS'}), 400
        
        # WMS requests supprimées - WMS sera fourni par MapServer via /wms
        if service and service.upper() == 'WMS':
            return jsonify({
                'error': 'WMS service moved',
                'message': 'WMS is now provided by MapServer. Use /wms endpoint via reverse proxy.',
                'info': 'For OGC API services, use /maps endpoint'
            }), 410  # 410 Gone - service moved
        
        # Pour les autres requêtes, retourner une erreur
        return jsonify({
            'error': 'Unsupported request',
            'organization': org_id,
            'service': service,
            'request': request_type,
            'collections': [c.get('id') for c in org_collections]
        }), 400
        
    except Exception as e:
        log.error(f"Error in OGC endpoint: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500



@wms.route('', methods=['GET'])
@wms.route('/', methods=['GET'])
def wms_direct_proxy():
    """
    Proxy WMS direct vers MapServer
    Accepte les requêtes WMS avec le paramètre map directement dans l'URL
    Format: /wms?map=/mapserver/mapfiles/dataset.map&SERVICE=WMS&...
    """
    try:
        # Récupérer le paramètre map depuis la requête
        mapfile_path = request.args.get('map')
        
        if not mapfile_path:
            return jsonify({
                'error': 'Missing map parameter',
                'message': 'The "map" parameter is required. Format: /wms?map=/mapserver/mapfiles/dataset.map&SERVICE=WMS&...'
            }), 400
        
        # Vérifier que c'est bien un chemin de mapfile valide
        if not mapfile_path.startswith('/mapserver/mapfiles/') or not mapfile_path.endswith('.map'):
            return jsonify({
                'error': 'Invalid map parameter',
                'message': f'Mapfile path must be in format /mapserver/mapfiles/dataset.map, got: {mapfile_path}'
            }), 400
        
        # Récupérer le service (doit être WMS)
        service = request.args.get('SERVICE') or request.args.get('service', 'WMS')
        if service.upper() != 'WMS':
            return jsonify({
                'error': 'Invalid SERVICE',
                'message': f'This endpoint only supports WMS, got {service}'
            }), 400
        
        # Construire l'URL MapServer (utiliser l'URL interne du conteneur)
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        
        # Récupérer le type de requête pour la conversion LAYERS -> LAYER
        request_type = request.args.get('REQUEST') or request.args.get('request')
        
        # Copier tous les paramètres de la requête (map est déjà dedans)
        params = dict(request.args)
        # Normalisation casse des paramètres SLD (clients envoient souvent en minuscules)
        if 'sld_body' in params and 'SLD_BODY' not in params:
            params['SLD_BODY'] = params.pop('sld_body')
        if 'sld' in params and 'SLD' not in params:
            params['SLD'] = params.pop('sld')
        if 'sld_version' in params and 'SLD_VERSION' not in params:
            params['SLD_VERSION'] = params.pop('sld_version')
        # WMS : avec un SLD client, STYLES doit exister (vide) pour éviter le CLASS du mapfile
        if ('SLD_BODY' in params or 'SLD' in params) and 'STYLES' not in params and 'styles' not in params:
            params['STYLES'] = ''
            params.pop('styles', None)
        
        # Extraire le nom du dataset depuis le chemin du mapfile pour la gestion du SLD
        # Format: /mapserver/mapfiles/dataset_name.map -> dataset_name
        dataset_name = os.path.basename(mapfile_path).replace('.map', '')
        
        # Pour GetLegendGraphic, MapServer nécessite LAYER (singulier) et non LAYERS (pluriel)
        # Convertir LAYERS en LAYER pour GetLegendGraphic et retirer LAYERS
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            if 'LAYERS' in params:
                params['LAYER'] = params.pop('LAYERS')
            elif 'layers' in params:
                params['LAYER'] = params.pop('layers')
            # S'assurer que LAYER est défini (utiliser dataset_name si absent)
            if 'LAYER' not in params and 'layer' not in params:
                params['LAYER'] = dataset_name
                log.debug(f"GetLegendGraphic: LAYER défini à {dataset_name}")
            # Retirer LAYERS si présent (MapServer n'en a pas besoin pour GetLegendGraphic)
            params.pop('LAYERS', None)
            params.pop('layers', None)
        
        log.debug(f"========== GESTION SLD POUR {dataset_name} ==========")
        log.debug(f"Mapfile: {mapfile_path}")
        log.debug(f"REQUEST: {request.args.get('REQUEST', 'N/A')}")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Requête légende (proxy dataset) — LAYER={params.get('LAYER')}")
        
        # Récupérer SLD_BODY depuis les paramètres (pour la prévisualisation)
        sld_body = request.args.get('SLD_BODY') or request.args.get('sld_body')
        
        # Vérifier si NO_SLD est demandé (avant de traiter le SLD)
        no_sld = params.pop('NO_SLD', None) or params.pop('no_sld', None)
        
        if no_sld:
            log.info(f"SLD désactivé explicitement (NO_SLD=1) pour {dataset_name}")
        elif 'SLD_BODY' in params:
            sld_from_request = params.get('SLD_BODY', '')
            sld_size = len(sld_from_request) if isinstance(sld_from_request, str) else 0
            log.debug(f"SLD_BODY trouvé dans la requête pour {dataset_name}: {sld_size} caractères")
            if 'SLD_VERSION' not in params:
                params['SLD_VERSION'] = '1.0.0'
            if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                sld_style_name = ogc_helpers.get_sld_style_name(sld_from_request) or 'default'
                params['STYLES'] = sld_style_name
                params.pop('styles', None)
                log.debug(f"STYLES={sld_style_name} (nom UserStyle du SLD client)")
                if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                    params['VERSION'] = '1.1.1'
                    if params.get('CRS'):
                        params['SRS'] = params.pop('CRS')
                    params.pop('crs', None)
                    log.debug(f"GetMap: VERSION forcée à 1.1.1, CRS→SRS (compatibilité SLD MapServer)")
        elif 'SLD' in params:
            sld_url = params.get('SLD', '')
            log.debug(f"Paramètre SLD (URL) trouvé dans la requête pour {dataset_name}: {sld_url}")
        else:
            # SLD par ressource uniquement (plus de SLD au niveau dataset)
            layers_param = params.get('LAYERS') or params.get('layers') or ''
            layer_names = [x.strip() for x in layers_param.split(',') if x.strip()]
            sld_style = None
            if layer_names:
                sld_style = ogc_helpers.build_combined_sld_for_layers(dataset_name, layer_names, dataset_name)
            if sld_style:
                sld_size = len(sld_style) if isinstance(sld_style, str) else 0
                sld_style_name = ogc_helpers.get_sld_style_name(sld_style) or 'default'
                # MapServer en SLD_BODY ne gère pas GraphicFill (rayures) : il tente d'ouvrir un fichier (ex. hatch-line) et échoue.
                # On n'envoie pas SLD_BODY et on utilise le style déjà compilé dans le mapfile.
                if ogc_helpers.sld_contains_graphic_fill(sld_style):
                    log.debug(f"SLD avec GraphicFill (rayures): pas d'envoi SLD_BODY pour {dataset_name}, utilisation du style compilé du mapfile (STYLES={sld_style_name})")
                    if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                        layer_list = (params.get('LAYERS') or params.get('layers') or '').split(',')
                        style_list = ",".join([sld_style_name] * max(1, len([x for x in layer_list if x.strip()])))
                        params['STYLES'] = style_list
                    if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
                        params['STYLE'] = sld_style_name
                else:
                    log.info(f"SLD ressource appliqué pour {dataset_name}: {sld_size} caractères")
                    params['SLD_BODY'] = sld_style
                    log.debug(f"SLD_BODY ajouté (envoi en POST à MapServer)")
                    if 'SLD_VERSION' not in params and 'sld_version' not in params:
                        params['SLD_VERSION'] = '1.0.0'
                    if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                        # Si le client a fourni STYLES, on le respecte
                        if 'STYLES' in params or 'styles' in params:
                            if 'styles' in params and 'STYLES' not in params:
                                params['STYLES'] = params.pop('styles')
                            log.info(f"STYLES conservé côté client: {params.get('STYLES')}")
                        else:
                            # Sinon, on met le style extrait du SLD (ou 'default')
                            layer_list = (params.get('LAYERS') or params.get('layers') or '').split(',')
                            style_list = ",".join([sld_style_name] * max(1, len([x for x in layer_list if x.strip()])))
                            params['STYLES'] = style_list
                            log.info(f"STYLES défini depuis SLD: {style_list}")
                        if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                            params['VERSION'] = '1.1.1'
                            if params.get('CRS'):
                                params['SRS'] = params.pop('CRS')
                            params.pop('crs', None)
                            log.debug(f"GetMap: VERSION forcée à 1.1.1, CRS→SRS (compatibilité SLD MapServer)")
                    if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
                        if 'SLD_VERSION' not in params and 'sld_version' not in params:
                            params['SLD_VERSION'] = '1.0.0'
                        params['STYLE'] = sld_style_name
                        log.debug(f"GetLegendGraphic: STYLE={sld_style_name}, SLD_VERSION={params.get('SLD_VERSION')}")
                    if isinstance(sld_style, str) and len(sld_style) > 0:
                        log.debug(f"Aperçu SLD: {sld_style[:300].replace(chr(10), ' ').strip()}...")
            else:
                log.info(f"Aucun SLD ressource pour LAYERS={layer_names}, utilisation du style MapServer par défaut")
        
        # Pour GetLegendGraphic sans SLD, MapServer peut exiger SLD_VERSION
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            if 'SLD_VERSION' not in params and 'sld_version' not in params:
                params['SLD_VERSION'] = '1.0.0'
                log.debug(f"GetLegendGraphic: SLD_VERSION=1.0.0 ajouté (sans SLD)")
        
        # GetMap/GetFeatureInfo avec SLD : forcer WMS 1.1.1 (MapServer doc n'exemple SLD qu'en 1.1.1)
        if ('SLD_BODY' in params or 'SLD' in params) and request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
            if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                params['VERSION'] = '1.1.1'
                if params.get('CRS'):
                    params['SRS'] = params.pop('CRS')
                params.pop('crs', None)
                log.debug(f"GetMap/GetFeatureInfo: VERSION 1.1.1, CRS→SRS (SLD ; compatibilité MapServer)")
        
        # Faire la requête à MapServer
        log.info(f"Envoi requête WMS à MapServer: {mapserver_url}/wms")
        log.debug(f"Mapfile: {mapfile_path}")
        # Logger les paramètres importants (sans le SLD_BODY complet qui peut être très long)
        important_params = {k: v if k != 'SLD_BODY' else f"[{len(v)} caractères]" for k, v in params.items()}
        log.info(f"Paramètres: {important_params}")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Requête envoyée (proxy dataset) — LAYER={params.get('LAYER')}, STYLE={params.get('STYLE')}, SLD_BODY={'oui' if 'SLD_BODY' in params else 'non'}")
        if 'SLD_BODY' in params:
            log.debug(f"SLD_BODY présent dans la requête: {len(params['SLD_BODY'])} caractères")
        
        log.debug(f"SLD_BODY in params? {'SLD_BODY' in params} size={len(params.get('SLD_BODY',''))}")
        log.debug(f"STYLES={params.get('STYLES')} VERSION={params.get('VERSION')} SRS={params.get('SRS')} CRS={params.get('CRS')}")
        
        # Si SLD_BODY présent : filtrer le SLD sur le style demandé. Garder STYLES : MapServer a besoin du nom du style (ex. default) pour appliquer le bon UserStyle du SLD.
        client_styles = params.get('STYLES') or params.get('styles') or params.get('STYLE') or params.get('style')
        if 'SLD_BODY' in params and client_styles:
            wanted = (client_styles.split(',')[0] or '').strip()
            if wanted:
                params['SLD_BODY'] = ogc_helpers.keep_only_userstyle(params['SLD_BODY'], wanted)
                log.debug(f"SLD_BODY filtré pour ne garder que UserStyle={wanted}")
        # Ne pas supprimer STYLES quand SLD_BODY est présent : MapServer utilise STYLES pour sélectionner le UserStyle dans le SLD (ex. STYLES=default).
        if 'SLD_BODY' in params and (params.get('STYLES') or params.get('styles')):
            log.debug(f"SLD_BODY + STYLES conservé pour MapServer: {params.get('STYLES') or params.get('styles')}")

        # SLD_BODY → POST avec corps explicite (application/x-www-form-urlencoded)
        from urllib.parse import urlencode
        _base = (mapserver_url or '').rstrip('/')
        wms_url = _base if _base.endswith('/wms') else f"{_base}/wms"
        if 'SLD_BODY' in params:
            post_body = urlencode(params, doseq=True)
            body_bytes = post_body.encode('utf-8')
            post_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body_bytes)),
            }
            log.debug(f"Envoi requête WMS en POST (SLD_BODY), body length={len(body_bytes)}")
            response = requests.post(
                wms_url,
                data=body_bytes,
                headers=post_headers,
                timeout=60
            )
        else:
            response = requests.get(wms_url, params=params, timeout=60)
        
        # Logger la réponse de MapServer
        log.debug(f"Réponse MapServer reçue: status={response.status_code}, content-type={response.headers.get('Content-Type', 'N/A')}, size={len(response.content)} bytes")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Réponse (proxy dataset) — type={response.headers.get('Content-Type', 'N/A')}, size={len(response.content)} bytes")
        if 'SLD_BODY' in params:
            # Vérifier si MapServer a retourné une erreur liée au SLD
            if response.status_code != 200 or (b'ServiceExceptionReport' in response.content and b'SLD' in response.content):
                log.warning(f"MapServer a peut-être rejeté le SLD (vérifier les logs MapServer)")
                # Logger un aperçu de l'erreur si c'est du XML
                if response.content.startswith(b'<?xml'):
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(response.content)
                        error_text = ET.tostring(root, encoding='unicode')[:500]
                        log.warning(f"Aperçu erreur MapServer: {error_text}...")
                    except Exception:
                        pass
        
        # Vérifier si la réponse est une erreur (HTML ou XML ServiceExceptionReport)
        content_type = response.headers.get('Content-Type', '').lower()
        is_html_error = 'text/html' in content_type or response.content.startswith(b'<HTML>') or response.content.startswith(b'<html>')
        is_xml_error = response.content.startswith(b'<?xml') and b'ServiceExceptionReport' in response.content
        
        # Pour GetMap, on s'attend à une image PNG, pas du XML ou HTML
        request_type = request.args.get('REQUEST') or request.args.get('request')
        if request_type and request_type.upper() == 'GETMAP':
            # Vérifier si c'est vraiment une image PNG (commence par les bytes PNG)
            is_png = response.content.startswith(b'\x89PNG\r\n\x1a\n')
            if not is_png and (is_html_error or is_xml_error or 'image' not in content_type):
                # C'est une erreur, pas une image
                error_msg = "MapServer error"
                try:
                    if is_xml_error:
                        # Parser le XML d'erreur
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(response.content)
                        # Chercher le message d'erreur dans ServiceExceptionReport
                        for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                            error_msg = exception.text or "Unknown error"
                            break
                        # Essayer aussi sans namespace
                        if error_msg == "MapServer error":
                            for exception in root.findall('.//ServiceException'):
                                error_msg = exception.text or "Unknown error"
                                break
                    elif is_html_error:
                        # Parser le HTML d'erreur
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(response.content, 'html.parser')
                            body = soup.find('body')
                            if body:
                                error_msg = body.get_text(strip=True)
                        except Exception:
                            import re
                            match = re.search(r'<BODY[^>]*>(.*?)</BODY>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                            if match:
                                error_msg = match.group(1).strip()
                except Exception as parse_error:
                    log.warning(f"Could not parse error message: {parse_error}")
                
                log.error(f"MapServer returned error for GetMap: {error_msg}")
                return jsonify({
                    'error': 'MapServer error',
                    'message': error_msg,
                    'mapfile': mapfile_path
                }), response.status_code if response.status_code >= 400 else 500
        
        # Résumé final de l'application du SLD
        sld_applied = 'SLD_BODY' in params or 'SLD' in params
        if sld_applied:
            if response.status_code == 200:
                request_type = request.args.get('REQUEST', '').upper()
                if request_type == 'GETMAP':
                    is_png = response.content.startswith(b'\x89PNG\r\n\x1a\n')
                    if is_png:
                        log.info(f"SLD appliqué avec succès pour {dataset_name} - MapServer a retourné une image PNG valide")
                    else:
                        log.warning(f"SLD appliqué mais réponse MapServer n'est pas une image PNG valide pour {dataset_name}")
                else:
                    log.info(f"SLD appliqué pour {dataset_name} (REQUEST={request_type})")
            else:
                log.warning(f"SLD appliqué mais MapServer a retourné une erreur (status={response.status_code}) pour {dataset_name}")
        else:
            log.info(f"Aucun SLD appliqué pour {dataset_name} (style par défaut)")
        
        log.debug(f"========== FIN GESTION SLD POUR {dataset_name} ==========")
        
        # Retourner la réponse avec headers pour affichage dans le navigateur (pas de téléchargement)
        final_content_type = response.headers.get('Content-Type', 'application/octet-stream')
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Content-Type': final_content_type
        }
        if request_type:
            req_upper = request_type.upper()
            if req_upper == 'GETCAPABILITIES':
                if response.content.startswith(b'<?xml') or response.content.startswith(b'<') or b'<WMT_MS_Capabilities' in response.content or b'Capabilities' in response.content[:500]:
                    headers['Content-Type'] = 'application/xml; charset=utf-8'
                    headers['Content-Disposition'] = 'inline'
            elif req_upper in ('GETMAP', 'GETLEGENDGRAPHIC'):
                # Toujours forcer l'affichage inline (éviter le téléchargement)
                headers['Content-Disposition'] = 'inline'
                if response.content.startswith(b'\x89PNG\r\n\x1a\n'):
                    headers['Content-Type'] = 'image/png'
        
        # Copier les autres headers importants de MapServer (ne pas copier Content-Disposition pour garder inline)
        for header_name in ['Content-Length', 'Cache-Control', 'Expires']:
            if header_name in response.headers:
                headers[header_name] = response.headers[header_name]
        
        return Response(
            response.content,
            status=response.status_code,
            headers=headers
        )
        
    except requests.exceptions.RequestException as e:
        log.error(f"Error proxying WMS request to MapServer: {e}")
        return jsonify({
            'error': 'MapServer connection error',
            'message': str(e)
        }), 502
    except Exception as e:
        log.error(f"Unexpected error in WMS direct proxy: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@wfs.route('', methods=['GET', 'OPTIONS'])
@wfs.route('/', methods=['GET', 'OPTIONS'])
def wfs_direct_proxy():
    """
    Proxy WFS direct vers MapServer
    Accepte les requêtes WFS avec le paramètre map directement dans l'URL
    Format: /wfs?map=/mapserver/mapfiles/dataset.map&SERVICE=WFS&...
    """
    # Gérer les requêtes OPTIONS pour CORS
    if request.method == 'OPTIONS':
        return Response(
            '',
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Max-Age': '3600'
            }
        )
    
    try:
        # Récupérer le paramètre map depuis la requête
        mapfile_path = request.args.get('map')
        
        if not mapfile_path:
            return jsonify({
                'error': 'Missing map parameter',
                'message': 'The "map" parameter is required. Format: /wfs?map=/mapserver/mapfiles/dataset.map&SERVICE=WFS&...'
            }), 400
        
        # Vérifier que c'est bien un chemin de mapfile valide
        if not mapfile_path.startswith('/mapserver/mapfiles/') or not mapfile_path.endswith('.map'):
            return jsonify({
                'error': 'Invalid map parameter',
                'message': f'Mapfile path must be in format /mapserver/mapfiles/dataset.map, got: {mapfile_path}'
            }), 400
        
        # Récupérer le service (doit être WFS)
        service = request.args.get('SERVICE') or request.args.get('service', 'WFS')
        if service.upper() != 'WFS':
            return jsonify({
                'error': 'Invalid SERVICE',
                'message': f'This endpoint only supports WFS, got {service}'
            }), 400
        
        # Construire l'URL MapServer (utiliser l'URL interne du conteneur)
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        
        # Copier tous les paramètres de la requête (map est déjà dedans)
        params = dict(request.args)
        
        # Faire la requête à MapServer
        log.debug(f"Direct proxying WFS request to MapServer: {mapserver_url}/wfs?map={mapfile_path}&...")
        response = requests.get(f"{mapserver_url}/wfs", params=params, timeout=60)
        
        # Post-traiter le XML pour corriger les noms de balises invalides
        xml_content = _fix_xml_invalid_tag_names(response.content)
        
        # Déterminer le content-type approprié selon le type de requête
        request_type = params.get('REQUEST') or params.get('request', '').upper()
        content_type = response.headers.get('Content-Type', 'application/xml')
        
        # Pour DescribeFeatureType, utiliser application/xml pour permettre l'affichage dans le navigateur
        if request_type == 'DESCRIBEFEATURETYPE':
            content_type = 'application/xml'
        elif 'text/html' in content_type.lower():
            # Si c'est une erreur HTML, retourner comme erreur
            return jsonify({
                'error': 'MapServer error',
                'message': 'MapServer returned HTML error. Check mapfile configuration.'
            }), 500
        
        # Vérifier si c'est une erreur XML ServiceExceptionReport
        if xml_content.startswith(b'<?xml') and b'ServiceExceptionReport' in xml_content:
            error_msg = "MapServer error"
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(xml_content)
                for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                    error_msg = exception.text or "Unknown error"
                    break
                if error_msg == "MapServer error":
                    for exception in root.findall('.//ServiceException'):
                        error_msg = exception.text or "Unknown error"
                        break
            except Exception:
                pass
            
            log.error(f"MapServer returned XML error: {error_msg}")
            return _create_wfs_exception('NoApplicableCode', f'MapServer error: {error_msg}', request_type or 'WFS')
        
        # Retourner la réponse avec les headers appropriés
        # Pour les navigateurs modernes, utiliser application/xml avec charset=utf-8
        final_content_type = content_type if 'charset' in content_type else f'{content_type}; charset=utf-8'
        
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Content-Type': final_content_type,
            'Content-Disposition': 'inline'  # Permet l'affichage dans le navigateur au lieu du téléchargement
        }
        
        # Copier les autres headers importants de MapServer
        for header_name in ['Content-Length', 'Cache-Control', 'Expires']:
            if header_name in response.headers:
                headers[header_name] = response.headers[header_name]
        
        return Response(
            xml_content,
            status=response.status_code,
            headers=headers
        )
        
    except requests.exceptions.RequestException as e:
        log.error(f"Error proxying WFS request to MapServer: {e}")
        return jsonify({
            'error': 'MapServer connection error',
            'message': str(e)
        }), 502
    except Exception as e:
        log.error(f"Unexpected error in WFS direct proxy: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@wms.route('/sld/<org_id>/<dataset_name>', methods=['GET'])
def wms_sld_for_layers(org_id, dataset_name):
    """
    Sert le SLD combiné pour des couches données (GET).
    Utilisé par MapServer avec SLD=<url> pour récupérer le style sans troncature URL/POST.
    Query: ?layers=res_xxx,res_yyy
    """
    from urllib.parse import quote
    layers_param = request.args.get('layers', '')
    layer_names = [x.strip() for x in layers_param.split(',') if x.strip()]
    if not layer_names:
        return Response(
            '<?xml version="1.0"?><ServiceExceptionReport xmlns="http://www.opengis.net/ogc">'
            '<ServiceException>Parameter layers is required</ServiceException></ServiceExceptionReport>',
            status=400,
            mimetype='application/xml'
        )
    try:
        sld_xml = ogc_helpers.build_combined_sld_for_layers(dataset_name, layer_names, dataset_name)
    except Exception as e:
        log.warning(f"wms_sld_for_layers: build_combined_sld_for_layers failed: {e}")
        return Response(
            '<?xml version="1.0"?><ServiceExceptionReport xmlns="http://www.opengis.net/ogc">'
            f'<ServiceException>{html.escape(str(e))}</ServiceException></ServiceExceptionReport>',
            status=500,
            mimetype='application/xml'
        )
    if not sld_xml:
        return Response(
            '<?xml version="1.0"?><ServiceExceptionReport xmlns="http://www.opengis.net/ogc">'
            '<ServiceException>No SLD defined for requested layers</ServiceException></ServiceExceptionReport>',
            status=404,
            mimetype='application/xml'
        )
    return Response(
        sld_xml,
        mimetype='application/vnd.ogc.se+xml',
        headers={'Content-Type': 'application/vnd.ogc.se+xml; charset=utf-8', 'Cache-Control': 'no-cache, no-store, must-revalidate'}
    )


@wms.route('/<org_id>', methods=['GET'])
@wms.route('/<org_id>/', methods=['GET'])
def wms_proxy(org_id):
    """
    Proxy WMS vers MapServer
    Redirige les requêtes WMS vers MapServer avec le bon format de mapfile
    """
    try:
        # Récupérer les paramètres de la requête (utiliser to_dict() pour éviter les doublons)
        request_args = request.args.to_dict(flat=False)
        service = None
        request_type = None
        
        if 'SERVICE' in request_args:
            service = request_args['SERVICE'][0] if isinstance(request_args['SERVICE'], list) else request_args['SERVICE']
        elif 'service' in request_args:
            service = request_args['service'][0] if isinstance(request_args['service'], list) else request_args['service']
        
        if 'REQUEST' in request_args:
            request_type = request_args['REQUEST'][0] if isinstance(request_args['REQUEST'], list) else request_args['REQUEST']
        elif 'request' in request_args:
            request_type = request_args['request'][0] if isinstance(request_args['request'], list) else request_args['request']
        
        # Si VERSION est présent mais pas SERVICE/REQUEST, supposer WMS GetCapabilities (comportement QGIS)
        if not service and not request_type:
            version = request_args.get('VERSION', request_args.get('version', ['']))
            if isinstance(version, list) and len(version) > 0:
                version = version[0]
            if version and ('1.3' in str(version) or '1.1' in str(version) or '1.0' in str(version)):
                # QGIS envoie parfois juste VERSION sans SERVICE/REQUEST, supposer WMS GetCapabilities
                service = 'WMS'
                request_type = 'GetCapabilities'
                log.debug(f"QGIS-style WMS request detected: VERSION={version} without SERVICE/REQUEST, assuming WMS GetCapabilities")
        
        # Si service n'est pas défini, supposer WMS (comportement par défaut pour /wms/)
        if not service:
            service = 'WMS'
        
        layers = request.args.get('LAYERS') or request.args.get('layers')
        
        # Récupérer SLD_BODY depuis les paramètres (pour la prévisualisation)
        sld_body = request.args.get('SLD_BODY') or request.args.get('sld_body')
        
        # Si ce n'est pas WMS, retourner une erreur
        if service.upper() != 'WMS':
            return jsonify({
                'error': 'Invalid SERVICE',
                'message': f'This endpoint only supports WMS, got {service}'
            }), 400
        
        # Si pas de request_type, supposer GetCapabilities (comportement par défaut pour /wms/)
        if not request_type:
            request_type = 'GetCapabilities'
            log.debug(f"No REQUEST parameter, assuming GetCapabilities for /wms/{org_id}")
        
        # Si pas de layers spécifié ET que c'est GetCapabilities, retourner toutes les collections de l'org
        # Sinon, si layers est spécifié, utiliser ce dataset spécifique
        if not layers:
            # Pour GetCapabilities sans LAYERS, on peut retourner toutes les couches de l'organisation
            if request_type and request_type.upper() == 'GETCAPABILITIES':
                org_collections, error = _get_org_collections(org_id)
                if error:
                    return error
                
                # Filtrer les collections pour ne garder que celles qui ont un mapfile
                mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
                collections_with_mapfiles = []
                
                for collection in org_collections:
                    collection_id = collection.get('id')
                    if not collection_id:
                        continue
                    
                    # Vérifier si le mapfile existe en vérifiant directement le fichier
                    mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
                    mapfile_exists = False
                    
                    try:
                        if os.path.exists(mapfile_path) and os.path.isfile(mapfile_path):
                            # Vérifier aussi que le fichier n'est pas vide
                            try:
                                if os.path.getsize(mapfile_path) > 0:
                                    mapfile_exists = True
                                else:
                                    log.debug(f"Skipping {collection_id} - mapfile is empty")
                            except OSError as e:
                                # Logging détaillé de l'erreur OSError
                                log.warning(f"OSError lors de l'accès au mapfile {mapfile_path} pour {collection_id}")
                                log.warning(f"   Type d'erreur: {type(e).__name__}")
                                log.warning(f"   Message: {str(e)}")
                                log.warning(f"   Errno: {e.errno if hasattr(e, 'errno') else 'N/A'}")
                                log.warning(f"   Strerror: {e.strerror if hasattr(e, 'strerror') else 'N/A'}")
                                log.warning(f"   Filename: {e.filename if hasattr(e, 'filename') else 'N/A'}")
                                log.debug(f"Skipping {collection_id} - cannot access mapfile")
                        else:
                            log.debug(f"Skipping {collection_id} - mapfile not found: {mapfile_path}")
                    except Exception as e:
                        # Gérer toutes les exceptions lors de la vérification du mapfile
                        log.debug(f"Skipping {collection_id} - error checking mapfile: {e}")
                    
                    # Inclure la collection seulement si le mapfile existe
                    # Note: On n'exclut plus les collections "sync-" si elles ont un mapfile valide
                    if mapfile_exists:
                        collections_with_mapfiles.append(collection)
                
                # Générer un GetCapabilities WMS avec toutes les couches de l'organisation
                return _generate_wms_capabilities(org_id, collections_with_mapfiles)
            else:
                # Pour les autres requêtes sans LAYERS, erreur
                return jsonify({
                    'error': 'Missing LAYERS parameter',
                    'message': 'LAYERS parameter is required for WMS requests (except GetCapabilities without LAYERS)'
                }), 400
        
        # Le paramètre layers contient le nom du layer (res_xxx par ressource, ou dataset_name / dataset_name_2 en rétrocompat)
        layer_name = layers
        # Résoudre le dataset : un mapfile par dataset, un layer par ressource (NAME "res_xxx")
        dataset_name = _find_dataset_for_layer_in_org(org_id, layer_name)
        if dataset_name is None:
            # Si le layer ressemble à un id ressource (res_xxx), ne pas utiliser comme nom de dataset
            if layer_name.startswith('res_') and '_' in layer_name[4:]:
                return jsonify({
                    'error': 'Layer not found',
                    'message': f'Layer "{layer_name}" was not found in any dataset mapfile.',
                    'hint': 'Regenerate mapfiles so that layers use the res_xxx format (one mapfile per dataset, one layer per resource): sync-mapfiles or generate-mapfile.py --dataset <dataset_name>'
                }), 404
            # Rétrocompat : layer_name = dataset_name ou dataset_name_2, dataset_name_3
            import re
            dataset_name_match = re.match(r'^(.+?)(?:_\d+)?$', layer_name)
            dataset_name = dataset_name_match.group(1) if dataset_name_match else layer_name
        
        # Pour GetCapabilities avec un LAYERS spécifique : MapServer en 1.1.0 puis réécriture CKAN (URLs + styles extras)
        if request_type and request_type.upper() == 'GETCAPABILITIES':
            mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
            mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
            params = dict(request.args)
            params['map'] = mapfile_path
            params.pop('LAYERS', None)
            params.pop('layers', None)
            params['VERSION'] = '1.1.0'
            
            log.debug(f"Proxying WMS GetCapabilities to MapServer for dataset {dataset_name} (VERSION=1.1.0)")
            response = requests.get(f"{mapserver_url}/wms", params=params, timeout=60)
            
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type or response.content.startswith(b'<HTML>') or response.content.startswith(b'<html>'):
                error_msg = "MapServer error"
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.content, 'html.parser')
                    body = soup.find('body')
                    if body:
                        error_msg = body.get_text(strip=True)
                except Exception:
                    match = re.search(r'<BODY[^>]*>(.*?)</BODY>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                    if match:
                        error_msg = match.group(1).strip()
                log.error(f"MapServer returned HTML error for GetCapabilities: {error_msg}")
                return jsonify({
                    'error': 'MapServer error',
                    'message': error_msg,
                    'mapfile': mapfile_path
                }), 500
            
            try:
                xml_content = response.content.decode('utf-8')
            except Exception:
                xml_content = response.content.decode('utf-8', errors='ignore')
            
            ckan_site_url = ogc_helpers.get_ogc_base_url()
            xml_content = _rewrite_wms_getcapabilities_for_ckan(org_id, dataset_name, xml_content, ckan_site_url)
            
            headers = {
                'Access-Control-Allow-Origin': '*',
                'Content-Type': 'application/xml; charset=utf-8',
                'Cache-Control': 'public, max-age=3600'
            }
            return Response(
                xml_content.encode('utf-8'),
                mimetype='application/xml',
                headers=headers
            )
        
        # GARDE-FOU: Valider le mapfile avant d'utiliser MapServer
        # Récupérer le dataset pour avoir accès aux métadonnées OGC
        dataset = None
        try:
            from ckan.plugins import toolkit
            context = {'ignore_auth': True}
            dataset = toolkit.get_action('package_show')(context, {'id': dataset_name})
        except Exception as e:
            log.debug(f"Erreur récupération dataset pour validation: {e}")
        
        # Valider le mapfile
        mapfile_valid, validation_error = _validate_mapfile_for_wms(dataset_name, dataset)
        
        if not mapfile_valid:
            log.warning(f"Mapfile invalide pour {dataset_name}: {validation_error}")
            # Fallback vers pygeoapi si disponible
            if pygeoapi_util:
                log.info(f"Fallback vers pygeoapi pour {dataset_name}")
                # Rediriger vers pygeoapi
                try:
                    # Utiliser la fonction helper pour obtenir l'URL pygeoapi
                    pygeoapi_url = get_pygeoapi_url()
                    if pygeoapi_url:
                        # Construire l'URL pygeoapi pour cette collection
                        collection_id = f"sync-{dataset_name}"
                        pygeoapi_collection_url = f"{pygeoapi_url}/collections/{collection_id}"
                        log.info(f"Redirection vers pygeoapi: {pygeoapi_collection_url}")
                        # Pour GetCapabilities, on peut retourner une erreur ou rediriger
                        # Pour GetMap, on doit rediriger vers pygeoapi
                        if request_type and request_type.upper() in ['GETMAP', 'GETFEATUREINFO']:
                            return jsonify({
                                'error': 'Mapfile invalid',
                                'mapfile': f"/mapserver/mapfiles/{dataset_name}.map",
                                'message': f'Mapfile for dataset "{dataset_name}" is invalid: {validation_error}',
                                'hint': f'This dataset may be available via OGC API Features (pygeoapi) instead. Try: {pygeoapi_collection_url}'
                            }), 404
                except Exception as e:
                    log.warning(f"Erreur lors du fallback pygeoapi: {e}")
            
            # Pas de pygeoapi ou erreur, retourner une erreur
            if request_type and request_type.upper() in ['GETMAP', 'GETFEATUREINFO']:
                return jsonify({
                    'error': 'Mapfile invalid',
                    'mapfile': f"/mapserver/mapfiles/{dataset_name}.map",
                    'message': f'Mapfile for dataset "{dataset_name}" is invalid: {validation_error}',
                    'hint': 'The mapfile may not exist, the datasource may be inaccessible, or the PostGIS table may not exist.'
                }), 404
        
        # Construire l'URL MapServer (utiliser l'URL interne du conteneur)
        mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
        mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
        
        # Vérifier si le mapfile existe en faisant une requête test à MapServer
        # ou en vérifiant via une requête HEAD
        try:
            test_params = {'map': mapfile_path, 'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetCapabilities'}
            test_response = requests.get(f"{mapserver_url}/wms", params=test_params, timeout=5)
            if test_response.status_code != 200 or 'text/html' in test_response.headers.get('Content-Type', '').lower():
                # Le mapfile n'existe pas ou est invalide
                # Vérifier aussi directement si le fichier existe dans le volume monté
                mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
                local_mapfile_path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
                mapfile_exists = os.path.exists(local_mapfile_path) if os.path.exists(mapfiles_dir) else False
                
                if not mapfile_exists:
                    # Essayer de générer le mapfile si le dataset existe dans CKAN
                    log.warning(f"Mapfile not found for {dataset_name} (path: {mapfile_path}), attempting to generate...")
                    try:
                        from ckanext.ogc.plugin import OGCPlugin
                        plugin = OGCPlugin()
                        plugin._generate_mapfile_async(dataset_name)
                        log.info(f"Mapfile generation triggered for {dataset_name}")
                        # Attendre un peu pour que le mapfile soit généré (non bloquant)
                        import time
                        time.sleep(3)
                        # Vérifier à nouveau si le mapfile a été créé
                        if os.path.exists(local_mapfile_path):
                            log.debug(f"Mapfile created successfully: {local_mapfile_path}")
                        else:
                            log.warning(f"Mapfile still not found after generation attempt: {local_mapfile_path}")
                    except Exception as gen_error:
                        log.error(f"Failed to generate mapfile for {dataset_name}: {gen_error}")
                        import traceback
                        log.error(traceback.format_exc())
                        # Si le dataset commence par "sync-", c'est probablement une collection pygeoapi
                        if dataset_name.startswith('sync-'):
                            return jsonify({
                                'error': 'Layer not available via WMS',
                                'message': f'Layer "{dataset_name}" is a pygeoapi collection and cannot be served via MapServer WMS. Use the OGC API Features endpoint instead.',
                                'hint': 'This layer is only available via OGC API Features (pygeoapi), not via traditional WMS/WFS.'
                            }), 404
                        else:
                            # Pour GetMap, GetFeatureInfo, etc., retourner une erreur si le mapfile n'existe pas
                            # Pour GetCapabilities, on peut retourner un GetCapabilities vide
                            if request_type and request_type.upper() in ['GETMAP', 'GETFEATUREINFO']:
                                return jsonify({
                                    'error': 'Mapfile not found',
                                    'mapfile': mapfile_path,
                                    'message': f'Mapfile for dataset "{dataset_name}" does not exist and could not be generated automatically.',
                                    'hint': f'The dataset may not have geospatial data, or the mapfile generation failed. Check the dataset resources and ensure they contain geospatial data. You can try to generate it manually with: docker compose exec mapserver python3 /usr/local/bin/generate-mapfile.py --dataset {dataset_name}'
                                }), 404
                            else:
                                # Pour GetCapabilities, retourner un GetCapabilities vide plutôt qu'une erreur
                                log.warning(f"Mapfile not found for {dataset_name}, returning empty GetCapabilities")
        except requests.exceptions.RequestException as e:
            log.warning(f"Could not verify mapfile existence for {dataset_name}: {e}")
            # Pour GetMap/GetFeatureInfo, retourner une erreur si on ne peut pas vérifier le mapfile
            # Pour GetCapabilities, on peut continuer
            if request_type and request_type.upper() in ['GETMAP', 'GETFEATUREINFO']:
                return jsonify({
                    'error': 'Mapfile verification failed',
                    'mapfile': mapfile_path,
                    'message': f'Could not verify if mapfile exists for dataset "{dataset_name}".',
                    'hint': f'Check the dataset resources and ensure they contain geospatial data. You can try to generate it manually with: docker compose exec mapserver python3 /usr/local/bin/generate-mapfile.py --dataset {dataset_name}'
                }), 502
            # Pour GetCapabilities, continuer quand même
        
        # Copier tous les paramètres de la requête
        params = dict(request.args)
        # Normalisation casse des paramètres SLD (clients envoient souvent en minuscules)
        if 'sld_body' in params and 'SLD_BODY' not in params:
            params['SLD_BODY'] = params.pop('sld_body')
        if 'sld' in params and 'SLD' not in params:
            params['SLD'] = params.pop('sld')
        if 'sld_version' in params and 'SLD_VERSION' not in params:
            params['SLD_VERSION'] = params.pop('sld_version')
        # WMS : avec un SLD client, STYLES doit exister (vide) pour éviter le CLASS du mapfile
        if ('SLD_BODY' in params or 'SLD' in params) and 'STYLES' not in params and 'styles' not in params:
            params['STYLES'] = ''
            params.pop('styles', None)
        # Ajouter le paramètre map (requis par MapServer) - utilise dataset_name car le mapfile est nommé d'après le dataset
        params['map'] = mapfile_path
        
        # IMPORTANT: Pour GetMap, GetFeatureInfo, etc., on doit utiliser layer_name dans LAYERS
        # pour que MapServer affiche le bon layer (testlmo666_2 au lieu de testlmo666)
        # Le paramètre LAYERS vient déjà de la requête avec le bon nom de layer, donc on le préserve
        # Mais pour GetLegendGraphic, MapServer nécessite LAYER (singulier) et non LAYERS (pluriel)
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            if 'LAYERS' in params:
                params['LAYER'] = params.pop('LAYERS')
            elif 'layers' in params:
                params['LAYER'] = params.pop('layers')
            # S'assurer que LAYER est défini (utiliser layer_name si absent pour afficher le bon layer)
            if 'LAYER' not in params and 'layer' not in params:
                params['LAYER'] = layer_name  # Utiliser layer_name (testlmo666_2) au lieu de dataset_name
                log.debug(f"GetLegendGraphic: LAYER défini à {layer_name}")
            # Retirer LAYERS si présent (MapServer n'en a pas besoin pour GetLegendGraphic)
            params.pop('LAYERS', None)
            params.pop('layers', None)
        else:
            # Pour les autres requêtes (GetMap, GetFeatureInfo), s'assurer que LAYERS utilise layer_name
            # Le paramètre LAYERS vient déjà de la requête, mais on s'assure qu'il est correct
            if 'LAYERS' not in params and 'layers' not in params:
                params['LAYERS'] = layer_name  # Utiliser layer_name pour afficher le bon layer
                log.debug(f"GetMap/GetFeatureInfo: LAYERS défini à {layer_name} (était absent)")
            elif 'LAYERS' in params:
                # S'assurer que LAYERS utilise layer_name (au cas où il aurait été modifié)
                old_layers = params['LAYERS']
                params['LAYERS'] = layer_name
                if old_layers != layer_name:
                    log.debug(f"GetMap/GetFeatureInfo: LAYERS modifié de '{old_layers}' à '{layer_name}'")
                else:
                    log.debug(f"GetMap/GetFeatureInfo: LAYERS déjà correct: '{layer_name}'")
            elif 'layers' in params:
                old_layers = params['layers']
                params['layers'] = layer_name
                if old_layers != layer_name:
                    log.debug(f"GetMap/GetFeatureInfo: layers modifié de '{old_layers}' à '{layer_name}'")
        
        log.debug(f"Proxying WMS {request_type} to MapServer: map={mapfile_path}, LAYERS={layer_name}, dataset={dataset_name}")
        
        log.debug(f"========== GESTION SLD POUR {dataset_name} (proxy org) ==========")
        log.debug(f"Mapfile: {mapfile_path}")
        log.debug(f"REQUEST: {request.args.get('REQUEST', 'N/A')}")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Requête légende détectée — LAYER attendu par MapServer: {params.get('LAYER') or params.get('layer') or layer_name}")
        
        # Vérifier si NO_SLD est demandé (avant de traiter le SLD)
        no_sld = params.pop('NO_SLD', None) or params.pop('no_sld', None)
        
        # Si un style SLD est défini pour ce dataset et qu'aucun SLD n'est déjà dans la requête,
        # ajouter le paramètre SLD_BODY ou SLD
        # Si SLD_BODY est déjà dans les paramètres (venant de la requête), on l'utilise tel quel
        # Si le paramètre NO_SLD est présent, ne pas appliquer le SLD automatique
        if no_sld:
            log.info(f"SLD désactivé explicitement (NO_SLD=1) pour {dataset_name}")
        elif 'SLD_BODY' in params:
            sld_from_request = params.get('SLD_BODY', '')
            sld_size = len(sld_from_request) if isinstance(sld_from_request, str) else 0
            log.debug(f"SLD_BODY trouvé dans la requête pour {dataset_name}: {sld_size} caractères")
            if 'SLD_VERSION' not in params:
                params['SLD_VERSION'] = '1.0.0'
            if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                sld_style_name = ogc_helpers.get_sld_style_name(sld_from_request) or 'default'
                params['STYLES'] = sld_style_name
                params.pop('styles', None)
                log.debug(f"STYLES={sld_style_name} (nom UserStyle du SLD client)")
                if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                    params['VERSION'] = '1.1.1'
                    if params.get('CRS'):
                        params['SRS'] = params.pop('CRS')
                    params.pop('crs', None)
                    log.debug(f"GetMap: VERSION forcée à 1.1.1, CRS→SRS (compatibilité SLD MapServer)")
        elif 'SLD' in params:
            sld_url = params.get('SLD', '')
            log.debug(f"Paramètre SLD (URL) trouvé dans la requête pour {dataset_name}: {sld_url}")
        else:
            # SLD par ressource uniquement (plus de SLD au niveau dataset)
            layers_param = params.get('LAYERS') or params.get('layers') or layer_name or ''
            layer_names = [x.strip() for x in layers_param.split(',') if x.strip()]
            sld_style = None
            if layer_names:
                sld_style = ogc_helpers.build_combined_sld_for_layers(dataset_name, layer_names, dataset_name)
            if sld_style:
                sld_size = len(sld_style) if isinstance(sld_style, str) else 0
                sld_style_name = ogc_helpers.get_sld_style_name(sld_style) or 'default'
                # MapServer en SLD_BODY ne gère pas GraphicFill (rayures) : il tente d'ouvrir un fichier (ex. hatch-line) et échoue.
                if ogc_helpers.sld_contains_graphic_fill(sld_style):
                    log.debug(f"SLD avec GraphicFill (rayures): pas d'envoi SLD_BODY pour {dataset_name}, utilisation du style compilé du mapfile (STYLES={sld_style_name})")
                    if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                        layer_list = (params.get('LAYERS') or params.get('layers') or layer_name or '').split(',')
                        style_list = ",".join([sld_style_name] * max(1, len([x for x in layer_list if x.strip()])))
                        params['STYLES'] = style_list
                    if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
                        params['STYLE'] = sld_style_name
                else:
                    log.info(f"SLD ressource appliqué pour {dataset_name}: {sld_size} caractères")
                    params['SLD_BODY'] = sld_style
                    log.debug(f"SLD_BODY ajouté pour {dataset_name} (envoi en POST à MapServer)")
                    if 'SLD_VERSION' not in params and 'sld_version' not in params:
                        params['SLD_VERSION'] = '1.0.0'
                        log.debug(f"SLD_VERSION=1.0.0 (aligné sur SLD_BODY)")
                    if request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
                        if 'STYLES' in params or 'styles' in params:
                            if 'styles' in params and 'STYLES' not in params:
                                params['STYLES'] = params.pop('styles')
                            log.info(f"STYLES conservé côté client: {params.get('STYLES')}")
                        else:
                            layer_list = (params.get('LAYERS') or params.get('layers') or '').split(',')
                            style_list = ",".join([sld_style_name] * max(1, len([x for x in layer_list if x.strip()])))
                            params['STYLES'] = style_list
                            log.info(f"STYLES défini depuis SLD: {style_list}")
                        if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                            params['VERSION'] = '1.1.1'
                            if params.get('CRS'):
                                params['SRS'] = params.pop('CRS')
                            params.pop('crs', None)
                            log.debug(f"GetMap: VERSION forcée à 1.1.1, CRS→SRS (compatibilité SLD MapServer)")
                    if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
                        if 'SLD_VERSION' not in params and 'sld_version' not in params:
                            params['SLD_VERSION'] = '1.0.0'
                            log.debug(f"GetLegendGraphic: SLD_VERSION=1.0.0 (aligné sur SLD_BODY)")
                        params['STYLE'] = sld_style_name
                        log.debug(f"GetLegendGraphic: STYLE={sld_style_name}, SLD_BODY en POST")
                        if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                            params['VERSION'] = '1.1.1'
                            log.debug(f"GetLegendGraphic: VERSION forcée à 1.1.1 (SLD_BODY ; compatibilité MapServer)")
                        log.debug(f"GetLegendGraphic: paramètres envoyés — LAYER={params.get('LAYER')}, STYLE={params.get('STYLE')}, SLD_BODY=oui ({sld_size} car)")
                    if isinstance(sld_style, str) and len(sld_style) > 0:
                        preview = sld_style[:300].replace('\n', ' ').strip()
                        log.debug(f"Aperçu SLD: {preview}...")
            else:
                log.info(f"Aucun SLD ressource pour LAYERS={layer_names}, utilisation du style MapServer par défaut")
        
        # Pour GetLegendGraphic sans SLD, MapServer peut exiger SLD_VERSION
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            if 'SLD_VERSION' not in params and 'sld_version' not in params:
                params['SLD_VERSION'] = '1.0.0'
                log.debug(f"GetLegendGraphic: SLD_VERSION=1.0.0 ajouté (sans SLD)")
        
        # GetMap/GetFeatureInfo avec SLD : forcer WMS 1.1.1 (MapServer doc n'exemple SLD qu'en 1.1.1)
        if ('SLD_BODY' in params or 'SLD' in params) and request_type and request_type.upper() in ('GETMAP', 'GETFEATUREINFO'):
            if (params.get('VERSION') or params.get('version') or '').strip().upper() == '1.3.0':
                params['VERSION'] = '1.1.1'
                if params.get('CRS'):
                    params['SRS'] = params.pop('CRS')
                params.pop('crs', None)
                log.debug(f"GetMap/GetFeatureInfo: VERSION 1.1.1, CRS→SRS (SLD ; compatibilité MapServer)")
        
        # Faire la requête à MapServer
        log.info(f"Envoi requête WMS à MapServer: {mapserver_url}/wms")
        log.debug(f"Mapfile: {mapfile_path}")
        # Logger les paramètres importants (sans le SLD_BODY complet qui peut être très long)
        important_params = {k: v if k != 'SLD_BODY' else f"[{len(v)} caractères]" for k, v in params.items()}
        log.info(f"Paramètres: {important_params}")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Requête envoyée — LAYER={params.get('LAYER')}, STYLE={params.get('STYLE')}, SLD_BODY={'oui' if 'SLD_BODY' in params else 'non'}")
        if 'SLD_BODY' in params:
            log.debug(f"SLD_BODY présent dans la requête: {len(params['SLD_BODY'])} caractères")
        
        log.debug(f"SLD_BODY in params? {'SLD_BODY' in params} size={len(params.get('SLD_BODY',''))}")
        log.debug(f"STYLES={params.get('STYLES')} VERSION={params.get('VERSION')} SRS={params.get('SRS')} CRS={params.get('CRS')}")
        
        # Si SLD_BODY présent : filtrer le SLD sur le style demandé. Garder STYLES : MapServer a besoin du nom du style (ex. default) pour appliquer le bon UserStyle du SLD.
        client_styles = params.get('STYLES') or params.get('styles') or params.get('STYLE') or params.get('style')
        if 'SLD_BODY' in params and client_styles:
            wanted = (client_styles.split(',')[0] or '').strip()
            if wanted:
                params['SLD_BODY'] = ogc_helpers.keep_only_userstyle(params['SLD_BODY'], wanted)
                log.debug(f"SLD_BODY filtré pour ne garder que UserStyle={wanted}")
        # Ne pas supprimer STYLES quand SLD_BODY est présent : MapServer utilise STYLES pour sélectionner le UserStyle dans le SLD (ex. STYLES=default).
        if 'SLD_BODY' in params and (params.get('STYLES') or params.get('styles')):
            log.debug(f"SLD_BODY + STYLES conservé pour MapServer: {params.get('STYLES') or params.get('styles')}")

        # SLD_BODY → POST avec corps explicite (application/x-www-form-urlencoded)
        from urllib.parse import urlencode
        _base = (mapserver_url or '').rstrip('/')
        wms_url = _base if _base.endswith('/wms') else f"{_base}/wms"
        log.info(f"URL MapServer (WMS): {wms_url}")
        if 'SLD_BODY' in params:
            post_body = urlencode(params, doseq=True)
            body_bytes = post_body.encode('utf-8')
            post_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body_bytes)),
            }
            log.debug(f"Envoi requête WMS en POST (SLD_BODY), body length={len(body_bytes)}")
            response = requests.post(
                wms_url,
                data=body_bytes,
                headers=post_headers,
                timeout=60
            )
        else:
            response = requests.get(wms_url, params=params, timeout=60)
        
        # Logger la réponse de MapServer
        log.debug(f"Réponse MapServer reçue: status={response.status_code}, content-type={response.headers.get('Content-Type', 'N/A')}, size={len(response.content)} bytes")
        if request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            log.debug(f"[GetLegendGraphic] Réponse reçue — type={response.headers.get('Content-Type', 'N/A')}, size={len(response.content)} bytes")
        if 'SLD_BODY' in params or 'SLD' in params:
            # Vérifier si MapServer a retourné une erreur liée au SLD
            if response.status_code != 200 or (b'ServiceExceptionReport' in response.content and b'SLD' in response.content):
                log.warning(f"MapServer a peut-être rejeté le SLD (vérifier les logs MapServer)")
                # Logger un aperçu de l'erreur si c'est du XML
                if response.content.startswith(b'<?xml'):
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(response.content)
                        error_text = ET.tostring(root, encoding='unicode')[:500]
                        log.warning(f"Aperçu erreur MapServer: {error_text}...")
                    except Exception:
                        pass
        
        # Vérifier si la réponse est une erreur (HTML ou XML ServiceExceptionReport)
        content_type = response.headers.get('Content-Type', '').lower()
        is_html_error = 'text/html' in content_type or response.content.startswith(b'<HTML>') or response.content.startswith(b'<html>')
        is_xml_error = response.content.startswith(b'<?xml') and b'ServiceExceptionReport' in response.content
        
        # Pour GetMap, on s'attend à une image PNG, pas du XML ou HTML
        if request_type and request_type.upper() == 'GETMAP':
            # Vérifier si c'est vraiment une image PNG (commence par les bytes PNG)
            is_png = response.content.startswith(b'\x89PNG\r\n\x1a\n')
            if not is_png and (is_html_error or is_xml_error or 'image' not in content_type):
                # C'est une erreur, pas une image
                error_msg = "MapServer error"
                try:
                    if is_xml_error:
                        # Parser le XML d'erreur
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(response.content)
                        # Chercher le message d'erreur dans ServiceExceptionReport
                        for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                            error_msg = exception.text or "Unknown error"
                            break
                        if error_msg == "MapServer error":
                            # Essayer sans namespace
                            for exception in root.findall('.//ServiceException'):
                                error_msg = exception.text or "Unknown error"
                                break
                    elif is_html_error:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(response.content, 'html.parser')
                        body = soup.find('body')
                        if body:
                            error_msg = body.get_text(strip=True)
                except Exception as parse_error:
                    log.warning(f"Could not parse error message: {parse_error}")
                    # Essayer d'extraire le texte brut
                    try:
                        import re
                        if is_xml_error:
                            match = re.search(r'<ServiceException[^>]*>(.*?)</ServiceException>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                            if match:
                                error_msg = match.group(1).strip()
                        elif is_html_error:
                            match = re.search(r'<BODY[^>]*>(.*?)</BODY>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                            if match:
                                error_msg = match.group(1).strip()
                    except Exception:
                        pass
                
                log.error(f"MapServer returned error for GetMap: {error_msg}")
                log.error(f"Response content type: {content_type}, first bytes: {response.content[:100]}")
                return jsonify({
                    'error': 'MapServer error',
                    'message': error_msg,
                    'mapfile': mapfile_path,
                    'hint': 'The mapfile may not exist, the layer may not have geospatial data, or there may be a configuration issue.'
                }), 500
        elif is_html_error or is_xml_error:
            # Pour les autres types de requêtes, gérer les erreurs HTML/XML
            error_msg = "MapServer error"
            try:
                if is_xml_error:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(response.content)
                    for exception in root.findall('.//{http://www.opengis.net/ogc}ServiceException'):
                        error_msg = exception.text or "Unknown error"
                        break
                elif is_html_error:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.content, 'html.parser')
                    body = soup.find('body')
                    if body:
                        error_msg = body.get_text(strip=True)
            except Exception:
                import re
                if is_xml_error:
                    match = re.search(r'<ServiceException[^>]*>(.*?)</ServiceException>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                    if match:
                        error_msg = match.group(1).strip()
                elif is_html_error:
                    match = re.search(r'<BODY[^>]*>(.*?)</BODY>', response.content.decode('utf-8', errors='ignore'), re.IGNORECASE | re.DOTALL)
                    if match:
                        error_msg = match.group(1).strip()
            
            log.error(f"MapServer returned error: {error_msg}")
            return jsonify({
                'error': 'MapServer error',
                'message': error_msg,
                'mapfile': mapfile_path,
                'hint': 'The mapfile may not exist or the layer may not have geospatial data in the datastore.'
            }), 500
        
        # Résumé final de l'application du SLD
        sld_applied = 'SLD_BODY' in params or 'SLD' in params
        if sld_applied:
            if response.status_code == 200:
                request_type_check = request.args.get('REQUEST', '').upper()
                if request_type_check == 'GETMAP':
                    is_png = response.content.startswith(b'\x89PNG\r\n\x1a\n')
                    if is_png:
                        log.info(f"SLD appliqué avec succès pour {dataset_name} - MapServer a retourné une image PNG valide")
                    else:
                        log.warning(f"SLD appliqué mais réponse MapServer n'est pas une image PNG valide pour {dataset_name}")
                else:
                    log.debug(f"SLD appliqué pour {dataset_name} (REQUEST={request_type_check})")
            else:
                log.warning(f"SLD appliqué mais MapServer a retourné une erreur (status={response.status_code}) pour {dataset_name}")
        else:
            log.info(f"Aucun SLD appliqué pour {dataset_name} (style par défaut)")
        
        log.debug(f"========== FIN GESTION SLD POUR {dataset_name} ==========")
        
        # Déterminer le mimetype correct selon le type de requête
        if request_type and request_type.upper() == 'GETMAP':
            mimetype = 'image/png'  # Par défaut pour GetMap
        elif request_type and request_type.upper() == 'GETCAPABILITIES':
            mimetype = 'application/xml'
        elif request_type and request_type.upper() == 'GETLEGENDGRAPHIC':
            mimetype = 'image/png'
        else:
            mimetype = response.headers.get('Content-Type', 'application/xml')
        
        # Retourner la réponse de MapServer (Content-Disposition: inline pour affichage, pas téléchargement)
        resp_headers = {
            'Content-Type': mimetype,
            'Cache-Control': 'no-cache',
        }
        if request_type and request_type.upper() in ('GETMAP', 'GETLEGENDGRAPHIC'):
            resp_headers['Content-Disposition'] = 'inline'
        elif request_type and request_type.upper() == 'GETCAPABILITIES':
            resp_headers['Content-Disposition'] = 'inline'
        return Response(
            response.content,
            status=response.status_code,
            mimetype=mimetype,
            headers=resp_headers
        )
        
    except Exception as e:
        log.error(f"Error in WMS proxy: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


# ---------- SLD par ressource (plus de SLD au niveau dataset) ----------
@ogc.route('/<org_id>/sld/<dataset_id>/resource/<resource_id>/edit', methods=['GET'])
def edit_sld_resource(org_id, dataset_id, resource_id):
    """Page d'édition du style SLD d'une ressource."""
    try:
        from flask import render_template
        context = {'user': toolkit.c.user}
        package = toolkit.get_action('package_show')(context, {'id': dataset_id})
        resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        if resource.get('package_id') != package.get('id'):
            return toolkit.abort(404, toolkit._('Resource not found in this dataset'))
        layer_name = ogc_helpers.layer_name_for_resource(resource_id)
        return render_template(
            'package/resource_sld_style_edit.html',
            pkg_dict=package,
            resource=resource,
            layer_name=layer_name,
        )
    except toolkit.NotAuthorized:
        return toolkit.abort(403, toolkit._('Not authorized to access this page'))
    except toolkit.ObjectNotFound:
        return toolkit.abort(404, toolkit._('Dataset or resource not found'))
    except Exception as e:
        log.error(f"Error rendering SLD edit page for resource {resource_id}: {e}")
        return toolkit.abort(500, toolkit._('Internal server error'))


@ogc.route('/<org_id>/sld/<dataset_id>/resource/<resource_id>', methods=['GET'])
def get_sld_resource(org_id, dataset_id, resource_id):
    """Récupère le document SLD d'une ressource."""
    try:
        from flask import render_template, Response
        context = {'user': toolkit.c.user}
        package = toolkit.get_action('package_show')(context, {'id': dataset_id})
        resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        if resource.get('package_id') != package.get('id'):
            return toolkit.abort(404, toolkit._('Resource not found in this dataset'))
        sld_xml = ogc_helpers.get_sld_style_for_resource(dataset_id, resource_id)
        if not sld_xml:
            return toolkit.redirect_to('ogc.edit_sld_resource', org_id=org_id, dataset_id=dataset_id, resource_id=resource_id)
        format_param = request.args.get('format', 'html')
        if format_param == 'xml' or request.headers.get('Accept', '').startswith('application/xml'):
            return Response(
                sld_xml,
                mimetype='application/vnd.ogc.se+xml',
                headers={'Content-Type': 'application/vnd.ogc.se+xml; charset=utf-8', 'Cache-Control': 'public, max-age=3600'}
            )
        return render_template(
            'package/resource_sld_style_view.html',
            pkg_dict=package,
            resource=resource,
            sld_xml=sld_xml,
        )
    except toolkit.NotAuthorized:
        return toolkit.abort(403, toolkit._('Not authorized'))
    except toolkit.ObjectNotFound:
        return toolkit.abort(404, toolkit._('Not found'))
    except Exception as e:
        log.error(f"Error getting SLD for resource {resource_id}: {e}")
        return toolkit.abort(500, toolkit._('Internal server error'))


@ogc.route('/<org_id>/sld/<dataset_id>/resource/<resource_id>/delete', methods=['POST'])
def delete_sld_resource(org_id, dataset_id, resource_id):
    """Supprime le style SLD de la ressource (vide les extras et réinitialise le mapfile)."""
    try:
        if not toolkit.c.user:
            return toolkit.abort(401, toolkit._('You must be logged in to delete SLD styles'))
        context = {'user': toolkit.c.user}
        if hasattr(toolkit.c, 'userobj') and toolkit.c.userobj:
            context['auth_user_obj'] = toolkit.c.userobj
        try:
            package = toolkit.get_action('package_show')(context, {'id': dataset_id})
            resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        except toolkit.NotAuthorized:
            return toolkit.abort(403, toolkit._('Not authorized'))
        if resource.get('package_id') != package.get('id'):
            return toolkit.abort(404, toolkit._('Resource not found in this dataset'))
        success = ogc_helpers.delete_sld_style_for_resource(dataset_id, resource_id, context=context)
        if success:
            toolkit.h.flash_success(toolkit._('SLD style removed for this resource. Mapfile has been updated.'))
        else:
            toolkit.h.flash_error(toolkit._('Could not remove SLD style (it may already be empty).'))
        return toolkit.redirect_to('resource.read', id=package.get('name'), resource_id=resource_id)
    except Exception as e:
        log.error(f"Error deleting SLD for resource {resource_id}: {e}")
        toolkit.h.flash_error(toolkit._('Error removing SLD style.'))
        return toolkit.redirect_to('resource.read', id=dataset_id, resource_id=resource_id)


@ogc.route('/<org_id>/sld/<dataset_id>/resource/<resource_id>', methods=['POST', 'PUT'])
@ogc.route('/<org_id>/sld/<dataset_id>/resource/<resource_id>/edit', methods=['POST'])
def save_sld_resource(org_id, dataset_id, resource_id):
    """Sauvegarde le style SLD d'une ressource."""
    try:
        if not toolkit.c.user:
            return jsonify({'error': 'Not authenticated', 'message': 'You must be logged in to edit SLD styles'}), 401
        context = {'user': toolkit.c.user}
        if hasattr(toolkit.c, 'userobj') and toolkit.c.userobj:
            context['auth_user_obj'] = toolkit.c.userobj
        try:
            package = toolkit.get_action('package_show')(context, {'id': dataset_id})
            resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        except toolkit.NotAuthorized:
            return jsonify({'error': 'Not authorized', 'message': 'You do not have permission to edit this dataset'}), 403
        if resource.get('package_id') != package.get('id'):
            return toolkit.abort(404, toolkit._('Resource not found in this dataset'))
        sld_xml = None
        if request.form:
            sld_xml = request.form.get('sld_xml') or request.form.get('sld')
        if not sld_xml and request.is_json:
            sld_xml = request.json.get('sld_xml') or request.json.get('sld')
        if not sld_xml and request.data:
            try:
                sld_xml = request.data.decode('utf-8')
            except Exception:
                pass
        # Vide = suppression du SLD (CRUD: vidage)
        if not sld_xml or not (isinstance(sld_xml, str) and sld_xml.strip()):
            success = ogc_helpers.save_sld_style_for_resource(dataset_id, resource_id, '' if sld_xml is None else (sld_xml or ''), context=context)
        else:
            try:
                ET.fromstring(sld_xml)
            except ET.ParseError as e:
                return jsonify({'error': 'Invalid SLD XML', 'message': str(e)}), 400
            success = ogc_helpers.save_sld_style_for_resource(dataset_id, resource_id, sld_xml, context=context)
        if success:
            if request.content_type and 'application/x-www-form-urlencoded' in request.content_type:
                return toolkit.redirect_to('resource.read', id=package.get('name'), resource_id=resource_id)
            return jsonify({'success': True, 'message': 'SLD style saved for resource'}), 200
        if request.content_type and 'application/x-www-form-urlencoded' in request.content_type:
            return toolkit.abort(500, toolkit._('Failed to save SLD style'))
        return jsonify({'error': 'Failed to save SLD style', 'message': 'An error occurred'}), 500
    except Exception as e:
        log.error(f"Error saving SLD for resource {resource_id}: {e}")
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500


def get_blueprint():
    """Retourne les Blueprints pour enregistrement dans CKAN"""
    blueprints = [ogc, wms, wfs]
    # Ajouter le blueprint CSW si disponible
    if csw is not None:
        blueprints.append(csw)
        # log.info("Blueprint CSW ajouté")
    return blueprints

"""
Shared utilities for the OGC views (WMS, WFS, OGC API).

Common helpers used across the views: XML response building, invalid XML
tag-name sanitisation, CRS identifier normalisation, WFS exception XML,
BBox computation and reprojection, organization collection lookup, and
URL/API-key configuration.
"""
from typing import Any, Dict, List, Optional, Tuple
from flask import Response, jsonify
import logging
import os
import re
import requests
import json
import html
import xml.etree.ElementTree as ET
from ckan.plugins import toolkit

try:
    from pygeoapi import util as pygeoapi_util
except Exception:  # pragma: no cover
    pygeoapi_util = None

log = logging.getLogger(__name__)


def _safe_response(xml_content: str, mimetype: str = 'application/xml', headers: Dict = None, status: int = 200) -> Response:
    """Build a Flask response safely, handling OSError on write.

    Args:
        xml_content: XML/text body.
        mimetype: Response MIME type.
        headers: HTTP headers.
        status: HTTP status code.

    Returns:
        A Flask Response.
    """
    try:
        return Response(xml_content, mimetype=mimetype, headers=headers or {}, status=status)
    except OSError as e:
        # Logging détaillé de l'erreur OSError lors de la création de la réponse
        log.error(f"OSError lors de la création de la réponse HTTP")
        log.error(f"   Type d'erreur: {type(e).__name__}")
        log.error(f"   Message: {str(e)}")
        log.error(f"   Errno: {e.errno if hasattr(e, 'errno') else 'N/A'}")
        log.error(f"   Strerror: {e.strerror if hasattr(e, 'strerror') else 'N/A'}")
        log.error(f"   Filename: {e.filename if hasattr(e, 'filename') else 'N/A'}")
        log.error(f"   Taille du contenu: {len(xml_content.encode('utf-8')) if isinstance(xml_content, str) else len(xml_content)} bytes")
        log.error(f"   Mimetype: {mimetype}")
        import traceback
        log.error(f"   Traceback: {traceback.format_exc()}")
        # Réessayer avec une réponse d'erreur plus petite
        try:
            error_response = f'<?xml version="1.0" encoding="UTF-8"?><ServiceException>Internal server error: {html.escape(str(e))}</ServiceException>'
            return Response(error_response, mimetype='application/xml', status=500)
        except Exception as e2:
            log.error(f"Erreur lors de la création de la réponse d'erreur: {e2}")
            # Dernier recours: réponse texte simple
            return Response(f"Internal server error: {str(e)}", mimetype='text/plain', status=500)


def _fix_xml_invalid_tag_names(xml_content: bytes) -> bytes:
    """Post-process XML to fix invalid tag names (columns containing spaces).

    MapServer uses column names verbatim as XML tag names, which crashes
    the XML parser when column names contain spaces.

    Args:
        xml_content: Raw XML body (bytes).

    Returns:
        Corrected XML body (bytes).
    """
    # Vérifier si c'est du XML
    if not (xml_content.startswith(b'<?xml') or xml_content.startswith(b'<')):
        return xml_content
    
    try:
        xml_str = xml_content.decode('utf-8')
        import re
        
        # Créer un dictionnaire pour mapper les noms de balises avec espaces vers des noms valides
        tag_replacements = {}
        
        # Trouver toutes les balises avec des espaces dans les noms (format: <ms:Nom avec espaces>)
        def collect_invalid_tags(match):
            full_tag = match.group(0)
            tag_content = match.group(1)
            
            # Ignorer les balises de déclaration XML, commentaires, etc.
            if tag_content.startswith('?') or tag_content.startswith('!'):
                return full_tag
            
            # Extraire le namespace et le nom de la balise
            if ':' in tag_content:
                parts = tag_content.split(':', 1)
                namespace = parts[0]
                tag_name_with_attrs = parts[1]
                
                # Extraire juste le nom de la balise (avant les attributs ou la fin)
                tag_name_parts = tag_name_with_attrs.split()
                tag_name = tag_name_parts[0]
                
                # Si le nom contient des espaces, créer un mapping
                if ' ' in tag_name:
                    tag_name_fixed = tag_name.replace(' ', '_')
                    old_tag = f'{namespace}:{tag_name}'
                    new_tag = f'{namespace}:{tag_name_fixed}'
                    
                    if old_tag not in tag_replacements:
                        tag_replacements[old_tag] = new_tag
                        log.info(f"Mapping balise XML: '{old_tag}' -> '{new_tag}'")
            
            return full_tag
        
        # Première passe : collecter tous les noms de balises invalides
        re.sub(r'<([^>]+)>', collect_invalid_tags, xml_str)
        
        # Deuxième passe : remplacer toutes les occurrences
        if tag_replacements:
            for old_tag, new_tag in tag_replacements.items():
                # Remplacer les balises ouvrantes et fermantes
                xml_str = xml_str.replace(f'<{old_tag}>', f'<{new_tag}>')
                xml_str = xml_str.replace(f'<{old_tag} ', f'<{new_tag} ')
                xml_str = xml_str.replace(f'</{old_tag}>', f'</{new_tag}>')
            
            xml_content = xml_str.encode('utf-8')
            log.info(f"XML post-traité: {len(tag_replacements)} balises corrigées")
        else:
            log.debug("Aucune balise avec espaces trouvée dans le XML")
    except Exception as e:
        log.warning(f"Erreur lors du post-traitement XML: {e}, retour de la réponse brute")
        import traceback
        log.error(traceback.format_exc())
        # En cas d'erreur, retourner le contenu original
    
    return xml_content


def _normalize_crs_to_uri(crs_value: Optional[str]) -> str:
    """
    Normalize CRS identifiers (EPSG:XXXX, CRS:84, URN, etc.) to the canonical HTTP URI
    that pygeoapi expects (http://www.opengis.net/def/crs/...).
    """
    if not crs_value:
        return "http://www.opengis.net/def/crs/OGC/1.3/CRS84"

    crs_value = crs_value.strip()
    upper_value = crs_value.upper()

    crs_map = {
        "EPSG:4326": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "URN:OGC:DEF:CRS:EPSG::4326": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "CRS:84": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "CRS84": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "OGC:CRS84": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "EPSG:3857": "http://www.opengis.net/def/crs/EPSG/0/3857",
        "URN:OGC:DEF:CRS:EPSG::3857": "http://www.opengis.net/def/crs/EPSG/0/3857",
        "EPSG:2154": "http://www.opengis.net/def/crs/EPSG/0/2154",
        "URN:OGC:DEF:CRS:EPSG::2154": "http://www.opengis.net/def/crs/EPSG/0/2154",
    }

    if upper_value in crs_map:
        return crs_map[upper_value]

    if upper_value.startswith("EPSG:"):
        epsg_code = upper_value.split(":")[1]
        return f"http://www.opengis.net/def/crs/EPSG/0/{epsg_code}"

    if upper_value.startswith(("HTTP://", "HTTPS://")):
        return crs_value

    if upper_value.startswith("URN:OGC:DEF:CRS:"):
        # Example: URN:OGC:DEF:CRS:EPSG::XXXX
        parts = upper_value.split("::")
        if len(parts) == 2 and parts[0].endswith(":EPSG"):
            epsg_code = parts[1]
            return f"http://www.opengis.net/def/crs/EPSG/0/{epsg_code}"

    return "http://www.opengis.net/def/crs/OGC/1.3/CRS84"


def _normalize_crs_identifier(crs_value: Optional[str]) -> Optional[str]:
    """Normalize common CRS identifiers to official URIs."""
    if not crs_value:
        return crs_value

    crs_value = crs_value.strip()
    lower_value = crs_value.lower()

    if lower_value.startswith(('http://', 'https://', 'urn:')):
        return crs_value

    value_upper = crs_value.upper()

    mapping = {
        'EPSG:4326': 'urn:ogc:def:crs:EPSG::4326',
        'EPSG:3857': 'urn:ogc:def:crs:EPSG::3857',
        'CRS84': 'urn:ogc:def:crs:OGC::CRS84',
        'CRS:84': 'urn:ogc:def:crs:OGC::CRS84',
        'OGC:CRS84': 'urn:ogc:def:crs:OGC::CRS84',
        'EPSG:2154': 'urn:ogc:def:crs:EPSG::2154'
    }

    return mapping.get(value_upper, crs_value)


def _normalize_srsname(crs_value: Optional[str]) -> Optional[str]:
    """Return a URN suitable for SRSNAME when possible."""
    if not crs_value:
        return crs_value

    crs_value = crs_value.strip()
    lower = crs_value.lower()
    if lower.startswith('urn:'):
        return crs_value

    if lower.startswith(('http://', 'https://')):
        parts = crs_value.split('/')
        if len(parts) >= 3:
            code = parts[-1]
            authority = parts[-3]
            return f"urn:ogc:def:crs:{authority}::{code}"
        return crs_value

    value_upper = crs_value.upper()
    mapping = {
        'EPSG:4326': 'urn:ogc:def:crs:EPSG::4326',
        'EPSG:3857': 'urn:ogc:def:crs:EPSG::3857',
        'CRS84': 'urn:ogc:def:crs:OGC::CRS84',
        'OGC:CRS84': 'urn:ogc:def:crs:OGC::CRS84',
        'EPSG:2154': 'urn:ogc:def:crs:EPSG::2154'
    }

    return mapping.get(value_upper, crs_value)


def get_pygeoapi_url() -> str:
    """Return the pygeoapi URL from configuration."""
    pygeoapi_url = os.getenv('PYGEOAPI_URL', 'http://localhost:5001')
    # Remplacer ogc. par ogc.ckan2. dans l'URL (pour les environnements ckan2)
    # Exemple: https://ogc.dataizen.eu -> https://ogc.ckan2.dataizen.eu
    if 'ogc.' in pygeoapi_url and 'ogc.ckan2.' not in pygeoapi_url:
        pygeoapi_url = pygeoapi_url.replace('ogc.', 'ogc.ckan2.', 1)
    return pygeoapi_url


def get_ckan_api_key() -> str:
    """Return the CKAN API key from the environment."""
    return os.getenv('CKAN_API_KEY', '')



def _get_org_collections(org_id: str) -> List[Dict[str, Any]]:
    """Fetch CKAN datasets for an organization and match them with pygeoapi collections and MapServer mapfiles."""
    log.info(f"_get_org_collections called for org {org_id} - this will analyze ALL datasets")
    context = {'ignore_auth': True, 'use_cache': False}
    try:
        organization = None
        dataset_list = []

        try:
            organization = toolkit.get_action('organization_show')(context, {'id': org_id})
        except Exception as e1:
            log.warning(f"First attempt failed for {org_id}: {e1}")
            try:
                organization = toolkit.get_action('organization_show')(
                    context, {'id': org_id, 'include_datasets': False}
                )
            except Exception as e2:
                log.error(f"Error fetching organization {org_id}: {e2}")
                return None, (jsonify({'error': 'Organization not found', 'message': str(e2)}), 404)

        try:
            org_name_for_filter = organization.get('name') if organization else org_id
            search_result = toolkit.get_action('package_search')(
                context, {'fq': f'organization:{org_name_for_filter}', 'rows': 10000}
            )
            dataset_list = search_result.get('results', [])
            log.info(f"Found {len(dataset_list)} datasets for organization {org_id} (using filter: organization:{org_name_for_filter})")
        except Exception as e3:
            log.warning(f"Error fetching datasets for {org_id}: {e3}, trying organization_show as fallback")
            try:
                datasets = toolkit.get_action('organization_show')(context, {'id': org_id, 'include_datasets': True})
                dataset_list = datasets.get('packages', [])
            except Exception as e4:
                log.error(f"Error fetching datasets via organization_show: {e4}")
                dataset_list = []

        # Récupérer les collections depuis pygeoapi
        pygeoapi_url = get_pygeoapi_url()
        collections_data = {}
        all_pygeoapi_collections = []
        try:
            collections_response = requests.get(f"{pygeoapi_url}/collections", timeout=60)
            if collections_response.status_code == 200:
                collections_data = collections_response.json()
                all_pygeoapi_collections = [c.get('id') for c in collections_data.get('collections', [])]
        except Exception as e:
            log.warning(f"pygeoapi unavailable or error: {e}, continuing with MapServer mapfiles only")

        # On suppose que tous les datasets non-pygeoapi ont un mapfile MapServer
        # Les mapfiles sont générés automatiquement, donc pas besoin de vérifier
        # MapServer gérera l'erreur si un mapfile n'existe pas réellement
        log.info(f"Supposant que tous les datasets non-pygeoapi ont un mapfile MapServer")

        org_collections = []
        
        # Vérifier quels datasets ont une table dans datagis (priorité)
        datagis_datasets = set()
        try:
            try:
                import psycopg2
            except ImportError:
                psycopg2 = None
            
            if psycopg2:
                conn = psycopg2.connect(
                    host=os.getenv('POSTGRES_HOST', 'db'),
                    port=os.getenv('POSTGRES_PORT', '5432'),
                    dbname=os.getenv('DATAGIS_DB', 'datagis'),
                    user=os.getenv('POSTGRES_USER', 'ckan'),
                    password=os.getenv('POSTGRES_PASSWORD', 'ckan')
                )
                cur = conn.cursor()
                # Récupérer toutes les tables avec géométrie dans datagis
                cur.execute("""
                    SELECT DISTINCT f_table_name
                    FROM geometry_columns
                    WHERE f_table_schema = 'public'
                """)
                datagis_tables = {row[0] for row in cur.fetchall()}
                cur.close()
                conn.close()
                
                # Pour chaque dataset, vérifier s'il a une table correspondante dans datagis
                for dataset in dataset_list:
                    dataset_name = dataset.get('name')
                    if not dataset_name:
                        continue
                    
                    # Chercher une table qui correspond au dataset (par nom ou pattern)
                    clean_name = dataset_name.replace('-', '_')
                    for table_name in datagis_tables:
                        # Vérifier si le nom de la table contient le nom du dataset (ou vice versa)
                        if clean_name in table_name or table_name in clean_name:
                            datagis_datasets.add(dataset_name)
                            log.debug(f"Dataset {dataset_name} trouvé dans datagis (table: {table_name})")
                            break
            else:
                log.warning(" psycopg2 non disponible, impossible de vérifier datagis")
        except Exception as e:
            log.warning(f" Erreur lors de la vérification datagis: {e}")
        
        # STRATÉGIE: On suppose que TOUS les datasets ont un mapfile MapServer (générés automatiquement)
        # On priorise MapServer pour tous les datasets, même s'ils sont aussi dans pygeoapi
        # PRIORISER les datasets qui sont dans datagis
        
        # Séparer les datasets en deux groupes : datagis d'abord, puis les autres
        datasets_with_datagis = []
        datasets_without_datagis = []
        
        for dataset in dataset_list:
            dataset_name = dataset.get('name')
            if not dataset_name:
                continue
            
            if dataset_name in datagis_datasets:
                datasets_with_datagis.append(dataset)
            else:
                datasets_without_datagis.append(dataset)
        
        # Traiter d'abord les datasets avec datagis, puis les autres
        for dataset in datasets_with_datagis + datasets_without_datagis:
            dataset_name = dataset.get('name')
            if not dataset_name:
                continue
            
            # Déterminer la source des données (datagis, datastore, ou ogr)
            is_datagis = dataset_name in datagis_datasets
            is_datastore = False
            # Vérifier si le dataset a une ressource datastore_active avec géométrie
            for resource in dataset.get('resources', []):
                if resource.get('datastore_active'):
                    # Vérifier si la ressource a une colonne géométrie
                    # On peut le faire en vérifiant si le mapfile existe et utilise PostGIS datastore
                    is_datastore = True
                    break
            
            # Vérifier si le dataset est aussi dans pygeoapi
            pygeoapi_collection = None
            for collection in collections_data.get('collections', []):
                if collection.get('id') == dataset_name:
                    pygeoapi_collection = collection
                    break
            
            # Créer une collection pour ce dataset (priorité MapServer)
            # Si le dataset est aussi dans pygeoapi, on garde ses métadonnées mais on marque comme MapServer
            if pygeoapi_collection:
                # Dataset dans pygeoapi ET MapServer - utiliser MapServer mais garder les métadonnées pygeoapi
                collection = dict(pygeoapi_collection)  # Copier les métadonnées
                collection['_mapserver_only'] = True  # Mais utiliser MapServer pour les données
                collection['_datagis'] = is_datagis  # Flag pour indiquer si dans datagis
                collection['_datastore'] = is_datastore and not is_datagis  # Flag pour indiquer si datastore (et pas datagis)
                
                # Mettre à jour le BBOX avec le vrai BBOX depuis les données
                # Pour datastore, passer is_datagis=False pour forcer la recherche dans datastore
                dataset_bbox = _get_dataset_bbox(dataset, is_datagis=False if is_datastore else is_datagis)
                if dataset_bbox:
                    collection['extents'] = {
                        'spatial': {
                            'bbox': [dataset_bbox]
                        }
                    }
                    log.info(f"Dataset {dataset_name} - BBOX mis à jour: {dataset_bbox}")
                else:
                    log.warning(f"Dataset {dataset_name} - BBOX non trouvé, utilisation du BBOX par défaut")
                
                log.debug(f"Dataset {dataset_name} trouvé dans pygeoapi, mais utilisation de MapServer (datagis: {is_datagis}, datastore: {is_datastore})")
            else:
                # Dataset uniquement dans MapServer
                # Essayer de récupérer le vrai BBOX depuis les données
                dataset_bbox = _get_dataset_bbox(dataset, is_datagis)
                
                collection = {
                    'id': dataset_name,
                    'title': dataset.get('title', dataset_name),
                    'description': dataset.get('notes', ''),
                    'extents': {
                        'spatial': {
                            'bbox': [dataset_bbox] if dataset_bbox else [[-5.0, 41.0, 10.0, 51.0]]  # BBOX réel ou par défaut France
                        }
                    },
                    '_mapserver_only': True,  # Flag pour indiquer que c'est MapServer uniquement
                    '_datagis': is_datagis  # Flag pour indiquer si dans datagis
                }
                # Si ce n'est pas datagis mais qu'il y a une ressource datastore, c'est datastore
                if not is_datagis and is_datastore:
                    collection['_datastore'] = True
                elif not is_datagis and not is_datastore:
                    collection['_ogr'] = True  # Sinon c'est probablement OGR
            
            org_collections.append(collection)
        
        if org_collections:
            log.info(f"Found {len(org_collections)} collections for organization {org_id}: {[c.get('id') for c in org_collections]}")
        else:
            log.warning(f" No collections found for organization {org_id}. CKAN datasets: {[d.get('name') for d in dataset_list[:5]]}, pygeoapi collections: {all_pygeoapi_collections[:5]}")

        return org_collections, None

    except Exception as exc:
        log.error(f"Unexpected error fetching organization data for {org_id}: {exc}")
        return None, (jsonify({'error': 'Internal server error', 'message': str(exc)}), 500)


def _get_dataset_bbox(dataset: Dict[str, Any], is_datagis: bool = False) -> Optional[List[float]]:
    """
    Récupère le BBOX réel d'un dataset depuis le datastore ou datagis
    
    Args:
        dataset: Dictionnaire du dataset CKAN
        is_datagis: True si le dataset est dans datagis
        
    Returns:
        BBOX [minx, miny, maxx, maxy] en WGS84 ou None
    """
    try:
        dataset_name = dataset.get('name')
        if not dataset_name:
            return None
        
        # Essayer d'abord depuis datagis si disponible
        if is_datagis:
            try:
                import psycopg2
                conn = psycopg2.connect(
                    host=os.getenv('POSTGRES_HOST', 'db'),
                    port=os.getenv('POSTGRES_PORT', '5432'),
                    dbname=os.getenv('DATAGIS_DB', 'datagis'),
                    user=os.getenv('POSTGRES_USER', 'ckan'),
                    password=os.getenv('POSTGRES_PASSWORD', 'ckan')
                )
                cur = conn.cursor()
                
                # Chercher la table correspondante
                clean_name = dataset_name.replace('-', '_')
                cur.execute("""
                    SELECT f_table_name, f_geometry_column, srid
                    FROM geometry_columns
                    WHERE f_table_schema = 'public'
                    AND (f_table_name = %s OR f_table_name LIKE %s)
                    LIMIT 1;
                """, (clean_name, f'%{clean_name}%'))
                
                result = cur.fetchone()
                if result:
                    table_name, geom_col, srid = result
                    # Récupérer le BBOX
                    cur.execute(f"""
                        SELECT ST_Extent({geom_col})::text
                        FROM "{table_name}"
                    """)
                    bbox_result = cur.fetchone()
                    if bbox_result and bbox_result[0]:
                        # Format: BOX(minx miny,maxx maxy)
                        bbox_str = bbox_result[0]
                        import re
                        match = re.search(r'BOX\(([-\d.]+)\s+([-\d.]+),([-\d.]+)\s+([-\d.]+)\)', bbox_str)
                        if match:
                            minx, miny, maxx, maxy = float(match.group(1)), float(match.group(2)), float(match.group(3)), float(match.group(4))
                            cur.close()
                            conn.close()
                            # Si le SRID n'est pas 4326, transformer
                            if srid != 4326:
                                try:
                                    from pyproj import Transformer
                                    transformer = Transformer.from_crs(f"EPSG:{srid}", "EPSG:4326", always_xy=True)
                                    minx, miny = transformer.transform(minx, miny)
                                    maxx, maxy = transformer.transform(maxx, maxy)
                                except Exception:
                                    pass
                            return [minx, miny, maxx, maxy]
                
                cur.close()
                conn.close()
            except Exception as e:
                log.warning(f" Erreur récupération BBOX depuis datagis pour {dataset_name}: {e}")
        
        # Sinon, essayer depuis le datastore
        for resource in dataset.get('resources', []):
            if resource.get('datastore_active'):
                resource_id = resource.get('id')
                try:
                    import psycopg2
                    conn = psycopg2.connect(
                        host=os.getenv('POSTGRES_HOST', 'db'),
                        port=os.getenv('POSTGRES_PORT', '5432'),
                        dbname=os.getenv('POSTGRES_DB', 'datastore'),
                        user=os.getenv('POSTGRES_USER', 'ckan'),
                        password=os.getenv('POSTGRES_PASSWORD', 'ckan')
                    )
                    cur = conn.cursor()
                    
                    # Chercher une colonne géométrie dans geometry_columns (plus fiable que information_schema)
                    cur.execute("""
                        SELECT f_geometry_column 
                        FROM geometry_columns 
                        WHERE f_table_name = %s 
                        AND f_table_schema = 'public'
                        LIMIT 1;
                    """, (resource_id,))
                    
                    geom_col = cur.fetchone()
                    if not geom_col or (isinstance(geom_col, tuple) and len(geom_col) == 0):
                        # Fallback: chercher dans information_schema
                        cur.execute("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = %s 
                            AND (column_name IN ('geometry', 'geom', 'the_geom', 'shape', 'geo_shape')
                                 OR column_name LIKE '%geom%')
                            LIMIT 1;
                        """, (resource_id,))
                        geom_col = cur.fetchone()
                    
                    # Vérifier que geom_col existe, est un tuple/liste non vide, et a au moins un élément
                    if geom_col and isinstance(geom_col, (tuple, list)) and len(geom_col) > 0:
                        geom_col_name = geom_col[0]
                        # Récupérer le BBOX directement avec ST_Extent
                        try:
                            cur.execute(f"""
                                SELECT ST_Extent({geom_col_name})::text
                                FROM "{resource_id}"
                                WHERE {geom_col_name} IS NOT NULL
                            """)
                            bbox_result = cur.fetchone()
                            if bbox_result and len(bbox_result) > 0 and bbox_result[0]:
                                # Format: BOX(minx miny,maxx maxy)
                                bbox_str = bbox_result[0]
                                import re
                                match = re.search(r'BOX\(([-\d.]+)\s+([-\d.]+),([-\d.]+)\s+([-\d.]+)\)', bbox_str)
                                if match:
                                    minx, miny, maxx, maxy = float(match.group(1)), float(match.group(2)), float(match.group(3)), float(match.group(4))
                                    cur.close()
                                    conn.close()
                                    log.debug(f"BBOX récupéré depuis datastore pour {resource_id}: [{minx}, {miny}, {maxx}, {maxy}]")
                                    return [minx, miny, maxx, maxy]
                                else:
                                    log.warning(f"Format BBOX non reconnu pour {resource_id}: {bbox_str}")
                            else:
                                log.warning(f"Pas de BBOX trouvé pour {resource_id} (table vide ou pas de géométrie)")
                        except Exception as e:
                            log.warning(f"Erreur lors de la récupération du BBOX pour {resource_id}: {e}")
                    else:
                        log.warning(f"Pas de colonne géométrie trouvée pour {resource_id}")
                    
                    cur.close()
                    conn.close()
                except Exception as e:
                    log.warning(f" Erreur récupération BBOX depuis datastore pour {resource_id}: {e}")
        
        return None
    except Exception as e:
        log.warning(f" Erreur générale récupération BBOX pour {dataset.get('name', 'unknown')}: {e}")
        return None


def _transform_bbox_to_wgs84(minx: float, miny: float, maxx: float, maxy: float, source_crs: Optional[str] = None) -> tuple:
    """
    Convertit un BBOX vers WGS84/CRS84 si nécessaire
    
    Args:
        minx, miny, maxx, maxy: Coordonnées du BBOX
        source_crs: CRS d'origine (CRS84, EPSG:4326, EPSG:2154, etc.)
        
    Returns:
        Tuple (minx, miny, maxx, maxy) en WGS84/CRS84 (géodétiquement identiques)
        
    Note:
        WGS84 (EPSG:4326) et CRS84 sont géodétiquement identiques.
        La différence est que CRS84 garantit l'ordre lon,lat (comme GeoJSON).
        Cette fonction transforme vers EPSG:4326 pour pyproj, mais le résultat
        est compatible avec CRS84.
    """
    if source_crs:
        # Si le CRS source est déjà CRS84 ou EPSG:4326, pas de transformation nécessaire
        source_crs_upper = str(source_crs).upper()
        if 'CRS84' in source_crs_upper or 'EPSG:4326' in source_crs_upper or '4326' in source_crs_upper:
            return (minx, miny, maxx, maxy)
        
        try:
            from pyproj import CRS, Transformer
            source = CRS.from_user_input(source_crs)
            target = CRS.from_epsg(4326)

            if source == target:
                return (minx, miny, maxx, maxy)

            transformer = Transformer.from_crs(source, target, always_xy=True)
            minx_wgs84, miny_wgs84 = transformer.transform(minx, miny)
            maxx_wgs84, maxy_wgs84 = transformer.transform(maxx, maxy)
            log.info(
                "Converted BBOX from %s (%s, %s, %s, %s) to WGS84 (%s, %s, %s, %s)",
                source_crs,
                minx,
                miny,
                maxx,
                maxy,
                minx_wgs84,
                miny_wgs84,
                maxx_wgs84,
                maxy_wgs84,
            )
            return (minx_wgs84, miny_wgs84, maxx_wgs84, maxy_wgs84)
        except ImportError:
            log.warning("pyproj not available, cannot transform BBOX")
        except Exception as exc:
            log.error("Error transforming BBOX from %s: %s", source_crs, exc)

    # Heuristic fallback for Lambert-93 (EPSG:2154)
    # Lambert-93 coordinates are typically:
    # X: 600000 - 1200000 (mètres)
    # Y: 6000000 - 7200000 (mètres)
    # On utilise une détection plus précise pour éviter les faux positifs
    is_lambert93 = (
        (600000 <= minx <= 1200000 and 600000 <= maxx <= 1200000) or
        (6000000 <= miny <= 7200000 and 6000000 <= maxy <= 7200000)
    )

    if not is_lambert93:
        # Si ce n'est pas Lambert-93 et que les valeurs sont dans la plage WGS84/CRS84, retourner tel quel
        if -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90:
            return (minx, miny, maxx, maxy)
        # Sinon, on ne peut pas déterminer le CRS, retourner tel quel
        log.warning(f"Cannot determine CRS for BBOX [{minx}, {miny}, {maxx}, {maxy}], returning as-is")
        return (minx, miny, maxx, maxy)

    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        minx_wgs84, miny_wgs84 = transformer.transform(minx, miny)
        maxx_wgs84, maxy_wgs84 = transformer.transform(maxx, maxy)
        log.info(
            "Converted BBOX from Lambert-93 (%s, %s, %s, %s) to WGS84 (%s, %s, %s, %s)",
            minx,
            miny,
            maxx,
            maxy,
            minx_wgs84,
            miny_wgs84,
            maxx_wgs84,
            maxy_wgs84,
        )
        return (minx_wgs84, miny_wgs84, maxx_wgs84, maxy_wgs84)
    except ImportError:
        log.warning("pyproj not available, cannot transform BBOX")
        return (minx, miny, maxx, maxy)
    except Exception as e:
        log.error(f"Error transforming BBOX: {e}")
        return (minx, miny, maxx, maxy)


def _create_wfs_exception(exception_code: str, exception_text: str, locator: str = '') -> Response:
    """
    Crée une réponse d'erreur WFS au format XML
    
    Args:
        exception_code: Code d'erreur OWS
        exception_text: Message d'erreur
        locator: Localisateur de l'erreur (paramètre concerné)
        
    Returns:
        Response XML avec l'exception
    """
    root = ET.Element('ows:ExceptionReport')
    root.set('xmlns:ows', 'http://www.opengis.net/ows/1.1')
    root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    root.set('version', '2.0.0')
    
    exception = ET.SubElement(root, 'ows:Exception')
    exception.set('exceptionCode', exception_code)
    if locator:
        exception.set('locator', locator)
    
    exception_text_elem = ET.SubElement(exception, 'ows:ExceptionText')
    exception_text_elem.text = exception_text
    
    ET.indent(root, space='  ')
    xml_str = ET.tostring(root, encoding='unicode', xml_declaration=True)
    
    return Response(
        xml_str,
        mimetype='application/xml',
        headers={'Content-Type': 'application/xml; charset=utf-8'},
        status=400
    )



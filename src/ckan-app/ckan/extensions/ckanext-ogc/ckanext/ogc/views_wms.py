"""
Implémentation des opérations WMS (Web Map Service).

Fonctions privées utilisées par les routes WMS pour générer GetCapabilities
(avec réécriture des URLs MapServer vers CKAN), valider les mapfiles et
résoudre les datasets pour une couche donnée dans une organisation.
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

from ckanext.ogc.views_utils import _get_org_collections

log = logging.getLogger(__name__)


def _validate_mapfile_for_wms(dataset_name: str, dataset: Dict = None) -> Tuple[bool, Optional[str]]:
    """
    Valide qu'un mapfile est utilisable pour WMS (existe, datasource accessible, table existe)
    
    Args:
        dataset_name: Nom du dataset
        dataset: Dictionnaire du dataset (optionnel, récupéré si non fourni)
        
    Returns:
        Tuple (is_valid, error_message)
    """
    mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
    mapfile_path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
    
    # 1. Vérifier que le mapfile existe
    if not os.path.exists(mapfile_path):
        return False, f"Mapfile not found: {mapfile_path}"
    
    # 2. Récupérer les métadonnées OGC depuis les extras du dataset
    source_type = None
    table_name = None
    data_source = None
    
    if dataset:
        extras = dataset.get('extras', [])
        if isinstance(extras, list):
            for extra in extras:
                if isinstance(extra, dict):
                    key = extra.get('key', '')
                    value = extra.get('value', '')
                    if key == 'ogc:source':
                        source_type = value
                    elif key == 'ogc:table':
                        table_name = value
    elif not dataset:
        # Récupérer le dataset depuis CKAN
        try:
            from ckan.plugins import toolkit
            context = {'ignore_auth': True}
            dataset = toolkit.get_action('package_show')(context, {'id': dataset_name})
            extras = dataset.get('extras', [])
            if isinstance(extras, list):
                for extra in extras:
                    if isinstance(extra, dict):
                        key = extra.get('key', '')
                        value = extra.get('value', '')
                        if key == 'ogc:source':
                            source_type = value
                        elif key == 'ogc:table':
                            table_name = value
        except Exception as e:
            log.debug(f"Erreur récupération dataset pour validation mapfile: {e}")
    
    # 3. Si c'est PostGIS (datagis ou datastore), vérifier que la table existe
    if source_type in {'datagis', 'datastore'} and table_name:
        try:
            import psycopg2
            postgis_host = os.getenv('POSTGIS_HOST', 'db')
            postgis_port = int(os.getenv('POSTGIS_PORT', '5432'))
            postgis_db = 'datagis' if source_type == 'datagis' else os.getenv('POSTGIS_DB', 'datastore')
            postgis_user = os.getenv('POSTGIS_USER', 'ckan')
            postgis_password = os.getenv('POSTGIS_PASSWORD', '')
            
            log.debug(f"Vérification table PostGIS: {table_name} dans {postgis_db} (source: {source_type})")
            
            conn = psycopg2.connect(
                host=postgis_host,
                port=postgis_port,
                dbname=postgis_db,
                user=postgis_user,
                password=postgis_password
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                );
            """, (table_name,))
            result = cur.fetchone()
            table_exists = result and len(result) > 0 and result[0] if result else False
            cur.close()
            conn.close()
            
            if not table_exists:
                log.warning(f"Table PostGIS non trouvée: {table_name} dans {postgis_db} (source: {source_type})")
                return False, f"PostGIS table not found: {table_name} (source: {source_type}, database: {postgis_db})"
            else:
                log.debug(f"Table PostGIS trouvée: {table_name} dans {postgis_db}")
        except ImportError:
            log.debug("psycopg2 non disponible, impossible de valider la table PostGIS")
        except Exception as e:
            log.warning(f"Erreur validation table PostGIS pour {table_name}: {e}")
            # Ne pas bloquer si on ne peut pas valider, mais logger l'erreur
    
    # 4. Si c'est OGR, vérifier que la datasource est accessible (test léger)
    # Note: Pour OGR, on ne peut pas facilement tester sans lire le mapfile complet
    # On fait confiance à la validation faite lors de la génération
    
    # 5. Test final: vérifier que MapServer peut charger le mapfile
    mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
    try:
        test_params = {'map': f"/mapserver/mapfiles/{dataset_name}.map", 'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetCapabilities'}
        log.debug(f"Test MapServer pour {dataset_name}: {mapserver_url}/wms avec map={test_params['map']}")
        test_response = requests.get(f"{mapserver_url}/wms", params=test_params, timeout=5)
        
        # Récupérer le texte de la réponse en premier
        response_text = test_response.text if hasattr(test_response, 'text') else str(test_response.content[:1000])
        
        # Vérifier le Content-Type
        content_type = test_response.headers.get('Content-Type', '').lower()
        is_html = 'text/html' in content_type
        is_xml = 'application/xml' in content_type or 'text/xml' in content_type or (response_text and response_text.strip().startswith('<?xml'))
        
        # "MS_SERVER SUPPORTS=..." n'est pas une erreur, c'est juste la sortie texte de MapServer
        # Ignorer cette sortie si elle est suivie d'un XML valide
        if 'MS_SERVER SUPPORTS=' in response_text and ('<?xml' in response_text or '<' in response_text):
            # Extraire la partie XML si présente
            xml_start = response_text.find('<?xml')
            if xml_start > 0:
                response_text = response_text[xml_start:]
        
        # Détecter les erreurs MapServer communes (mais pas "MS_SERVER SUPPORTS=" qui est normal)
        mapserver_errors = [
            'msDrawMap', 'msOGRFileOpen', 'msLoadMap', 'msPostGISLayerOpen',
            'msPostGISConnect', 'msPostGISExecuteSQL', 'msPostGISLayerWhichShapes',
            'msPostGISLayerGetItems', 'msPostGISLayerGetExtent', 'msPostGISLayerClose',
            'msPostGISLayerGetShape', 'msProcessProjection', 'proj error',
            'Unknown error', 'Projection library error'
        ]
        
        # Ne pas considérer "MS_SERVER SUPPORTS=" comme une erreur
        has_mapserver_error = any(error in response_text for error in mapserver_errors) and 'MS_SERVER SUPPORTS=' not in response_text
        
        if test_response.status_code != 200:
            # Status code non-200 = erreur HTTP
            error_msg = f"MapServer returned HTTP error (status: {test_response.status_code})"
            if has_mapserver_error:
                error_msg += f": {response_text[:300]}"
            log.warning(f"{error_msg}")
            return False, error_msg
        elif is_html and not is_xml and has_mapserver_error:
            # Status 200 mais contenu HTML avec erreur MapServer = mapfile invalide
            # Extraire le message d'erreur MapServer
            error_msg = response_text[:500]
            # Chercher un message d'erreur plus spécifique
            error_match = re.search(r'(ms\w+.*?|proj error.*?|Projection library error.*?)(?:\n|$)', response_text, re.IGNORECASE)
            if error_match:
                error_msg = error_match.group(1).strip()
            log.warning(f"Erreur MapServer pour {dataset_name}: {error_msg}")
            return False, f"MapServer error loading mapfile: {error_msg}"
        elif is_html and not is_xml and 'MS_SERVER SUPPORTS=' in response_text:
            # MapServer a retourné du texte au lieu de XML, mais ce n'est pas forcément une erreur
            # Vérifier si c'est juste la sortie de capacités (sans XML)
            log.debug(f"MapServer a retourné du texte au lieu de XML pour {dataset_name}, mais ce n'est pas forcément une erreur")
            # Ne pas considérer cela comme une erreur bloquante
            return True, None
        elif is_html and not is_xml:
            log.warning(f"MapServer a retourné HTML au lieu de XML pour {dataset_name} (Content-Type: {content_type})")
            return False, f"MapServer returned HTML instead of XML (status: 200, Content-Type: {content_type})"
        else:
            log.debug(f"MapServer a validé le mapfile pour {dataset_name}")
    except requests.exceptions.RequestException as e:
        log.warning(f"Erreur test MapServer pour {dataset_name}: {e}")
        # Ne pas bloquer si on ne peut pas tester, mais logger l'erreur
    
    return True, None


def _replace_mapserver_url_in_getcapabilities(url_match: str, ckan_site_url: str, org_id: str, dataset_name: str, layer_name: str) -> str:
    """
    Remplace une URL MapServer interne par l'URL du proxy CKAN dans un GetCapabilities.
    
    Args:
        url_match: URL complète de MapServer à remplacer
        ckan_site_url: URL de base CKAN (déjà échappée pour XML si nécessaire)
        org_id: ID de l'organisation
        dataset_name: Nom du dataset
        layer_name: Nom du layer (peut être dataset_name ou dataset_name_2, etc.)
        
    Returns:
        URL remplacée avec le proxy CKAN
    """
    import re
    from urllib.parse import parse_qs, urlencode, urlparse, unquote
    
    try:
        # Extraire l'URL complète (peut être dans un attribut href avec guillemets)
        url_str = url_match.strip().rstrip('"').rstrip("'").rstrip('>').rstrip()
        # URLs relatives : /wms?map=... (urlparse met query dans .query)
        if url_str.startswith('/') and '?' in url_str:
            path, _, query_part = url_str.partition('?')
            url_str = f"http://dummy{path}?{query_part}"
        parsed = urlparse(url_str)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        
        # Construire la nouvelle URL avec le proxy CKAN
        # Format: /wms/{org_id}?LAYERS={layer_name}&SERVICE=...&REQUEST=...
        # IMPORTANT: Utiliser layer_name (peut être dataset_name ou dataset_name_2) pour que le bon layer soit affiché
        new_query = {'LAYERS': layer_name}
        
        # Copier les paramètres existants (SERVICE, REQUEST, VERSION, BBOX, etc.) mais retirer 'map' et 'LAYERS' (on utilise layer_name)
        for key, values in query_params.items():
            key_upper = key.upper()
            if key_upper not in ['MAP', 'LAYERS']:  # Retirer MAP et LAYERS (on utilise notre layer_name)
                # Prendre la première valeur si c'est une liste
                if isinstance(values, list):
                    if len(values) > 0:
                        value = values[0]
                    else:
                        value = ''
                else:
                    value = values if values is not None else ''
                new_query[key_upper] = value
        # Tout passer en WMS 1.1.0 pour compatibilité SLD / QGIS
        new_query['VERSION'] = '1.1.0'
        
        # Construire l'URL complète
        new_path = f"/wms/{org_id}"
        # Utiliser urlencode avec doseq=False pour éviter les listes (on a déjà pris la première valeur)
        new_query_str = urlencode(new_query, doseq=False)
        new_url = f"{ckan_site_url}{new_path}?{new_query_str}"
        
        # Échapper les & pour XML si nécessaire (mais ne pas double-échapper)
        if '&amp;' not in ckan_site_url:
            new_url = new_url.replace('&', '&amp;')
        
        log.debug(f"URL MapServer remplacée: {url_str[:100]}... -> {new_url[:100]}...")
        return new_url
    except Exception as e:
        log.warning(f"Erreur lors du remplacement d'URL MapServer '{url_match[:100]}...': {e}, fallback sur URL simple")
        import traceback
        log.debug(traceback.format_exc())
        # Fallback: utiliser une URL simple
        fallback_url = f"{ckan_site_url}/wms/{org_id}?LAYERS={layer_name}&VERSION=1.1.0"
        if '&amp;' not in ckan_site_url:
            fallback_url = fallback_url.replace('&', '&amp;')
        return fallback_url


def _rewrite_wms_getcapabilities_for_ckan(org_id: str, dataset_name: str, xml_content: str, ckan_site_url: str) -> str:
    """
    Réécrit un GetCapabilities WMS MapServer pour CKAN :
    - Remplace toutes les URLs MapServer par l'URL du proxy CKAN (sans map=), VERSION=1.1.0
    - Ajoute les styles issus des extras dataset (SLD) au niveau de chaque Layer concerné
    """
    ckan_site_url_escaped = ckan_site_url.replace('&', '&amp;')
    layer_name_for_url = dataset_name

    # 1) Styles SLD par layer (extras dataset) : layer_name -> style_name
    layer_extra_styles = {}
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': dataset_name})
        package_id = package.get('id')
        for res in package.get('resources') or []:
            rid = res.get('id')
            if not rid:
                continue
            ln = ogc_helpers.layer_name_for_resource(rid)
            if not ln:
                continue
            sld = ogc_helpers.get_sld_style_for_resource(package_id, rid)
            if sld:
                style_name = ogc_helpers.get_sld_style_name(sld) or 'default'
                layer_extra_styles[ln] = style_name
    except Exception as e:
        log.debug(f"Impossible de charger les styles SLD pour {dataset_name}: {e}")

    # 2) Remplacer TOUTES les URLs WMS (MapServer ou relatives /wms?map=...) par l'URL CKAN proxy org (sans map=)
    def is_wms_url_to_rewrite(url_val):
        if not url_val or not url_val.strip():
            return False
        u = url_val.strip()
        if 'map=' in u and ('/wms' in u or 'wms?' in u):
            return True
        if u.startswith('/wms?'):
            return True
        if re.match(r'https?://[^/]+/wms\?', u):
            return True
        return False

    def replace_href_url(match):
        attr = match.group(1)  # xlink:href ou href
        quoted = match.group(2)  # "..." ou '...'
        quote_char = quoted[0] if quoted else '"'
        url_val = (match.group(3) or match.group(4) or '').replace('&amp;', '&')
        if not is_wms_url_to_rewrite(url_val):
            return match.group(0)
        replaced = _replace_mapserver_url_in_getcapabilities(
            url_val, ckan_site_url_escaped, org_id, dataset_name, layer_name_for_url
        )
        return f'{attr}={quote_char}{replaced}{quote_char}'

    # xlink:href="..." ou href="..." ou href='...'
    xml_content = re.sub(
        r'((?:xlink:)?href)=("([^"]*)"|\'([^\']*)\')',
        replace_href_url,
        xml_content
    )
    # Anciennes URLs absolues MapServer (localhost, mapserver:80)
    xml_content = re.sub(
        r'http://(localhost:8081|mapserver:80)/wms\?[^"\'<>\s]+',
        lambda m: _replace_mapserver_url_in_getcapabilities(
            m.group(0).rstrip('"').rstrip("'").rstrip('>').rstrip(),
            ckan_site_url_escaped, org_id, dataset_name, layer_name_for_url
        ) + ('"' if m.group(0).endswith('"') else "'" if m.group(0).endswith("'") else ">"),
        xml_content
    )
    xml_content = re.sub(
        r'http://(localhost:8081|mapserver:80)/wms(?![?"\'<&])',
        f"{ckan_site_url_escaped}/wms/{org_id}".replace('&', '&amp;'),
        xml_content
    )

    # 3) Ajouter les styles (UserStyle des extras) dans chaque Layer concerné
    if layer_extra_styles:
        try:
            root = ET.fromstring(xml_content)
            for elem in root.iter():
                tag_local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag_local != 'Layer':
                    continue
                name_elems = list(elem.findall('{*}Name') or elem.findall('Name'))
                if not name_elems or not name_elems[0].text:
                    continue
                layer_name = name_elems[0].text.strip()
                if layer_name not in layer_extra_styles:
                    continue
                style_name = layer_extra_styles[layer_name]
                # Éviter doublon
                has_style = False
                for child in elem:
                    ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if ctag == 'Style':
                        n = list(child.findall('{*}Name') or child.findall('Name'))
                        if n and n[0].text and n[0].text.strip() == style_name:
                            has_style = True
                            break
                if not has_style:
                    ns = elem.tag.rsplit('}', 1)[0] + '}' if '}' in elem.tag else ''
                    style_elem = ET.Element(f'{ns}Style')
                    ET.SubElement(style_elem, f'{ns}Name').text = style_name
                    ET.SubElement(style_elem, f'{ns}Title').text = style_name
                    # Insérer en premier pour que ce style soit le style par défaut (QGIS prend le 1er)
                    elem.insert(0, style_elem)
                    log.debug(f"GetCapabilities: style {style_name} ajouté en style par défaut pour layer {layer_name}")
            xml_content = ET.tostring(root, encoding='unicode', default_namespace='', method='xml')
        except ET.ParseError as e:
            log.warning(f"GetCapabilities: parse XML pour injection styles: {e}")
        except Exception as e:
            log.warning(f"GetCapabilities: injection styles: {e}")

    # 4) Forcer version 1.1.0 dans la racine si présent
    xml_content = re.sub(r'version="1\.3\.0"', 'version="1.1.0"', xml_content, count=1)
    xml_content = re.sub(r'version=\'1\.3\.0\'', 'version=\'1.1.0\'', xml_content, count=1)

    return xml_content


def _find_dataset_for_layer_in_org(org_id: str, layer_name: str):
    """
    Trouve le dataset (dans l'org) dont le mapfile contient une couche nommée layer_name.
    Les mapfiles peuvent avoir des layers nommés d'après les ressources (Axes_de_ruissellement_potentiels, etc.)
    et non plus uniquement dataset_name / dataset_name_2.
    
    Returns:
        dataset_name (str) si trouvé, None sinon.
    """
    import re
    org_collections, err = _get_org_collections(org_id)
    if err or not org_collections:
        return None
    mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
    # Chercher dans le contenu du mapfile: NAME "layer_name"
    needle = f'NAME "{layer_name}"'
    for coll in org_collections:
        dataset_name = coll.get('id')
        if not dataset_name:
            continue
        path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                if needle in f.read():
                    log.debug(f"Layer '{layer_name}' trouvé dans mapfile {dataset_name}.map")
                    return dataset_name
        except Exception as e:
            log.debug(f"Lecture mapfile {path}: {e}")
            continue
    return None


def _generate_wms_capabilities(org_id: str, collections: List[Dict]) -> Response:
    """
    Génère un document WMS GetCapabilities pour l'organisation avec toutes les couches
    
    Args:
        org_id: ID de l'organisation
        collections: Liste des collections de l'organisation
        
    Returns:
        Réponse XML avec les capabilities WMS
    """
    # Utiliser le helper qui détecte automatiquement HTTPS
    ckan_site_url = ogc_helpers.get_ogc_base_url()
    
    def escape_xml(text):
        """Escape XML special characters"""
        if text is None:
            return ''
        text_str = str(text)
        text_str = text_str.replace('&amp;', '&')
        text_str = text_str.replace('&lt;', '<')
        text_str = text_str.replace('&gt;', '>')
        text_str = text_str.replace('&quot;', '"')
        text_str = text_str.replace('&apos;', "'")
        return html.escape(text_str)
    
    def get_collection_bbox(collection):
        """Extract BBOX from collection extents"""
        extents = collection.get('extents', {})
        spatial = extents.get('spatial', {})
        bbox_list = spatial.get('bbox', [])
        
        if bbox_list and len(bbox_list) > 0:
            if isinstance(bbox_list[0], list):
                bbox = bbox_list[0]
            else:
                bbox = bbox_list
            if len(bbox) >= 4:
                return bbox[0], bbox[1], bbox[2], bbox[3]
        
        # Default BBOX for France
        return -5.0, 41.0, 10.0, 51.0
    
    # Générer le XML WMS GetCapabilities (WMS 1.1.0 pour compatibilité SLD / QGIS)
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<WMS_Capabilities version="1.1.0" xmlns="http://www.opengis.net/wms"
                 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xsi:schemaLocation="http://www.opengis.net/wms http://schemas.opengis.net/wms/1.1.0/capabilities_1_1_0.xsd">
  <Service>
    <Name>WMS</Name>
    <Title>WMS Service for Organization {escape_xml(org_id)}</Title>
    <Abstract>Web Map Service providing all layers from CKAN organization {escape_xml(org_id)}</Abstract>
    <OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{ckan_site_url}/wms/{org_id}?"/>
    <ContactInformation>
    </ContactInformation>
  </Service>
  <Capability>
    <Request>
      <GetCapabilities>
        <Format>text/xml</Format>
        <DCPType>
          <HTTP>
            <Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{ckan_site_url}/wms/{org_id}?SERVICE=WMS&amp;VERSION=1.1.0&amp;REQUEST=GetCapabilities"/></Get>
          </HTTP>
        </DCPType>
      </GetCapabilities>
      <GetMap>
        <Format>image/png</Format>
        <Format>image/jpeg</Format>
        <Format>image/png; mode=8bit</Format>
        <DCPType>
          <HTTP>
            <Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{ckan_site_url}/wms/{org_id}?SERVICE=WMS&amp;VERSION=1.1.0&amp;REQUEST=GetMap"/></Get>
          </HTTP>
        </DCPType>
      </GetMap>
      <GetFeatureInfo>
        <Format>text/html</Format>
        <Format>application/vnd.ogc.gml</Format>
        <Format>text/plain</Format>
        <DCPType>
          <HTTP>
            <Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{ckan_site_url}/wms/{org_id}?SERVICE=WMS&amp;VERSION=1.1.0&amp;REQUEST=GetFeatureInfo"/></Get>
          </HTTP>
        </DCPType>
      </GetFeatureInfo>
    </Request>
    <Exception>
      <Format>application/vnd.ogc.se_xml</Format>
      <Format>application/vnd.ogc.se_inimage</Format>
      <Format>application/vnd.ogc.se_blank</Format>
    </Exception>
    <Layer>
      <Title>Layers for {escape_xml(org_id)}</Title>
      <CRS>EPSG:4326</CRS>
      <CRS>EPSG:3857</CRS>
      <CRS>EPSG:2154</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>-5.0</westBoundLongitude>
        <eastBoundLongitude>10.0</eastBoundLongitude>
        <southBoundLatitude>41.0</southBoundLatitude>
        <northBoundLatitude>51.0</northBoundLatitude>
      </EX_GeographicBoundingBox>'''
    
    # OPTIMISATION: Interroger MapServer en parallèle pour tous les datasets
    mapserver_url = os.getenv('MAPSERVER_URL', 'http://mapserver:80')
    
    def _extract_layers_from_mapfile_file(mapfile_path: str, collection_id: str) -> List[Dict]:
        """Extrait les noms de layers depuis un mapfile (fallback si MapServer inaccessible)."""
        layers_found = []
        mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
        path = mapfile_path if os.path.isabs(mapfile_path) else os.path.join(mapfiles_dir, mapfile_path.lstrip('/'))
        if not os.path.isfile(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            # Chaque LAYER a un NAME "xxx" ; exclure les noms de symboles/classes
            exclude = {'default', 'circle_point', 'hatch-line', 'hatch-fill'}
            seen = set()
            blocks = re.split(r'\bLAYER\b', content, flags=re.IGNORECASE)
            for block in blocks[1:]:
                m = re.search(r'NAME\s+"([^"]+)"', block)
                if m:
                    name = m.group(1).strip()
                    if name and name not in exclude and name not in seen:
                        seen.add(name)
                        layers_found.append({'name': name, 'title': name, 'bbox': None, 'styles': [{'name': 'default', 'title': 'Default Style'}]})
            return layers_found
        except Exception as e:
            log.debug(f"Lecture mapfile {path}: {e}")
            return []
    
    def _fetch_wms_layers_for_collection(collection):
        """Récupère les layers WMS d'un mapfile via GetCapabilities, puis fallback lecture fichier puis CKAN."""
        collection_id = collection.get('id')
        mapfile_path = f"/mapserver/mapfiles/{collection_id}.map"
        layers_found = []
        
        # 1) MapServer GetCapabilities
        try:
            params = {'map': mapfile_path, 'SERVICE': 'WMS', 'VERSION': '1.1.0', 'REQUEST': 'GetCapabilities'}
            resp = requests.get(f"{mapserver_url}/wms", params=params, timeout=10)
            if resp.status_code == 200 and ('text/xml' in resp.headers.get('Content-Type', '').lower() or resp.content.strip().startswith(b'<?xml')):
                root = ET.fromstring(resp.content)
                ns = {'wms': 'http://www.opengis.net/wms'}
                wms_ns = 'http://www.opengis.net/wms'
                layer_elems = root.findall('.//wms:Layer', ns)
                if not layer_elems:
                    layer_elems = root.findall(f'.//{{{wms_ns}}}Layer')
                for layer in layer_elems:
                    name_el = (layer.find('wms:Name', ns) or layer.find(f'{{{wms_ns}}}Name') or 
                               layer.find('Name') if layer.find('Name') is not None else None)
                    if name_el is None or not (name_el.text or '').strip():
                        continue
                    layer_name = (name_el.text or '').strip()
                    title_el = layer.find('wms:Title', ns) or layer.find(f'{{{wms_ns}}}Title') or layer.find('Title')
                    layer_title = (title_el.text or layer_name).strip() if title_el is not None else layer_name
                    bbox_elem = layer.find('.//wms:EX_GeographicBoundingBox', ns) or layer.find('.//EX_GeographicBoundingBox')
                    layer_bbox = None
                    if bbox_elem is not None:
                        west = bbox_elem.find('wms:westBoundLongitude', ns) or bbox_elem.find('westBoundLongitude')
                        east = bbox_elem.find('wms:eastBoundLongitude', ns) or bbox_elem.find('eastBoundLongitude')
                        south = bbox_elem.find('wms:southBoundLatitude', ns) or bbox_elem.find('southBoundLatitude')
                        north = bbox_elem.find('wms:northBoundLatitude', ns) or bbox_elem.find('northBoundLatitude')
                        if west is not None and east is not None and south is not None and north is not None:
                            try:
                                layer_bbox = (float(west.text), float(south.text), float(east.text), float(north.text))
                            except (ValueError, TypeError):
                                pass
                    styles = []
                    for style_elem in layer.findall('wms:Style', ns) or layer.findall('Style') or []:
                        sn = style_elem.find('wms:Name', ns) or style_elem.find('Name')
                        st = style_elem.find('wms:Title', ns) or style_elem.find('Title')
                        if sn is not None and sn.text:
                            styles.append({'name': sn.text, 'title': (st.text or sn.text) if st is not None else sn.text})
                    if not styles:
                        styles.append({'name': 'default', 'title': 'Default Style'})
                    layers_found.append({'name': layer_name, 'title': layer_title, 'bbox': layer_bbox, 'styles': styles})
                if layers_found:
                    return collection, layers_found
        except Exception as e:
            log.debug(f"MapServer WMS GetCapabilities pour {collection_id}: {e}")
        
        # 2) Fallback: lecture directe du mapfile
        layers_found = _extract_layers_from_mapfile_file(mapfile_path, collection_id)
        if layers_found:
            # Enrichir les titres depuis CKAN (ressources)
            try:
                pkg = toolkit.get_action('package_show')({'ignore_auth': True}, {'id': collection_id})
                for ms in layers_found:
                    ln = ms.get('name')
                    for res in (pkg.get('resources') or []):
                        if ln and ogc_helpers.layer_name_for_resource(res.get('id')) == ln:
                            ms['title'] = res.get('name', res.get('title', ln))
                            break
            except Exception:
                pass
            return collection, layers_found
        
        # 3) Fallback: utiliser les ressources CKAN (pour construire la hiérarchie Dataset → Ressources)
        try:
            pkg = toolkit.get_action('package_show')({'ignore_auth': True}, {'id': collection_id})
            geo_formats = {'geojson', 'json', 'shp', 'shapefile', 'kml', 'gml', 'gpkg', 'geopackage'}
            for res in (pkg.get('resources') or []):
                rid = res.get('id')
                fmt = (res.get('format') or '').lower()
                if res.get('datastore_active') or any(g in fmt for g in geo_formats):
                    ln = ogc_helpers.layer_name_for_resource(rid)
                    if ln:
                        layers_found.append({
                            'name': ln,
                            'title': res.get('name', res.get('title', ln)),
                            'bbox': None,
                            'styles': [{'name': 'default', 'title': 'Default Style'}]
                        })
        except Exception as e:
            log.debug(f"Fallback CKAN resources pour {collection_id}: {e}")
        
        return collection, layers_found
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    collection_layers = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_wms_layers_for_collection, c): c for c in collections}
        for future in as_completed(futures):
            coll, layers = future.result()
            collection_layers.append((coll, layers))
    
    for collection, mapserver_layers in collection_layers:
        collection_id = collection.get('id')
        collection_title = escape_xml(collection.get('title', collection_id))
        minx, miny, maxx, maxy = get_collection_bbox(collection)
        
        # Fusionner les styles SLD issus des extras dataset (pour que QGIS voie les styles personnalisés)
        try:
            context = {'ignore_auth': True}
            package = toolkit.get_action('package_show')(context, {'id': collection_id})
            package_id = package.get('id')
            for ms_layer in mapserver_layers:
                ln = ms_layer.get('name')
                if not ln:
                    continue
                for res in package.get('resources') or []:
                    rid = res.get('id')
                    if not rid or ogc_helpers.layer_name_for_resource(rid) != ln:
                        continue
                    sld = ogc_helpers.get_sld_style_for_resource(package_id, rid)
                    if sld:
                        style_name = ogc_helpers.get_sld_style_name(sld) or 'default'
                        if not any(s.get('name') == style_name for s in ms_layer.get('styles', [])):
                            ms_layer.setdefault('styles', []).insert(0, {'name': style_name, 'title': style_name})
                    break
        except Exception as e:
            log.debug(f"Styles SLD (extras) pour {collection_id}: {e}")
        
        # Si MapServer a retourné des layers individuels, les ajouter comme sous-layers
        if mapserver_layers and len(mapserver_layers) > 0:
            # Ajouter le layer parent (dataset) qui contient les sous-layers
            xml += f'''
      <Layer queryable="1">
        <Name>{collection_id}</Name>
        <Title>{collection_title}</Title>
        <Abstract>{escape_xml(collection.get('description', ''))}</Abstract>
        <CRS>EPSG:4326</CRS>
        <CRS>EPSG:3857</CRS>
        <CRS>EPSG:2154</CRS>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>{minx:.6f}</westBoundLongitude>
          <eastBoundLongitude>{maxx:.6f}</eastBoundLongitude>
          <southBoundLatitude>{miny:.6f}</southBoundLatitude>
          <northBoundLatitude>{maxy:.6f}</northBoundLatitude>
        </EX_GeographicBoundingBox>'''
            
            # Ajouter chaque layer individuel comme sous-layer
            for ms_layer in mapserver_layers:
                layer_name = escape_xml(ms_layer['name'])
                layer_title = escape_xml(ms_layer['title'])
                layer_bbox = ms_layer['bbox'] if ms_layer['bbox'] else (minx, miny, maxx, maxy)
                layer_styles = ms_layer.get('styles', [{'name': 'default', 'title': 'Default Style'}])
                
                xml += f'''
        <Layer queryable="1">
          <Name>{layer_name}</Name>
          <Title>{layer_title}</Title>
          <CRS>EPSG:4326</CRS>
          <CRS>EPSG:3857</CRS>
          <CRS>EPSG:2154</CRS>
          <EX_GeographicBoundingBox>
            <westBoundLongitude>{layer_bbox[0]:.6f}</westBoundLongitude>
            <eastBoundLongitude>{layer_bbox[2]:.6f}</eastBoundLongitude>
            <southBoundLatitude>{layer_bbox[1]:.6f}</southBoundLatitude>
            <northBoundLatitude>{layer_bbox[3]:.6f}</northBoundLatitude>
          </EX_GeographicBoundingBox>'''
                
                # Ajouter tous les styles disponibles pour ce layer (compatible avec SLD)
                for style in layer_styles:
                    style_name = escape_xml(style['name'])
                    style_title = escape_xml(style['title'])
                    xml += f'''
          <Style>
            <Name>{style_name}</Name>
            <Title>{style_title}</Title>
          </Style>'''
                
                xml += '''
        </Layer>'''
            
            xml += '''
      </Layer>'''
        else:
            # Pas de sous-layers trouvés, ajouter comme layer simple
            xml += f'''
      <Layer queryable="1">
        <Name>{collection_id}</Name>
        <Title>{collection_title}</Title>
        <Abstract>{escape_xml(collection.get('description', ''))}</Abstract>
        <CRS>EPSG:4326</CRS>
        <CRS>EPSG:3857</CRS>
        <CRS>EPSG:2154</CRS>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>{minx:.6f}</westBoundLongitude>
          <eastBoundLongitude>{maxx:.6f}</eastBoundLongitude>
          <southBoundLatitude>{miny:.6f}</southBoundLatitude>
          <northBoundLatitude>{maxy:.6f}</northBoundLatitude>
        </EX_GeographicBoundingBox>
        <Style>
          <Name>default</Name>
          <Title>Default Style</Title>
        </Style>
      </Layer>'''
    
    xml += '''
    </Layer>
  </Capability>
</WMS_Capabilities>'''
    
    # Headers pour améliorer la compatibilité avec QGIS
    headers = {
        'Content-Type': 'application/xml; charset=utf-8',
        'Content-Length': str(len(xml.encode('utf-8'))),
        'Cache-Control': 'public, max-age=3600',
        'X-Content-Type-Options': 'nosniff'
    }
    
    try:
        return Response(xml, mimetype='application/xml', headers=headers)
    except OSError as e:
        # Logging détaillé de l'erreur OSError lors de la création de la réponse WMS GetCapabilities
        log.error(f"OSError lors de la création de la réponse WMS GetCapabilities pour {org_id}")
        log.error(f"   Type d'erreur: {type(e).__name__}")
        log.error(f"   Message: {str(e)}")
        log.error(f"   Errno: {e.errno if hasattr(e, 'errno') else 'N/A'}")
        log.error(f"   Strerror: {e.strerror if hasattr(e, 'strerror') else 'N/A'}")
        log.error(f"   Filename: {e.filename if hasattr(e, 'filename') else 'N/A'}")
        log.error(f"   Taille du XML: {len(xml.encode('utf-8'))} bytes")
        log.error(f"   Nombre de collections: {len(collections)}")
        import traceback
        log.error(f"   Traceback: {traceback.format_exc()}")
        # Retourner une réponse d'erreur WMS valide
        error_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc">
  <ServiceException code="InternalError">
    Internal server error: {html.escape(str(e))}
  </ServiceException>
</ServiceExceptionReport>'''
        return Response(error_xml, mimetype='application/xml', status=500)


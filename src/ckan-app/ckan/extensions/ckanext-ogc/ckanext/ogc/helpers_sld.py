"""
SLD (Styled Layer Descriptor) helpers.

Reading, writing, parsing and compiling SLD styles:

- style name extracted from the SLD (used for GetLegendGraphic ``STYLE=``)
- resource_id to MapServer layer_name mapping
- read/write from resource extras (with dataset extras fallback)
- SLD -> MapServer CLASS compilation through ``ckanext.ogc.sld_to_mapfile``
- combination of multiple per-layer SLDs into a single NamedLayer document
"""
import os
import re
import logging
from typing import Any, Dict, List, Optional

from ckan.plugins import toolkit

from ckanext.ogc.helpers_mapfile import (
    get_layer_geometry_type,
    patch_mapfile_layer_classes,
)

log = logging.getLogger(__name__)


def get_sld_style_name(sld_xml: Optional[str]) -> str:
    """Extract the style name from an SLD document (for the WMS ``STYLE`` param).

    MapServer needs ``STYLE`` to pick the right style from the SLD for the
    legend. Looks for ``UserStyle/Name``, then ``UserStyle/Title``,
    otherwise returns ``'default'``.
    """
    try:
        if not sld_xml or not isinstance(sld_xml, str):
            return 'default'

        import xml.etree.ElementTree as ET
        root = ET.fromstring(sld_xml)

        # 1) UserStyle/Name (identifiant du style, prioritaire)
        for ns in ('http://www.opengis.net/sld', 'http://www.opengis.net/se'):
            style_name_elem = root.find(f'.//{{{ns}}}UserStyle/{{{ns}}}Name')
            if style_name_elem is not None and style_name_elem.text and style_name_elem.text.strip():
                name = style_name_elem.text.strip()
                log.debug(f"get_sld_style_name: nom trouvé (UserStyle/Name ns={ns[:30]}...): '{name}'")
                return name
        style_name_elem = root.find('.//UserStyle/Name')
        if style_name_elem is not None and style_name_elem.text and style_name_elem.text.strip():
            name = style_name_elem.text.strip()
            log.debug(f"get_sld_style_name: nom trouvé (UserStyle/Name sans ns): '{name}'")
            return name

        # 2) UserStyle/Title (souvent présent dans les SLD sans Name)
        for ns in ('http://www.opengis.net/sld', 'http://www.opengis.net/se'):
            title_elem = root.find(f'.//{{{ns}}}UserStyle/{{{ns}}}Title')
            if title_elem is not None and title_elem.text and title_elem.text.strip():
                name = title_elem.text.strip()
                log.debug(f"get_sld_style_name: nom trouvé (UserStyle/Title ns): '{name}'")
                return name
        title_elem = root.find('.//UserStyle/Title')
        if title_elem is not None and title_elem.text and title_elem.text.strip():
            name = title_elem.text.strip()
            log.debug(f"get_sld_style_name: nom trouvé (UserStyle/Title sans ns): '{name}'")
            return name

        # 3) NamedStyle/Name
        for ns in ('http://www.opengis.net/sld',):
            style_name_elem = root.find(f'.//{{{ns}}}NamedStyle/{{{ns}}}Name')
            if style_name_elem is not None and style_name_elem.text and style_name_elem.text.strip():
                name = style_name_elem.text.strip()
                log.debug(f"get_sld_style_name: nom trouvé (NamedStyle/Name): '{name}'")
                return name
        style_name_elem = root.find('.//NamedStyle/Name')
        if style_name_elem is not None and style_name_elem.text and style_name_elem.text.strip():
            name = style_name_elem.text.strip()
            log.debug(f"get_sld_style_name: nom trouvé (NamedStyle/Name): '{name}'")
            return name

        log.debug("get_sld_style_name: aucun Name/Title trouvé dans le SLD, retour 'default'")
        return 'default'
    except Exception as e:
        log.warning(f"Erreur lors de l'extraction du nom de style depuis le SLD: {e}")
        return 'default'


def sld_contains_graphic_fill(sld_xml: Optional[str]) -> bool:
    """Return True when the SLD contains a ``GraphicFill`` (hatches).

    When MapServer receives such an SLD through ``SLD_BODY`` it tries to
    open the symbol as a file (e.g. hatch-line) and fails. In that case
    callers should not send ``SLD_BODY`` and rely on the style compiled
    into the mapfile instead.
    """
    if not sld_xml or not isinstance(sld_xml, str):
        return False
    return 'GraphicFill' in sld_xml


def layer_name_for_resource(resource_id: Optional[str]) -> Optional[str]:
    """Return the layer name (datagis table) for a resource.

    Uses the same convention as the mapfile and the datagis import, so
    the SLD can be matched to the correct layer in the mapfile.
    """
    if not resource_id:
        return None
    clean = resource_id.replace('-', '_')
    clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in clean)
    name = f"res_{clean}"
    if len(name) > 63:
        name = f"res_{clean[:59]}"
    return name


def _get_sld_from_resource_extras(resource: Dict[str, Any]) -> Optional[str]:
    """Extract the ``sld_style`` value from a resource dict's extras (list or dict form)."""
    extras = resource.get('extras') or []
    if isinstance(extras, dict):
        val = extras.get('sld_style')
        if val is not None and str(val).strip():
            return str(val) if not isinstance(val, str) else val
        return None
    for extra in extras:
        if isinstance(extra, dict) and extra.get('key') == 'sld_style':
            val = extra.get('value')
            if val is not None and str(val).strip():
                return str(val) if not isinstance(val, str) else val
    return None


def _get_sld_from_package_extras(package_id: str, resource_id: str) -> Optional[str]:
    """Fallback: read the SLD from the dataset extras (legacy ``sld_style_<resource_id>`` key)."""
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        key = f"sld_style_{resource_id}"
        extras = package.get('extras', [])
        if isinstance(extras, dict):
            val = extras.get(key)
            if val is not None and str(val).strip():
                return str(val) if not isinstance(val, str) else val
            return None
        for extra in (extras or []):
            if isinstance(extra, dict) and extra.get('key') == key:
                val = extra.get('value')
                if val is not None and str(val).strip():
                    return str(val) if not isinstance(val, str) else val
    except Exception:
        pass
    return None


def get_sld_style_for_resource(package_id: str, resource_id: str) -> Optional[str]:
    """Return the SLD style for a resource.

    Looks up the resource extras first (key ``sld_style``), then falls
    back to the dataset extras (key ``sld_style_<resource_id>``).
    """
    try:
        context = {'ignore_auth': True}
        resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        val = _get_sld_from_resource_extras(resource)
        if val is not None:
            log.debug(f"get_sld_style_for_resource: sld_style trouvé dans extras ressource {resource_id}")
            return val
        val = _get_sld_from_package_extras(package_id, resource_id)
        if val is not None:
            log.debug(f"get_sld_style_for_resource: sld_style trouvé dans extras dataset (fallback) pour {resource_id}")
            return val
        log.debug(f"get_sld_style_for_resource: aucun SLD pour ressource {resource_id}")
        return None
    except Exception as e:
        log.debug(f"Erreur get_sld_style_for_resource {package_id}/{resource_id}: {e}")
        return None


def save_sld_style_for_resource(package_id: str, resource_id: str, sld_xml: Optional[str], context: Optional[Dict[str, Any]] = None) -> bool:
    """Persist an SLD style on the resource (extras, key ``sld_style``).

    If the backend does not support resource extras, falls back to the
    dataset extras. The SLD is also compiled into MapServer CLASS blocks
    and patched into the dataset mapfile for that resource.

    When ``sld_xml`` is empty or None, the SLD is removed via
    :func:`delete_sld_style_for_resource` and the mapfile is reset to the
    default class.
    """
    if context is None:
        context = {'ignore_auth': True}
    if sld_xml is None or (isinstance(sld_xml, str) and not (sld_xml or '').strip()):
        return delete_sld_style_for_resource(package_id, resource_id, context)
    try:
        resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        raw_extras = resource.get('extras') or []
        if isinstance(raw_extras, dict):
            extras_dict = dict(raw_extras)
        else:
            extras_dict = {e['key']: e['value'] for e in raw_extras if isinstance(e, dict) and 'key' in e and 'value' in e}
        extras_dict['sld_style'] = sld_xml
        updated_extras = [{'key': k, 'value': v} for k, v in extras_dict.items()]
        toolkit.get_action('resource_patch')(context, {'id': resource_id, 'extras': updated_extras})
        log.info(f"SLD sauvegardé dans les métadonnées de la ressource {resource_id}")
    except Exception as e:
        log.warning(f"save_sld_style_for_resource: extras ressource non supportés ({e}), fallback sur extras dataset")
        try:
            package = toolkit.get_action('package_show')(context, {'id': package_id})
            raw_extras = package.get('extras') or []
            if isinstance(raw_extras, dict):
                extras_dict = dict(raw_extras)
            else:
                extras_dict = {e['key']: e['value'] for e in raw_extras if isinstance(e, dict) and 'key' in e and 'value' in e}
            extras_dict[f'sld_style_{resource_id}'] = sld_xml
            updated_extras = [{'key': k, 'value': v} for k, v in extras_dict.items()]
            toolkit.get_action('package_patch')(context, {'id': package_id, 'extras': updated_extras})
            log.info(f"SLD sauvegardé dans les extras du dataset (fallback) pour ressource {resource_id}")
        except Exception as e2:
            log.error(f"Erreur save_sld_style_for_resource {package_id}/{resource_id}: {e2}")
            return False
    # Compiler le SLD dans le mapfile du dataset (évite le proxy SLD)
    try:
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        dataset_name = package.get('name')
        if not dataset_name:
            log.debug("save_sld_style_for_resource: pas de nom de dataset, skip compilation mapfile")
            return True
        layer_name = layer_name_for_resource(resource_id)
        if not layer_name:
            log.debug("save_sld_style_for_resource: pas de layer_name, skip compilation mapfile")
            return True
        mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
        mapfile_path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
        if not os.path.exists(mapfile_path):
            log.debug(f"save_sld_style_for_resource: mapfile absent {mapfile_path}, skip compilation")
            return True
        geom_type = get_layer_geometry_type(mapfile_path, layer_name)
        class_content = sld_to_mapserver_class(sld_xml, geometry_type=geom_type)
        if patch_mapfile_layer_classes(mapfile_path, layer_name, class_content):
            log.info(f"SLD compilé dans le mapfile pour LAYER \"{layer_name}\"")
    except Exception as e3:
        log.warning(f"save_sld_style_for_resource: compilation mapfile ignorée ({e3})")
    return True


def has_sld_style_for_resource(package_id: str, resource_id: str) -> bool:
    """Return True when an SLD style is defined for the resource."""
    return get_sld_style_for_resource(package_id, resource_id) is not None


def keep_only_userstyle(sld_xml: str, wanted: str) -> str:
    """Keep only the ``<UserStyle>`` whose ``<Name>`` equals ``wanted``.

    Lets us send MapServer a single-style SLD with an empty ``STYLES=``
    parameter, which avoids the ``Style (xxx) not defined on layer``
    error.
    """
    if not sld_xml or not wanted:
        return sld_xml
    # Blocs UserStyle (avec ou sans préfixe de namespace)
    blocks = re.findall(r'(<\w*:?UserStyle\b[^>]*>.*?</\w*:?UserStyle>)', sld_xml, flags=re.DOTALL)
    if not blocks:
        return sld_xml
    for b in blocks:
        m = re.search(r'<\w*:?Name[^>]*>\s*([^<]+)\s*</\w*:?Name>', b)
        if m and m.group(1).strip() == wanted:
            # Remplacer toute la séquence de UserStyle par ce bloc unique
            sld_xml2 = re.sub(r'(<\w*:?UserStyle\b[^>]*>.*?</\w*:?UserStyle>\s*)+', b, sld_xml, flags=re.DOTALL)
            return sld_xml2
    return sld_xml


def _sld_to_mapfile_module() -> Optional[Any]:
    """Lazy import of ``sld_to_mapfile`` (avoids a top-level import failure)."""
    try:
        from ckanext.ogc import sld_to_mapfile as m
        return m
    except ImportError:
        return None


def sld_to_mapserver_class(sld_xml: Optional[str], geometry_type: Optional[str] = None) -> str:
    """Compile an SLD into MapServer CLASS/STYLE blocks.

    Maps ``UserStyle`` to ``CLASS NAME`` and ``Rule`` to
    ``EXPRESSION + STYLE``. Delegates to ``ckanext.ogc.sld_to_mapfile``.
    ``geometry_type`` is one of ``'POINT'``, ``'LINE'`` or ``'POLYGON'``
    and drives the default style if the SLD is empty.
    """
    mod = _sld_to_mapfile_module()
    if mod:
        return mod.sld_to_mapserver_class(sld_xml, geometry_type=geometry_type)
    # Fallback minimal si module absent
    if not sld_xml or not isinstance(sld_xml, str) or not sld_xml.strip():
        return '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END'''
    log.warning("sld_to_mapserver_class: module sld_to_mapfile absent, style par défaut")
    return '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END'''


def delete_sld_style_for_resource(package_id: str, resource_id: str, context: Optional[Dict[str, Any]] = None) -> bool:
    """Remove the SLD everywhere it might live.

    Clears the resource extras (``sld_style``), the dataset extras
    (``sld_style_<resource_id>``) and resets the mapfile CLASS to the
    geometry-appropriate default. Returns True on success (including
    no-op), False on error.
    """
    if context is None:
        context = {'ignore_auth': True}
    mod = _sld_to_mapfile_module()
    default_class = mod.DEFAULT_MAPSERVER_CLASS_POLYGON if mod else '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END'''
    # 1) Réinitialiser le mapfile (CLASS par défaut pour la couche)
    try:
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        dataset_name = package.get('name')
        layer_name = layer_name_for_resource(resource_id)
        if dataset_name and layer_name:
            mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
            mapfile_path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
            if mod and os.path.exists(mapfile_path):
                geom_type = get_layer_geometry_type(mapfile_path, layer_name)
                if geom_type == 'POINT':
                    default_class = mod.DEFAULT_MAPSERVER_CLASS_POINT
                elif geom_type == 'LINE':
                    default_class = mod.DEFAULT_MAPSERVER_CLASS_LINE
                else:
                    default_class = mod.DEFAULT_MAPSERVER_CLASS_POLYGON
                patch_mapfile_layer_classes(mapfile_path, layer_name, default_class)
                log.debug(f"delete_sld_style_for_resource: mapfile réinitialisé pour layer {layer_name}")
    except Exception as e:
        log.warning(f"delete_sld_style_for_resource: mapfile reset ignoré ({e})")

    # 2) Nettoyer les extras ressource (clé sld_style)
    try:
        resource = toolkit.get_action('resource_show')(context, {'id': resource_id})
        raw_extras = resource.get('extras') or []
        if isinstance(raw_extras, dict):
            extras_dict = dict(raw_extras)
        else:
            extras_dict = {e['key']: e['value'] for e in raw_extras if isinstance(e, dict) and 'key' in e and 'value' in e}
        if 'sld_style' in extras_dict:
            del extras_dict['sld_style']
            updated_extras = [{'key': k, 'value': v} for k, v in extras_dict.items()]
            toolkit.get_action('resource_patch')(context, {'id': resource_id, 'extras': updated_extras})
            log.info(f"delete_sld_style_for_resource: SLD supprimé des extras ressource {resource_id}")
    except Exception as e:
        log.debug(f"delete_sld_style_for_resource: extras ressource non modifiables ({e})")

    # 3) Nettoyer les extras dataset (clé sld_style_<resource_id>) — toujours faire ce nettoyage
    try:
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        raw_extras = package.get('extras') or []
        if isinstance(raw_extras, dict):
            extras_dict = dict(raw_extras)
        else:
            extras_dict = {e['key']: e['value'] for e in raw_extras if isinstance(e, dict) and 'key' in e and 'value' in e}
        key = f'sld_style_{resource_id}'
        if key in extras_dict:
            del extras_dict[key]
            updated_extras = [{'key': k, 'value': v} for k, v in extras_dict.items()]
            toolkit.get_action('package_patch')(context, {'id': package_id, 'extras': updated_extras})
            log.info(f"delete_sld_style_for_resource: SLD supprimé des extras dataset (clé {key})")
    except Exception as e2:
        log.warning(f"delete_sld_style_for_resource: extras dataset ({e2})")
        return False
    return True


def _extract_user_style_from_sld(sld_xml: str) -> Optional[str]:
    """Extract the ``UserStyle`` content (without ``NamedLayer``) from an SLD.

    Ensures the fragment contains a ``<Name>`` under ``UserStyle`` so that
    ``GetLegendGraphic`` with ``STYLE=`` keeps working.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(sld_xml)
    for elem in root.iter():
        if elem.tag.endswith('}UserStyle') or elem.tag == 'UserStyle':
            fragment = ET.tostring(elem, encoding='unicode', default_namespace='')
            # MapServer exige un nom de style pour GetLegendGraphic ; injecter <Name>default</Name> si absent
            if not re.search(r'<(?:\w+:)?Name[^>]*>[^<]*</(?:\w+:)?Name>', fragment):
                fragment = re.sub(r'(<(?:\w+:)?UserStyle[^>]*>)', r'\1<Name>default</Name>', fragment, count=1)
                log.info("_extract_user_style_from_sld: <Name>default</Name> injecté (aucun Name dans UserStyle)")
            else:
                log.debug("_extract_user_style_from_sld: UserStyle contient déjà un Name, pas d'injection")
            return fragment
    return None


def build_combined_sld_for_layers(package_id: str, layer_names: List[str], dataset_name: str) -> Optional[str]:
    """Build a combined SLD for the given layers from per-resource SLDs.

    Each layer (``layer_name`` = datagis table name) is matched to a
    resource and its SLD is used if defined. Returns the combined SLD XML
    or None when no SLD needs to be applied.
    """
    if not layer_names:
        return None
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        resources = package.get('resources', []) or []
        layer_to_resource = {}
        for res in resources:
            rid = res.get('id')
            if not rid:
                continue
            ln = layer_name_for_resource(rid)
            if ln:
                layer_to_resource[ln] = rid
        parts = []
        for layer_name in layer_names:
            resource_id = layer_to_resource.get(layer_name)
            sld_xml = get_sld_style_for_resource(package_id, resource_id) if resource_id else None
            if not sld_xml:
                log.debug(f"build_combined_sld: aucun SLD ressource pour layer {layer_name} (resource_id={resource_id})")
                continue
            user_style = _extract_user_style_from_sld(sld_xml)
            if not user_style:
                log.warning(f"build_combined_sld: UserStyle non extrait du SLD pour layer {layer_name}")
                continue
            log.info(f"build_combined_sld: SLD ressource utilisé pour layer {layer_name}")
            parts.append(
                f'  <NamedLayer><Name>{layer_name}</Name>{user_style}</NamedLayer>'
            )
        if not parts:
            return None
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld" '
            'xmlns:ogc="http://www.opengis.net/ogc" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.0.0/StyledLayerDescriptor.xsd">\n'
            + '\n'.join(parts) + '\n</StyledLayerDescriptor>'
        )
    except Exception as e:
        log.warning(f"build_combined_sld_for_layers: {e}")
        return None

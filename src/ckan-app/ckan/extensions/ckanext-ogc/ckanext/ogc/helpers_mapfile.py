"""
MapServer mapfile manipulation.

Reads, modifies and writes ``.map`` files to:

- inject the SYMBOL definitions (hatch-line, circle_point) required by
  CLASS blocks compiled from SLD
- patch the CLASS blocks of a given LAYER
- read the geometry TYPE of a LAYER
- remove a LAYER entirely
- test the existence of a mapfile for a dataset
"""
import os
import re
import logging
from typing import Optional

log = logging.getLogger(__name__)


# Bloc SYMBOL pour hatch (GraphicFill) et point, à injecter dans le mapfile si absent
_MAPFILE_HATCH_SYMBOLS_BLOCK = '''    # Symbole pour les points (SLD PointSymbolizer)
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    # Symbole pour les hachures (SLD PolygonSymbolizer avec GraphicFill / horline, etc.)
    SYMBOL
        NAME "hatch-line"
        TYPE HATCH
    END
'''


def ensure_mapfile_has_hatch_symbols(mapfile_path: str) -> bool:
    """Inject the hatch-line / circle_point SYMBOL definitions if missing.

    When CLASS blocks compiled from SLD reference SYMBOL "hatch-line" or
    "circle_point" without those being defined at MAP level, MapServer
    tries to open a file and fails. This function adds the SYMBOL block
    just before the first LAYER if they are absent.

    Returns True if the file was modified or already contained the
    symbols, False otherwise.
    """
    if not mapfile_path or not os.path.exists(mapfile_path):
        return False
    try:
        with open(mapfile_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        log.warning(f"ensure_mapfile_has_hatch_symbols: lecture {mapfile_path} ({e})")
        return False
    if 'NAME "hatch-line"' in content or 'NAME "circle_point"' in content:
        log.debug(f"ensure_mapfile_has_hatch_symbols: symboles déjà présents dans {mapfile_path}")
        return True
    first_layer = re.search(r'\bLAYER\b', content)
    if not first_layer:
        log.debug(f"ensure_mapfile_has_hatch_symbols: aucun LAYER dans {mapfile_path}")
        return False
    insert_pos = first_layer.start()
    new_content = content[:insert_pos] + _MAPFILE_HATCH_SYMBOLS_BLOCK + content[insert_pos:]
    try:
        with open(mapfile_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        log.warning(f"ensure_mapfile_has_hatch_symbols: écriture {mapfile_path} ({e})")
        return False
    log.debug(f"ensure_mapfile_has_hatch_symbols: bloc SYMBOL hatch-line/circle_point ajouté dans {mapfile_path}")
    return True


def patch_mapfile_layer_classes(mapfile_path: str, layer_name: str, new_class_content: str) -> bool:
    """Replace the CLASS blocks of a given LAYER in the mapfile.

    Args:
        mapfile_path: Path to the ``.map`` file.
        layer_name: ``NAME`` of the LAYER (e.g. ``res_xxx``).
        new_class_content: String containing the CLASS ... END blocks with
            consistent indentation.

    Returns:
        True if the replacement was applied, False otherwise (file missing,
        layer not found, write error).

    If the CLASS references ``hatch-line`` or ``circle_point``, the
    required SYMBOL definitions are injected first.
    """
    if not mapfile_path or not layer_name or new_class_content is None:
        return False
    if not os.path.exists(mapfile_path):
        log.debug(f"patch_mapfile_layer_classes: mapfile absent {mapfile_path}")
        return False
    if 'hatch-line' in new_class_content or 'circle_point' in new_class_content:
        ensure_mapfile_has_hatch_symbols(mapfile_path)
    try:
        with open(mapfile_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        log.warning(f"patch_mapfile_layer_classes: lecture {mapfile_path} ({e})")
        return False
    # Trouver le LAYER dont le NAME est "layer_name" (un LAYER peut avoir NAME sur la ligne suivante)
    escaped = re.escape(layer_name)
    name_pattern = re.compile(r'NAME\s+"' + escaped + r'"')
    for layer_m in re.finditer(r'\bLAYER\b', content):
        start_pos = layer_m.start()
        # Bloc de ce LAYER : jusqu'au prochain LAYER ou fin de fichier
        next_layer = re.search(r'\bLAYER\b', content[start_pos + 5:])
        end_block = start_pos + 5 + next_layer.start() if next_layer else len(content)
        block = content[start_pos:end_block]
        if name_pattern.search(block):
            break
    else:
        log.debug(f"patch_mapfile_layer_classes: LAYER NAME \"{layer_name}\" non trouvé dans {mapfile_path}")
        return False
    # Trouver le premier CLASS après ce LAYER
    after_layer = content[start_pos:]
    class_match = re.search(r'\n\s*CLASS\s', after_layer)
    if not class_match:
        log.debug(f"patch_mapfile_layer_classes: aucun CLASS trouvé pour LAYER \"{layer_name}\"")
        return False
    class_start_in_slice = class_match.start()
    class_start_global = start_pos + class_start_in_slice
    rest = after_layer[class_start_in_slice:]
    # Fin du bloc CLASS: juste avant METADATA ou prochain LAYER (même niveau d'indentation)
    next_meta = re.search(r'\n\s*METADATA\s', rest)
    next_layer = re.search(r'\n\s*LAYER\s', rest)
    end_class_block = len(rest)
    if next_meta:
        end_class_block = min(end_class_block, next_meta.start())
    if next_layer:
        end_class_block = min(end_class_block, next_layer.start())
    new_content = (
        content[:class_start_global] +
        '\n' + new_class_content.strip() + '\n' +
        content[class_start_global + end_class_block:]
    )
    try:
        with open(mapfile_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        log.warning(f"patch_mapfile_layer_classes: écriture {mapfile_path} ({e})")
        return False
    log.info(f"patch_mapfile_layer_classes: CLASS du LAYER \"{layer_name}\" mis à jour dans {mapfile_path}")
    return True


def get_layer_geometry_type(mapfile_path: str, layer_name: str) -> Optional[str]:
    """Read the LAYER ``TYPE`` (POINT, LINE, POLYGON) from the mapfile.

    Returns None if the file is missing or the layer is not found.
    """
    if not mapfile_path or not layer_name or not os.path.exists(mapfile_path):
        return None
    try:
        with open(mapfile_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return None
    escaped = re.escape(layer_name)
    name_pattern = re.compile(r'NAME\s+"' + escaped + r'"')
    for layer_m in re.finditer(r'\bLAYER\b', content):
        start = layer_m.start()
        next_layer = re.search(r'\bLAYER\b', content[start + 5:])
        end = start + 5 + next_layer.start() if next_layer else len(content)
        block = content[start:end]
        if not name_pattern.search(block):
            continue
        type_m = re.search(r'\bTYPE\s+(\w+)', block)
        if type_m:
            return type_m.group(1).upper()
        return None
    return None


def remove_layer_from_mapfile(mapfile_path: str, layer_name: str) -> bool:
    """Remove the LAYER block (``NAME="layer_name"``) entirely from the mapfile.

    Used when a resource is deleted to drop its layer from the dataset
    mapfile. Returns True if the layer was removed or absent, False on
    write error.
    """
    log.debug(f"remove_layer_from_mapfile: mapfile_path={mapfile_path!r}, layer_name={layer_name!r}")
    if not mapfile_path or not layer_name:
        log.debug(f"remove_layer_from_mapfile: paramètre vide, skip")
        return True
    if not os.path.exists(mapfile_path):
        log.debug(f"remove_layer_from_mapfile: mapfile absent {mapfile_path}, rien à retirer")
        return True
    try:
        with open(mapfile_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        log.warning(f"remove_layer_from_mapfile: lecture échouée {mapfile_path}: {e}")
        return False
    escaped = re.escape(layer_name)
    name_pattern = re.compile(r'NAME\s+"' + escaped + r'"')
    layer_start = None
    for layer_m in re.finditer(r'\bLAYER\b', content):
        start_pos = layer_m.start()
        next_layer = re.search(r'\bLAYER\b', content[start_pos + 5:])
        end_block = start_pos + 5 + next_layer.start() if next_layer else len(content)
        block = content[start_pos:end_block]
        if name_pattern.search(block):
            layer_start = start_pos
            break
    if layer_start is None:
        log.debug(f"remove_layer_from_mapfile: LAYER NAME \"{layer_name}\" non trouvé dans {mapfile_path}")
        return True
    # Trouver le END qui ferme ce LAYER (comptage des blocs MapServer: LAYER, CLASS, STYLE, METADATA, PROJECTION +1, END -1)
    rest = content[layer_start:]
    depth = 1
    block_start = re.compile(r'\n\s*(LAYER|CLASS|STYLE|METADATA|PROJECTION)\b')
    block_end = re.compile(r'\n\s*END\b')
    pos = 0
    end_match = None
    while True:
        next_start = block_start.search(rest, pos)
        next_end = block_end.search(rest, pos)
        if next_end is None and next_start is None:
            break
        if next_end is not None and (next_start is None or next_end.start() < next_start.start()):
            depth -= 1
            if depth == 0:
                end_match = next_end
                break
            pos = next_end.end()
        else:
            depth += 1
            pos = next_start.end()
    if end_match is None:
        log.warning(f"remove_layer_from_mapfile: END non trouvé pour LAYER \"{layer_name}\" dans {mapfile_path}")
        return False
    end_of_line = end_match.end()
    if end_of_line < len(rest) and rest[end_of_line:end_of_line + 1] == '\n':
        end_of_line += 1
    layer_end = layer_start + end_of_line
    new_content = content[:layer_start] + content[layer_end:]
    new_content = re.sub(r'\n{3,}', '\n\n', new_content)
    try:
        with open(mapfile_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        log.warning(f"remove_layer_from_mapfile: écriture échouée {mapfile_path}: {e}")
        return False
    log.info(f"remove_layer_from_mapfile: LAYER \"{layer_name}\" supprimé de {mapfile_path}")
    return True


def has_mapfile(dataset_name: str) -> bool:
    """Check whether a mapfile exists for a given dataset.

    Args:
        dataset_name: CKAN dataset name.

    Returns:
        True if the mapfile exists, False otherwise.
    """
    if not dataset_name:
        return False
    try:
        mapfile_path = f'/mapserver/mapfiles/{dataset_name}.map'
        return os.path.exists(mapfile_path)
    except Exception:
        return False

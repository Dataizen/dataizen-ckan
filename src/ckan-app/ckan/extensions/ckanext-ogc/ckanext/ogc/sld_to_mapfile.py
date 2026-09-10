"""
SLD to MapServer mapfile compilation (CLASS, EXPRESSION, STYLE).

Standalone module with no CKAN dependency, used by helpers and the
``generate-mapfile`` script.

- Parse SLD: ``UserStyle`` -> ``CLASS NAME "<style_name>"``
- ``Rule`` -> ``EXPRESSION`` (translation of OGC Filter) + ``STYLE`` (Symbolizer)
- Fill (color, opacity), Stroke (color, width, opacity)
- Filters: ``PropertyIsEqualTo``, ``IsNull``, ``And``, ``Or``, ``Not``,
  ``PropertyIsLike``; ``Categorize`` when relevant.
"""
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

# Classes MapServer par défaut quand pas de SLD ou SLD vidé (CRUD)
DEFAULT_MAPSERVER_CLASS_POLYGON = '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END'''

DEFAULT_MAPSERVER_CLASS_LINE = '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                WIDTH 2
            END
        END'''

DEFAULT_MAPSERVER_CLASS_POINT = '''        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
                SYMBOL "circle_point"
                SIZE 6
            END
        END'''


def _hex_to_rgb(color_str: Optional[str]) -> Tuple[int, int, int]:
    """Convertit #RRGGBB ou #RGB ou rgb(r,g,b) en (r, g, b) 0-255."""
    if not color_str:
        return (128, 128, 128)
    s = (color_str or '').strip()
    if s.startswith('#'):
        s = s[1:]
    if len(s) == 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    if len(s) == 3:
        return (int(s[0] + s[0], 16), int(s[1] + s[1], 16), int(s[2] + s[2], 16))
    m = re.search(r'rgb\s*\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*\)', s, re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (128, 128, 128)


def _get_css_param(parent: Optional[ET.Element], name: str) -> Optional[str]:
    """Retourne la valeur du CssParameter @name=name sous parent."""
    return _get_param(parent, name)


def _get_param(parent: Optional[ET.Element], name: str) -> Optional[str]:
    """Retourne la valeur du CssParameter ou SvgParameter @name=name sous parent (SLD 1.0 et 1.1/se)."""
    if parent is None:
        return None
    for elem in parent.iter():
        tag_local = (elem.tag or '').split('}')[-1]
        if tag_local in ('CssParameter', 'SvgParameter') and elem.get('name') == name:
            if elem.text:
                return elem.text.strip()
            return None
    return None


def _get_text(elem: Optional[ET.Element]) -> str:
    """Texte direct d'un élément (PropertyName, Literal)."""
    if elem is None:
        return ''
    return (elem.text or '').strip()


def _find_one(parent: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
    """Premier enfant dont le tag se termine par local_name."""
    if parent is None:
        return None
    for c in parent:
        if c.tag.endswith(local_name) or (c.tag or '').split('}')[-1] == local_name:
            return c
    return None


def _find_all(parent: Optional[ET.Element], local_name: str) -> List[ET.Element]:
    """Tous les enfants dont le tag se termine par local_name."""
    if parent is None:
        return []
    return [c for c in parent if c.tag.endswith(local_name) or (c.tag or '').split('}')[-1] == local_name]


def sld_filter_to_expression(filter_elem: Optional[ET.Element]) -> Optional[str]:
    """
    Traduit un élément OGC Filter en chaîne EXPRESSION MapServer.
    Supporte : PropertyIsEqualTo, PropertyIsNotEqualTo, PropertyIsNull, PropertyIsLike,
    And, Or, Not, PropertyIsLessThan, PropertyIsGreaterThan, etc.
    Retourne None si pas de filtre ou filtre vide.
    """
    if filter_elem is None:
        return None
    # Filter contient souvent un seul enfant (PropertyIsEqualTo, And, etc.)
    for child in filter_elem:
        expr = _filter_operator_to_expression(child)
        if expr:
            return expr
    return None


def _filter_operator_to_expression(op: ET.Element) -> Optional[str]:
    """Traduit un opérateur OGC (PropertyIsEqualTo, And, ...) en EXPRESSION MapServer."""
    if op is None:
        return None
    tag = (op.tag or '').split('}')[-1]

    if tag == 'PropertyIsEqualTo':
        prop = _get_text(_find_one(op, 'PropertyName'))
        lit = _find_one(op, 'Literal')
        val = _get_text(lit) if lit is not None else ''
        if not prop:
            return None
        # Numérique si literal ressemble à un nombre
        try:
            n = float(val)
            return '([%s] = %s)' % (prop.replace(']', '\\]'), n)
        except (ValueError, TypeError):
            return '("[%s]" = "%s")' % (prop.replace(']', '\\]').replace('"', '\\"'), val.replace('\\', '\\\\').replace('"', '\\"'))

    if tag == 'PropertyIsNotEqualTo':
        prop = _get_text(_find_one(op, 'PropertyName'))
        lit = _find_one(op, 'Literal')
        val = _get_text(lit) if lit is not None else ''
        if not prop:
            return None
        try:
            n = float(val)
            return '([%s] != %s)' % (prop.replace(']', '\\]'), n)
        except (ValueError, TypeError):
            return '("[%s]" != "%s")' % (prop.replace(']', '\\]').replace('"', '\\"'), val.replace('\\', '\\\\').replace('"', '\\"'))

    if tag == 'PropertyIsNull':
        prop = _get_text(_find_one(op, 'PropertyName'))
        if not prop:
            return None
        return '("[%s]" = "")' % (prop.replace(']', '\\]').replace('"', '\\"'))

    if tag == 'PropertyIsLike':
        prop = _get_text(_find_one(op, 'PropertyName'))
        lit = _find_one(op, 'Literal')
        val = _get_text(lit) if lit is not None else ''
        if not prop:
            return None
        # SLD wildCard * et . → MapServer regex * et .
        pattern = val.replace('*', '.*').replace('.', '\\.')
        return '("[%s]" ~ "%s")' % (prop.replace(']', '\\]').replace('"', '\\"'), pattern.replace('\\', '\\\\').replace('"', '\\"'))

    if tag == 'And':
        parts = []
        for c in op:
            e = _filter_operator_to_expression(c)
            if e:
                parts.append(e)
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return '( (' + ') and ('.join(parts) + ') )'

    if tag == 'Or':
        parts = []
        for c in op:
            e = _filter_operator_to_expression(c)
            if e:
                parts.append(e)
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return '( (' + ') or ('.join(parts) + ') )'

    if tag == 'Not':
        for c in op:
            e = _filter_operator_to_expression(c)
            if e:
                return '( not (%s) )' % e
        return None

    if tag in ('PropertyIsLessThan', 'PropertyIsLessThanOrEqualTo', 'PropertyIsGreaterThan', 'PropertyIsGreaterThanOrEqualTo'):
        prop = _get_text(_find_one(op, 'PropertyName'))
        lit = _find_one(op, 'Literal')
        val = _get_text(lit) if lit is not None else ''
        if not prop:
            return None
        try:
            n = float(val)
        except (ValueError, TypeError):
            return None
        opmap = {
            'PropertyIsLessThan': 'lt',
            'PropertyIsLessThanOrEqualTo': 'le',
            'PropertyIsGreaterThan': 'gt',
            'PropertyIsGreaterThanOrEqualTo': 'ge',
        }
        ms_op = opmap.get(tag, '=')
        return '([%s] %s %s)' % (prop.replace(']', '\\]'), ms_op, n)

    return None


def _get_style_name_from_sld(root: ET.Element) -> str:
    """Extrait le nom du style (UserStyle/Name ou Title)."""
    for us in root.iter():
        if not (us.tag.endswith('UserStyle') or 'UserStyle' in (us.tag or '')):
            continue
        for name_el in us:
            if (name_el.tag.endswith('Name') or (name_el.tag or '').endswith('Name')) and name_el.text:
                return (name_el.text or '').strip()
        for title_el in us:
            if (title_el.tag.endswith('Title') or (title_el.tag or '').endswith('Title')) and title_el.text:
                return (title_el.text or '').strip()
    return 'default'


def _rule_filter(rule: ET.Element) -> Optional[ET.Element]:
    """Retourne l'élément Filter d'une Rule."""
    return _find_one(rule, 'Filter')


def _build_style_lines(
    symbolizer_kind: str,
    fill_color: Optional[str],
    fill_opacity: Optional[str],
    stroke_color: Optional[str],
    stroke_width: Optional[str],
    stroke_opacity: Optional[str],
    point_size: Optional[str],
    outline_color: Optional[str],
) -> str:
    """Construit les lignes STYLE MapServer (COLOR, OPACITY, OUTLINECOLOR, WIDTH, SYMBOL, SIZE).
    PolygonOutline = contour uniquement (OUTLINECOLOR + WIDTH, pas de COLOR) pour éviter un remplissage noir.
    """
    lines = []
    if symbolizer_kind == 'PolygonOutline':
        if outline_color is not None:
            lines.append('                OUTLINECOLOR %d %d %d' % (outline_color[0], outline_color[1], outline_color[2]))
        if stroke_width is not None:
            w = max(0, int(float(stroke_width)))
            lines.append('                WIDTH %d' % w)
        if not lines:
            lines = ['                OUTLINECOLOR 0 0 0', '                WIDTH 1']
        return '\n'.join(lines)
    if fill_color is not None:
        lines.append('                COLOR %d %d %d' % (fill_color[0], fill_color[1], fill_color[2]))
    if fill_opacity is not None and 0 <= fill_opacity < 1:
        # Éviter OPACITY 0 en WMS (image vide) : minimum 20 % pour les polygones
        opacity_pct = int(fill_opacity * 100)
        if symbolizer_kind == 'Polygon' and opacity_pct == 0:
            opacity_pct = 20
        lines.append('                OPACITY %d' % opacity_pct)
    if outline_color is not None and symbolizer_kind in ('Polygon', 'Point'):
        lines.append('                OUTLINECOLOR %d %d %d' % (outline_color[0], outline_color[1], outline_color[2]))
    if stroke_color is not None and symbolizer_kind == 'Line':
        lines.append('                COLOR %d %d %d' % (stroke_color[0], stroke_color[1], stroke_color[2]))
    if stroke_width is not None and symbolizer_kind in ('Line', 'Polygon'):
        w = max(0, int(float(stroke_width))) if symbolizer_kind == 'Polygon' else max(1, int(float(stroke_width)))
        lines.append('                WIDTH %d' % w)
    if stroke_opacity is not None and 0 <= stroke_opacity < 1:
        lines.append('                OPACITY %d' % int(stroke_opacity * 100))
    if point_size is not None and symbolizer_kind == 'Point':
        lines.append('                SYMBOL "circle_point"')
        lines.append('                SIZE %d' % max(1, int(float(point_size))))
    if not lines:
        lines = ['                COLOR 255 0 0', '                WIDTH 1']
    return '\n'.join(lines)


def _parse_graphic_fill_hatch(fill_el: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
    """
    Parse Fill/GraphicFill/Graphic (SLD) pour un remplissage hachuré.
    Retourne None ou un dict {angle, size, width, color_rgb} pour un STYLE hatch MapServer.
    """
    if fill_el is None:
        return None
    graphic_fill = _find_one(fill_el, 'GraphicFill')
    if graphic_fill is None:
        return None
    graphic = _find_one(graphic_fill, 'Graphic')
    if graphic is None:
        return None
    # Mark / WellKnownName (horline, slash, etc.)
    mark = _find_one(graphic, 'Mark')
    if mark is None:
        return None
    wkn = _find_one(mark, 'WellKnownName')
    name = (wkn.text or '').strip().lower() if wkn is not None else ''
    if not name or name not in ('horline', 'horizontal', 'vertline', 'slash', 'backslash', 'cross', 'x'):
        name = 'horline'
    # Stroke (couleur et largeur des lignes de hachure)
    stroke_el = _find_one(mark, 'Stroke')
    color_rgb = (31, 120, 180)  # #1f78b4
    width = 1
    if stroke_el:
        v = _get_param(stroke_el, 'stroke')
        if v:
            color_rgb = _hex_to_rgb(v)
        v = _get_param(stroke_el, 'stroke-width')
        if v:
            try:
                width = max(1, int(float(v)))
            except (ValueError, TypeError):
                pass
    # Size (espacement des hachures)
    size_el = _find_one(graphic, 'Size')
    size = 7
    if size_el and size_el.text:
        try:
            size = max(1, int(float(size_el.text.strip())))
        except (ValueError, TypeError):
            pass
    # Rotation (angle en degrés)
    rotation_el = _find_one(graphic, 'Rotation')
    angle = 135
    if rotation_el is not None:
        lit = _find_one(rotation_el, 'Literal')
        if lit is not None and lit.text:
            try:
                angle = float(lit.text.strip()) % 360
            except (ValueError, TypeError):
                pass
    return {'angle': angle, 'size': size, 'width': width, 'color_rgb': color_rgb}


def _polygon_symbolizer_to_styles(symb: ET.Element) -> List[str]:
    """
    Pour un PolygonSymbolizer, peut produire plusieurs STYLE (ex: GraphicFill hatch + Fill/Stroke).
    Retourne une liste de chaînes (chaque chaîne = contenu d'un STYLE ... END).
    """
    styles = []
    fill_el = _find_one(symb, 'Fill')
    stroke_el = _find_one(symb, 'Stroke')
    # 1) GraphicFill (hachures) → un STYLE avec SYMBOL hatch
    hatch = _parse_graphic_fill_hatch(fill_el)
    if hatch is not None:
        r, g, b = hatch['color_rgb']
        lines = [
            '                SYMBOL "hatch-line"',
            '                ANGLE %s' % hatch['angle'],
            '                SIZE %d' % hatch['size'],
            '                WIDTH %d' % hatch['width'],
            '                COLOR %d %d %d' % (r, g, b),
        ]
        styles.append('\n'.join(lines))
    # 2) Fill / Stroke classique (remplissage et/ou contour)
    fill_color = (255, 0, 0)
    fill_opacity = None
    outline_color = (0, 0, 0)
    stroke_width = 1
    stroke_opacity = None
    if fill_el and hatch is None:
        v = _get_param(fill_el, 'fill')
        if v:
            fill_color = _hex_to_rgb(v)
        v = _get_param(fill_el, 'fill-opacity')
        if v:
            try:
                fill_opacity = float(v)
            except (ValueError, TypeError):
                pass
    if stroke_el:
        v = _get_param(stroke_el, 'stroke')
        if v:
            outline_color = _hex_to_rgb(v)
        v = _get_param(stroke_el, 'stroke-width')
        if v:
            try:
                stroke_width = max(0, int(float(v)))
            except (ValueError, TypeError):
                pass
        v = _get_param(stroke_el, 'stroke-opacity')
        if v:
            try:
                stroke_opacity = float(v)
            except (ValueError, TypeError):
                pass
    # Si GraphicFill seul sans Fill explicite, on n'ajoute pas de deuxième STYLE fill
    # Remplissage ou contour classique (toujours ajouter si pas seulement hatch, ou si stroke explicite)
    need_fill_stroke = (fill_el and _get_param(fill_el, 'fill')) or (stroke_el and (_get_param(stroke_el, 'stroke') or stroke_width > 0))
    if need_fill_stroke or not styles:
        style_lines = _build_style_lines('Polygon', fill_color, fill_opacity, None, stroke_width, stroke_opacity, None, outline_color)
        styles.append(style_lines)
    return styles


def _symbolizer_to_style(symb: ET.Element, kind: str, geometry_type: Optional[str] = None) -> Any:
    """Kind: Polygon, Line, Point. geometry_type: POLYGON|LINE|POINT pour adapter LineSymbolizer (contour seul si POLYGON).
    Retourne les lignes STYLE (une seule string) ou liste de strings pour Polygon.
    """
    fill_color = (255, 0, 0)
    fill_opacity = None
    stroke_color = (0, 0, 0)
    stroke_width = 1
    stroke_opacity = None
    point_size = 6
    outline_color = (0, 0, 0)

    if kind == 'Polygon':
        return _polygon_symbolizer_to_styles(symb)

    if kind == 'Line':
        stroke_el = _find_one(symb, 'Stroke')
        if stroke_el:
            v = _get_param(stroke_el, 'stroke')
            if v:
                stroke_color = _hex_to_rgb(v)
            v = _get_param(stroke_el, 'stroke-width')
            if v:
                try:
                    stroke_width = max(1, int(float(v)))
                except (ValueError, TypeError):
                    pass
            v = _get_param(stroke_el, 'stroke-opacity')
            if v:
                try:
                    stroke_opacity = float(v)
                except (ValueError, TypeError):
                    pass
        # Sur une couche POLYGON, un LineSymbolizer = contour du polygone uniquement (pas de remplissage COLOR).
        # Sinon MapServer dessine un remplissage noir qui masque les hachures.
        if geometry_type == 'POLYGON':
            return _build_style_lines('PolygonOutline', None, None, None, stroke_width, None, None, stroke_color)
        return _build_style_lines('Line', None, None, stroke_color, stroke_width, stroke_opacity, None, None)

    if kind == 'Point':
        graphic = _find_one(symb, 'Graphic')
        if graphic:
            size_el = _find_one(graphic, 'Size')
            if size_el and size_el.text:
                try:
                    point_size = max(1, int(float(size_el.text.strip())))
                except (ValueError, TypeError):
                    pass
            for mark in graphic.iter():
                if not (mark.tag.endswith('Mark') or 'Mark' in (mark.tag or '')):
                    continue
                fill_el = _find_one(mark, 'Fill')
                if fill_el:
                    v = _get_param(fill_el, 'fill')
                    if v:
                        fill_color = _hex_to_rgb(v)
                stroke_el = _find_one(mark, 'Stroke')
                if stroke_el:
                    v = _get_param(stroke_el, 'stroke')
                    if v:
                        outline_color = _hex_to_rgb(v)
                    v = _get_param(stroke_el, 'stroke-width')
                    if v:
                        try:
                            stroke_width = max(1, int(float(v)))
                        except (ValueError, TypeError):
                            pass
                break
        return _build_style_lines('Point', fill_color, fill_opacity, None, stroke_width, None, point_size, outline_color)

    return '                COLOR 255 0 0\n                WIDTH 1'


def sld_to_mapserver_class(sld_xml: Optional[str], geometry_type: Optional[str] = None) -> str:
    """
    Compile un SLD en blocs MapServer CLASS/STYLE.
    - Pour chaque UserStyle : CLASS NAME "<style_name>"
    - Pour chaque Rule : EXPRESSION (traduction Filter) + STYLE (Symbolizer)
    - Fill (color, opacity), Stroke (color, width, opacity)
    - geometry_type: 'POLYGON'|'LINE'|'POINT' pour défaut si aucune Rule.
    """
    if not sld_xml or not isinstance(sld_xml, str) or not sld_xml.strip():
        return _default_class_for_geometry(geometry_type)
    try:
        root = ET.fromstring(sld_xml)
    except ET.ParseError:
        return _default_class_for_geometry(geometry_type)

    style_name_global = _get_style_name_from_sld(root)
    classes = []

    for us in root.iter():
        if (us.tag or '').split('}')[-1] != 'UserStyle':
            continue
        for rule in us.iter():
            tag_local = (rule.tag or '').split('}')[-1]
            if tag_local != 'Rule':
                continue
            rule_name_el = _find_one(rule, 'Name')
            rule_name = _get_text(rule_name_el) if rule_name_el is not None else style_name_global
            if not rule_name:
                rule_name = 'default'

            expression = None
            filter_el = _rule_filter(rule)
            if filter_el is not None:
                expression = sld_filter_to_expression(filter_el)

            # Collecter tous les blocs STYLE (plusieurs symbolizers par règle, Polygon peut en avoir plusieurs)
            # Pour une couche POINT, n'utiliser que PointSymbolizer : PolygonSymbolizer (ex. GraphicFill/hatch)
            # produit SYMBOL TYPE HATCH que MapServer ne supporte pas pour les points (erreur "unsupported symbol type 1005").
            all_style_blocks = []
            for sym in rule.iter():
                tag_local = (sym.tag or '').split('}')[-1]
                if geometry_type == 'POINT':
                    if tag_local == 'PointSymbolizer':
                        all_style_blocks.append(_symbolizer_to_style(sym, 'Point'))
                    # ignorer PolygonSymbolizer et LineSymbolizer pour POINT (hatch invalide)
                else:
                    if tag_local == 'PolygonSymbolizer':
                        res = _symbolizer_to_style(sym, 'Polygon')
                        if isinstance(res, list):
                            all_style_blocks.extend(res)
                        else:
                            all_style_blocks.append(res)
                    elif tag_local == 'LineSymbolizer':
                        all_style_blocks.append(_symbolizer_to_style(sym, 'Line', geometry_type=geometry_type))
                    elif tag_local == 'PointSymbolizer':
                        all_style_blocks.append(_symbolizer_to_style(sym, 'Point'))
            if not all_style_blocks:
                if geometry_type == 'POINT':
                    all_style_blocks = ['                COLOR 255 0 0\n                OUTLINECOLOR 0 0 0\n                WIDTH 1\n                SYMBOL "circle_point"\n                SIZE 6']
                else:
                    all_style_blocks = [_build_style_lines('Polygon', (255, 0, 0), None, (0, 0, 0), 1, None, None, (0, 0, 0))]

            name_esc = rule_name.replace('\\', '\\\\').replace('"', '\\"')
            block = '        CLASS\n            NAME "%s"' % name_esc
            if expression:
                expr_esc = expression.replace('\\', '\\\\').replace('"', '\\"')
                block += '\n            EXPRESSION "' + expr_esc + '"'
            for style_lines in all_style_blocks:
                block += '\n            STYLE\n' + style_lines + '\n            END'
            block += '\n        END'
            classes.append(block)

    if not classes:
        return _default_class_for_geometry(geometry_type)
    return '\n'.join(classes)


def _default_class_for_geometry(geometry_type: Optional[str]) -> str:
    """Retourne la CLASS MapServer par défaut selon le type de géométrie."""
    if geometry_type == 'POINT':
        return DEFAULT_MAPSERVER_CLASS_POINT
    if geometry_type == 'LINE':
        return DEFAULT_MAPSERVER_CLASS_LINE
    return DEFAULT_MAPSERVER_CLASS_POLYGON

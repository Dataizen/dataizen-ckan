"""Tests for :mod:`ckanext.ogc.sld_to_mapfile` (standalone, no CKAN dep)."""
from ckanext.ogc.sld_to_mapfile import (
    _hex_to_rgb,
    sld_to_mapserver_class,
    _default_class_for_geometry,
    DEFAULT_MAPSERVER_CLASS_POLYGON,
    DEFAULT_MAPSERVER_CLASS_POINT,
    DEFAULT_MAPSERVER_CLASS_LINE,
)


# ---------------------------------------------------------------------------
# _hex_to_rgb
# ---------------------------------------------------------------------------

def test_hex_to_rgb_six_digit_hex():
    assert _hex_to_rgb('#ff0000') == (255, 0, 0)
    assert _hex_to_rgb('#00ff00') == (0, 255, 0)
    assert _hex_to_rgb('#0000ff') == (0, 0, 255)


def test_hex_to_rgb_three_digit_hex():
    assert _hex_to_rgb('#f00') == (255, 0, 0)
    assert _hex_to_rgb('#0f0') == (0, 255, 0)


def test_hex_to_rgb_without_hash():
    assert _hex_to_rgb('ff0000') == (255, 0, 0)


def test_hex_to_rgb_rgb_function():
    assert _hex_to_rgb('rgb(10, 20, 30)') == (10, 20, 30)
    assert _hex_to_rgb('rgb(10 20 30)') == (10, 20, 30)


def test_hex_to_rgb_invalid_returns_grey():
    assert _hex_to_rgb(None) == (128, 128, 128)
    assert _hex_to_rgb('') == (128, 128, 128)
    assert _hex_to_rgb('not-a-color') == (128, 128, 128)


# ---------------------------------------------------------------------------
# _default_class_for_geometry
# ---------------------------------------------------------------------------

def test_default_class_polygon():
    assert _default_class_for_geometry('POLYGON') == DEFAULT_MAPSERVER_CLASS_POLYGON
    assert _default_class_for_geometry('polygon') == DEFAULT_MAPSERVER_CLASS_POLYGON


def test_default_class_point():
    assert _default_class_for_geometry('POINT') == DEFAULT_MAPSERVER_CLASS_POINT


def test_default_class_line():
    assert _default_class_for_geometry('LINE') == DEFAULT_MAPSERVER_CLASS_LINE


def test_default_class_unknown_falls_back_to_polygon():
    assert _default_class_for_geometry(None) == DEFAULT_MAPSERVER_CLASS_POLYGON
    assert _default_class_for_geometry('') == DEFAULT_MAPSERVER_CLASS_POLYGON
    assert _default_class_for_geometry('unknown') == DEFAULT_MAPSERVER_CLASS_POLYGON


# ---------------------------------------------------------------------------
# sld_to_mapserver_class
# ---------------------------------------------------------------------------

def test_empty_sld_returns_default_class():
    out = sld_to_mapserver_class(None)
    assert 'CLASS' in out
    assert 'NAME "default"' in out


def test_empty_string_sld_returns_default_class():
    out = sld_to_mapserver_class('')
    assert 'CLASS' in out


def test_sld_with_simple_polygon_fill_compiles_to_class():
    sld = '''<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld"
    xmlns:ogc="http://www.opengis.net/ogc">
  <NamedLayer><Name>test</Name>
    <UserStyle><Name>simple</Name>
      <FeatureTypeStyle><Rule>
        <PolygonSymbolizer>
          <Fill><CssParameter name="fill">#ff8800</CssParameter></Fill>
        </PolygonSymbolizer>
      </Rule></FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>'''
    out = sld_to_mapserver_class(sld, geometry_type='POLYGON')
    assert 'CLASS' in out
    assert 'STYLE' in out
    # Fill #ff8800 -> rgb(255, 136, 0)
    assert '255 136 0' in out


def test_sld_with_invalid_xml_returns_default():
    out = sld_to_mapserver_class('<not-valid-xml>')
    assert 'CLASS' in out
    assert 'NAME "default"' in out

"""Tests for pure helpers in :mod:`ckanext.ogc.helpers_sld`.

Only functions that do not call ``ckan.plugins.toolkit`` are exercised here;
the others would need a running CKAN instance.
"""
from ckanext.ogc.helpers_sld import (
    get_sld_style_name,
    sld_contains_graphic_fill,
    layer_name_for_resource,
    keep_only_userstyle,
)


# ---------------------------------------------------------------------------
# layer_name_for_resource
# ---------------------------------------------------------------------------

def test_layer_name_for_resource_basic_uuid():
    out = layer_name_for_resource('abc-123-def')
    assert out == 'res_abc_123_def'


def test_layer_name_for_resource_replaces_non_alnum_with_underscore():
    out = layer_name_for_resource('abc!@#xyz')
    # Non-alphanumeric characters become underscores
    assert out == 'res_abc___xyz'


def test_layer_name_for_resource_returns_none_when_empty():
    assert layer_name_for_resource(None) is None
    assert layer_name_for_resource('') is None


def test_layer_name_for_resource_truncates_long_input():
    long_id = 'x' * 100
    out = layer_name_for_resource(long_id)
    assert out is not None
    # MapServer / PostGIS limit: 63 chars (truncated to res_ + 59)
    assert len(out) <= 63


# ---------------------------------------------------------------------------
# sld_contains_graphic_fill
# ---------------------------------------------------------------------------

def test_sld_contains_graphic_fill_true():
    sld = '<SLD><GraphicFill><Name>hatch</Name></GraphicFill></SLD>'
    assert sld_contains_graphic_fill(sld) is True


def test_sld_contains_graphic_fill_false():
    assert sld_contains_graphic_fill('<SLD><Fill>red</Fill></SLD>') is False
    assert sld_contains_graphic_fill('') is False
    assert sld_contains_graphic_fill(None) is False
    assert sld_contains_graphic_fill(123) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_sld_style_name
# ---------------------------------------------------------------------------

def test_get_sld_style_name_from_userstyle_name():
    sld = '''<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld">
  <NamedLayer>
    <UserStyle>
      <Name>my-style</Name>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>'''
    assert get_sld_style_name(sld) == 'my-style'


def test_get_sld_style_name_from_userstyle_title_when_no_name():
    sld = '''<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld">
  <NamedLayer>
    <UserStyle>
      <Title>My nice title</Title>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>'''
    assert get_sld_style_name(sld) == 'My nice title'


def test_get_sld_style_name_defaults_when_missing():
    assert get_sld_style_name(None) == 'default'
    assert get_sld_style_name('') == 'default'
    assert get_sld_style_name('not-xml') == 'default'
    assert get_sld_style_name('<empty/>') == 'default'


def test_get_sld_style_name_handles_bad_input_type():
    assert get_sld_style_name(42) == 'default'  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# keep_only_userstyle
# ---------------------------------------------------------------------------

def test_keep_only_userstyle_returns_input_when_inputs_empty():
    assert keep_only_userstyle('', 'foo') == ''
    assert keep_only_userstyle('<x/>', '') == '<x/>'


def test_keep_only_userstyle_returns_input_when_no_userstyle_block():
    sld = '<x><Y/></x>'
    assert keep_only_userstyle(sld, 'foo') == sld


def test_keep_only_userstyle_keeps_wanted_block():
    sld = (
        '<SLD>'
        '<UserStyle><Name>a</Name><Body>aa</Body></UserStyle>'
        '<UserStyle><Name>b</Name><Body>bb</Body></UserStyle>'
        '</SLD>'
    )
    out = keep_only_userstyle(sld, 'b')
    # Only the "b" UserStyle should remain
    assert '<Name>b</Name>' in out
    assert '<Name>a</Name>' not in out

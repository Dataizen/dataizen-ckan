"""Tests for :mod:`ckanext.ogc.helpers_mapfile` (.map file manipulation)."""
import os

from ckanext.ogc.helpers_mapfile import (
    ensure_mapfile_has_hatch_symbols,
    patch_mapfile_layer_classes,
    get_layer_geometry_type,
    remove_layer_from_mapfile,
    has_mapfile,
)


MINIMAL_MAPFILE = '''MAP
    NAME "test"
    EXTENT -180 -90 180 90
    UNITS DD
    LAYER
        NAME "res_abc"
        TYPE POLYGON
        STATUS ON
        DATA "shapes/test.shp"
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END
    END
END
'''


# ---------------------------------------------------------------------------
# has_mapfile
# ---------------------------------------------------------------------------

def test_has_mapfile_returns_false_on_empty_input():
    assert has_mapfile('') is False
    assert has_mapfile(None) is False


def test_has_mapfile_returns_false_when_file_missing():
    # Le chemin par défaut /mapserver/mapfiles n'existe pas localement
    assert has_mapfile('nonexistent-dataset-xyz') is False


# ---------------------------------------------------------------------------
# get_layer_geometry_type
# ---------------------------------------------------------------------------

def test_get_layer_geometry_type_reads_polygon(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    assert get_layer_geometry_type(str(map_file), 'res_abc') == 'POLYGON'


def test_get_layer_geometry_type_returns_none_for_missing_layer(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    assert get_layer_geometry_type(str(map_file), 'res_unknown') is None


def test_get_layer_geometry_type_returns_none_for_missing_file():
    assert get_layer_geometry_type('/does/not/exist.map', 'res_abc') is None
    assert get_layer_geometry_type('', 'res_abc') is None


# ---------------------------------------------------------------------------
# ensure_mapfile_has_hatch_symbols
# ---------------------------------------------------------------------------

def test_ensure_hatch_symbols_adds_block_when_missing(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    result = ensure_mapfile_has_hatch_symbols(str(map_file))
    assert result is True
    content = map_file.read_text()
    assert 'NAME "hatch-line"' in content
    assert 'NAME "circle_point"' in content


def test_ensure_hatch_symbols_idempotent(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    ensure_mapfile_has_hatch_symbols(str(map_file))
    first = map_file.read_text()
    # Second call should not duplicate the block
    ensure_mapfile_has_hatch_symbols(str(map_file))
    second = map_file.read_text()
    assert first == second
    assert second.count('NAME "hatch-line"') == 1


def test_ensure_hatch_symbols_missing_file():
    assert ensure_mapfile_has_hatch_symbols('/does/not/exist.map') is False


# ---------------------------------------------------------------------------
# patch_mapfile_layer_classes
# ---------------------------------------------------------------------------

def test_patch_layer_classes_replaces_class(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    new_class = '''        CLASS
            NAME "custom"
            STYLE
                COLOR 0 200 0
                WIDTH 2
            END
        END'''
    result = patch_mapfile_layer_classes(str(map_file), 'res_abc', new_class)
    assert result is True
    content = map_file.read_text()
    assert 'NAME "custom"' in content
    # Le COLOR par défaut "255 0 0" du minimal mapfile a été remplacé
    assert '0 200 0' in content


def test_patch_layer_classes_returns_false_for_missing_layer(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    result = patch_mapfile_layer_classes(str(map_file), 'res_missing', 'CLASS END')
    assert result is False


def test_patch_layer_classes_returns_false_for_missing_file():
    assert patch_mapfile_layer_classes('/no.map', 'res_abc', 'CLASS END') is False
    assert patch_mapfile_layer_classes('', 'res_abc', 'CLASS END') is False
    assert patch_mapfile_layer_classes('/no.map', '', 'CLASS END') is False


# ---------------------------------------------------------------------------
# remove_layer_from_mapfile
# ---------------------------------------------------------------------------

def test_remove_layer_drops_block(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    result = remove_layer_from_mapfile(str(map_file), 'res_abc')
    assert result is True
    content = map_file.read_text()
    assert 'NAME "res_abc"' not in content


def test_remove_layer_returns_true_when_layer_absent(tmp_path):
    map_file = tmp_path / 'test.map'
    map_file.write_text(MINIMAL_MAPFILE)
    # Layer that does not exist: rien à retirer, retour True
    assert remove_layer_from_mapfile(str(map_file), 'res_unknown') is True


def test_remove_layer_returns_true_when_file_missing():
    # Absent file: rien à retirer, retour True
    assert remove_layer_from_mapfile('/no.map', 'res_abc') is True
    assert remove_layer_from_mapfile('', 'res_abc') is True

"""Tests for pure helpers in :mod:`ckanext.ogc.helpers`."""
from ckanext.ogc.helpers import format_resource_size


# ---------------------------------------------------------------------------
# format_resource_size
# ---------------------------------------------------------------------------

def test_format_resource_size_handles_none():
    assert format_resource_size(None) == ''


def test_format_resource_size_handles_invalid_input():
    assert format_resource_size('not-a-number') == ''  # type: ignore[arg-type]
    assert format_resource_size(-1) == ''


def test_format_resource_size_bytes():
    assert format_resource_size(0) == '0 o'
    assert format_resource_size(512) == '512 o'
    assert format_resource_size(1023) == '1023 o'


def test_format_resource_size_kilobytes():
    assert format_resource_size(1024) == '1.0 Ko'
    assert format_resource_size(2 * 1024) == '2.0 Ko'


def test_format_resource_size_megabytes():
    assert format_resource_size(1024 * 1024) == '1.0 Mo'
    assert format_resource_size(int(2.5 * 1024 * 1024)) == '2.5 Mo'


def test_format_resource_size_gigabytes():
    assert format_resource_size(1024 * 1024 * 1024) == '1.0 Go'

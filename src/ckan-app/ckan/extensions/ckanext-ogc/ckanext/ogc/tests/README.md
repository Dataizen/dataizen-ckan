# ckanext-ogc tests

Unit tests for the pure-logic helpers of `ckanext-ogc` :

- `test_sld_to_mapfile.py` : SLD → MapServer CLASS compilation (standalone module).
- `test_helpers_mapfile.py` : mapfile manipulation (LAYER, CLASS, SYMBOL hatch).
- `test_helpers_sld.py` : SLD parsing helpers (style name, GraphicFill detection, UserStyle filter).
- `test_helpers.py` : misc helpers (`format_resource_size`).

`conftest.py` installs lightweight `unittest.mock` stubs for `ckan.plugins`,
`ckan.common` and `ckan.model` so the helpers can be imported in
isolation, without a running CKAN instance. When CKAN is already importable
(e.g. when running from inside the CKAN Docker image) the stubs are skipped.

## Run

From the extension root (`ckan-app/ckan/extensions/ckanext-ogc/`):

```bash
pip install -r dev-requirements.txt
pytest
```

From the host without installing pytest, the project's helper script can
execute the test discovery loop (see `dev-requirements.txt` for the
recommended way).

## Scope

Only the modules thematically split out during the cleanup are covered:
`helpers.py`, `helpers_sld.py`, `helpers_mapfile.py`, `helpers_home.py`
and `sld_to_mapfile.py`. The thick CKAN-coupled modules (`plugin.py`,
`views.py`, the WMS/WFS view modules) are left out of the unit suite,
they should be exercised through CKAN integration tests inside the
container.

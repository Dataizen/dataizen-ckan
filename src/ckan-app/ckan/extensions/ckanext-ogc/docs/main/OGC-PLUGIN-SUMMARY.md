# OGC Plugin Integration - Summary

## What was done

### 1. Created OGC Plugin (`ckanext-ogc`)

The plugin (`ckanext-ogc/ckanext/ogc/plugin.py`) now contains:

- **Complete OGC logic**: All synchronization, CRS detection, and dataset management
- **Automatic startup**: Starts pygeoapi on port 5001 when CKAN starts
- **Background synchronization**: Automatically syncs geospatial datasets
- **No CKAN routes**: pygeoapi runs independently on port 5001

### 2. Integrated All OGC Scripts

All OGC functionality is now in the plugin:

- **`ckan_sync.py`**: Core synchronization logic
- **`sync_tool.py`**: Command-line synchronization tool
- **`sync-ogc.sh`**: Shell script for manual sync
- **`start-pygeoapi.sh`**: pygeoapi startup script
- **`test-ogc.sh`**: OGC testing script

### 3. Updated Dockerfile

The Dockerfile now:

- Installs the OGC plugin: `pip install -e /srv/app/ckanext-ogc`
- Configures OGC settings in `ckan.ini`
- Copies OGC startup scripts
- Uses `start-ckan-with-ogc.sh` as the default command

### 4. Created Startup Scripts

- **`start-ckan-with-ogc.sh`**: Main startup script that launches CKAN and pygeoapi
- **`start-pygeoapi.sh`**: pygeoapi startup script
- **`sync-ogc.sh`**: Manual synchronization script
- **`test-ogc-integration.sh`**: Integration testing script

### 5. Configuration

The plugin is configured in `ckan.ini`:

```ini
ckanext.ogc.pygeoapi_url = http://localhost:5001
ckanext.ogc.pygeoapi_config_path = /srv/app/pygeoapi/local.config.yml
ckanext.ogc.ckan_api_key = ckan-local-dev-apikey
ckanext.ogc.auto_sync = true
ckanext.ogc.collections_base_path = /srv/app/pygeoapi
```

## How it works

1. **CKAN starts** with the OGC plugin enabled
2. **Plugin starts pygeoapi** on port 5001 in background
3. **Plugin waits** for both CKAN and pygeoapi to be ready
4. **Plugin runs synchronization** automatically in background thread
5. **OGC services** are available on port 5001

## Benefits

- **Single plugin**: All OGC logic in one place
- **Easy activation/deactivation**: Just enable/disable the plugin
- **Automatic startup**: No manual scripts needed
- **Background processing**: Non-blocking synchronization
- **Independent pygeoapi**: Runs on port 5001, not in CKAN

## Testing

To test the integration:

```bash
# Start services
./start-local.sh

# Test OGC integration
./test-ogc-integration.sh

# Test OGC services
docker compose exec ckan /srv/app/test-ogc.sh
```

## Access

- **CKAN**: http://localhost:8080
- **OGC API**: http://localhost:5001
- **Collections**: http://localhost:5001/collections
- **API Info**: http://localhost:5001/api

## Files created/modified

### New files:
- `ckan-app/ckan/extensions/ckanext-ogc/ckanext/ogc/plugin.py`
- `ckan-app/ckan/extensions/ckanext-ogc/pygeoapi/local.config.yml`
- `ckan-app/ckan/extensions/ckanext-ogc/pygeoapi-providers/ckan_provider/`
- `ckan-app/ckan/extensions/ckanext-ogc/README.md`
- `ckan-app/ckan/start-pygeoapi.sh`
- `ckan-app/ckan/start-ckan-with-ogc.sh`
- `ckan-app/ckan/sync-ogc.sh`
- `ckan-app/ckan/test-ogc.sh`
- `test-ogc-integration.sh`
- `test-ogc-structure.sh`
- `OGC-INTEGRATION.md`

### Moved files:
- `pygeoapi/` → `ckan-app/ckan/extensions/ckanext-ogc/pygeoapi/`
- `pygeoapi-providers/` → `ckan-app/ckan/extensions/ckanext-ogc/pygeoapi-providers/`

### Modified files:
- `ckan-app/ckan/Dockerfile` (added OGC plugin installation and configuration)

## Next steps

1. **Build and test**: Run `./start-local.sh` to build and test
2. **Verify OGC services**: Check that pygeoapi is accessible on port 5001
3. **Test synchronization**: Verify that datasets are synced automatically
4. **Add test dataset**: Use `--add-test-dataset` flag to test with geospatial data

The OGC integration is now completely self-contained in the plugin and will start automatically when CKAN starts!

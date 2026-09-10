# OGC Scripts

This directory contains utility scripts for the OGC extension.

## Scripts

### `start-pygeoapi.sh`
Starts the pygeoapi server on port 5001.

**Usage:**
```bash
/srv/app/start-pygeoapi.sh
```

### `sync-ogc.sh` ⭐ **IMPORTANT**
Manually synchronizes CKAN datasets with pygeoapi OGC services.

**Usage:**
```bash
/srv/app/sync-ogc.sh
```

**What it does:**
- Waits for CKAN and pygeoapi to be ready
- Runs synchronization using the sync tool
- Creates OGC collections for geospatial datasets

### `fix-ogc-issues.sh`
Diagnoses and fixes common OGC issues.

**Usage:**
```bash
/srv/app/fix-ogc-issues.sh
```

**What it does:**
- Stops pygeoapi
- Synchronizes CKAN data
- Restores OGC links
- Verifies configuration
- Restarts pygeoapi
- Tests endpoints

## Integration

These scripts are automatically copied to `/srv/app/` during Docker build and are available for manual use or troubleshooting.

The OGC plugin handles automatic startup and synchronization, but these scripts provide manual control when needed.











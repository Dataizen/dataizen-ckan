# OGC Tests

This directory contains test scripts for the OGC extension.

## Test Scripts

### `test-ogc.sh`
Basic OGC services test.

**Usage:**
```bash
/srv/app/test-ogc.sh
```

**What it tests:**
- CKAN connectivity
- Dataset access
- pygeoapi accessibility
- Collection endpoints
- Items endpoints
- Individual item access
- WFS, WMS, WMTS endpoints

### `test-ogc-services.sh`
Comprehensive OGC services test (WFS, WMS, WMTS).

**Usage:**
```bash
/srv/app/test-ogc-services.sh
```

**What it tests:**
- WFS GetCapabilities and GetFeature
- WMS GetCapabilities and GetMap
- WMTS GetCapabilities and GetTile
- GeoJSON endpoints
- Service functionality validation

### `test-all-datasets.sh`
Tests all datasets in pygeoapi.

**Usage:**
```bash
/srv/app/test-all-datasets.sh
```

**What it tests:**
- All collections in pygeoapi
- Collection pages
- Items endpoints
- Individual items
- OGC service links

### `test-specific-issues.sh`
Tests specific issues identified in logs.

**Usage:**
```bash
/srv/app/test-specific-issues.sh
```

**What it tests:**
- Individual item access
- Collection page OGC links
- Specific error conditions
- CRS issues
- Configuration problems

## Integration

These test scripts are automatically copied to `/srv/app/` during Docker build and are available for testing OGC functionality.











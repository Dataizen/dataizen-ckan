# OGC Integration Documentation

## Overview

This document describes the OGC (Open Geospatial Consortium) integration for Dataizen CKAN. The integration provides OGC API services (WFS, WMS, WMTS) for geospatial datasets stored in CKAN.

## Architecture

The OGC integration consists of:

1. **CKAN OGC Plugin** (`ckanext-ogc`): A CKAN extension that manages OGC services
2. **pygeoapi**: A Python server that provides OGC API services
3. **CKAN Provider**: A pygeoapi provider that connects to CKAN datasets
4. **Synchronization**: Automatic synchronization between CKAN and pygeoapi

## Components

### 1. CKAN OGC Plugin

The plugin (`ckanext-ogc`) provides:

- **Automatic startup**: Starts pygeoapi when CKAN starts
- **Synchronization**: Automatically syncs geospatial datasets
- **Configuration**: Manages OGC service configuration
- **Monitoring**: Monitors service health

### 2. pygeoapi Server

pygeoapi runs on port 5001 and provides:

- **OGC API endpoints**: `/collections`, `/items`, etc.
- **WFS services**: Feature services for vector data
- **WMS services**: Map services for visualization
- **WMTS services**: Tile services for web mapping

### 3. CKAN Provider

The CKAN provider connects pygeoapi to CKAN datasets:

- **Data access**: Reads data from CKAN datastore
- **Format conversion**: Converts CKAN data to OGC formats
- **CRS detection**: Automatically detects coordinate reference systems
- **Spatial indexing**: Provides spatial queries and filtering

## Configuration

### CKAN Configuration

The plugin is configured in `ckan.ini`:

```ini
ckanext.ogc.pygeoapi_url = http://localhost:5001
ckanext.ogc.pygeoapi_config_path = /srv/app/pygeoapi/local.config.yml
ckanext.ogc.ckan_api_key = ckan-local-dev-apikey
ckanext.ogc.auto_sync = true
ckanext.ogc.collections_base_path = /srv/app/pygeoapi
```

### pygeoapi Configuration

pygeoapi is configured via `local.config.yml`:

```yaml
server:
  name: Dataizen OGC API
  description: OGC API for CKAN datasets
  version: 1.0.0
  limits:
    default: 10
    max: 10000

resources:
  # Collections are automatically added here by the sync process
```

## Usage

### Starting Services

The OGC integration starts automatically when CKAN starts:

```bash
# Start CKAN with OGC integration
docker compose up ckan

# Or use the startup script directly
/srv/app/start-ckan-with-ogc.sh
```

### Accessing OGC Services

Once started, OGC services are available at:

- **OGC API**: http://localhost:5001
- **Collections**: http://localhost:5001/collections
- **API Info**: http://localhost:5001/api
- **OpenAPI**: http://localhost:5001/openapi

### Testing

Test the integration:

```bash
# Test OGC services
/srv/app/test-ogc.sh

# Test integration
./test-ogc-integration.sh
```

## Synchronization

### Automatic Synchronization

The plugin automatically synchronizes geospatial datasets:

1. **Startup**: Syncs all datasets when CKAN starts
2. **Dataset changes**: Syncs when datasets are created/updated/deleted
3. **Background jobs**: Uses CKAN job queues for async processing

### Manual Synchronization

You can manually trigger synchronization:

```bash
# Sync all datasets
/srv/app/sync-ogc.sh

# Or use the sync tool directly
cd /srv/app/pygeoapi-providers/ckan_provider
python3 sync_tool.py --action sync-all
```

## Supported Formats

The integration supports:

- **GeoJSON**: Vector data in GeoJSON format
- **Shapefile**: ESRI Shapefile format
- **KML/KMZ**: Google Earth formats
- **CSV with geometry**: CSV files with geometry columns
- **WMS/WFS**: OGC web services

## CRS Support

The integration automatically detects and supports:

- **WGS84** (EPSG:4326): Default coordinate system
- **Lambert-93** (EPSG:2154): French national system
- **UTM**: Universal Transverse Mercator projections
- **Custom CRS**: User-defined coordinate systems

## Troubleshooting

### Common Issues

1. **pygeoapi not starting**: Check logs and ensure port 5001 is available
2. **Sync failures**: Verify CKAN API key and dataset permissions
3. **CRS issues**: Check dataset metadata for coordinate system information

### Logs

Check logs for debugging:

```bash
# CKAN logs
docker logs ckan

# pygeoapi logs
docker exec ckan tail -f /tmp/pygeoapi.log
```

### Health Checks

Verify services are running:

```bash
# Check CKAN
curl http://localhost:5000/api/action/status_show

# Check pygeoapi
curl http://localhost:5001/api/v1/collections
```

## Development

### Plugin Structure

```
ckanext-ogc/
├── ckanext/
│   └── ogc/
│       ├── __init__.py
│       ├── plugin.py          # Main plugin
│       ├── commands.py        # CLI commands
│       ├── utils.py           # Utility functions
│       └── ogc_manager.py     # OGC management
└── setup.py
```

### Adding New Features

To add new OGC features:

1. Update the plugin in `ckanext-ogc/`
2. Modify pygeoapi configuration
3. Update synchronization logic
4. Test with existing datasets

## Security

The integration includes:

- **API key authentication**: Secure access to CKAN API
- **CORS support**: Cross-origin resource sharing
- **Rate limiting**: Prevents abuse of OGC services
- **Input validation**: Validates all OGC requests

## Performance

Optimizations include:

- **Caching**: Caches frequently accessed data
- **Pagination**: Limits result sets for large datasets
- **Spatial indexing**: Efficient spatial queries
- **Background processing**: Async synchronization

## Monitoring

Monitor the integration:

- **Health checks**: Regular service availability checks
- **Performance metrics**: Response times and throughput
- **Error tracking**: Log and monitor sync failures
- **Usage statistics**: Track OGC service usage

## Future Enhancements

Planned improvements:

- **3D support**: Support for 3D geospatial data
- **Time series**: Temporal data support
- **Advanced filtering**: Complex spatial and temporal queries
- **Caching**: Redis-based caching for better performance
- **Load balancing**: Multiple pygeoapi instances
- **SSL/TLS**: HTTPS support for production
- **Authentication**: OAuth2 and JWT support
- **Rate limiting**: Advanced rate limiting strategies
- **Metrics**: Prometheus metrics integration
- **Alerting**: Automated alerting for service issues
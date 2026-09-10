# Dataizen OGC Integration

This document describes the OGC (Open Geospatial Consortium) integration for Dataizen CKAN, which provides standardized geospatial web services for CKAN datasets.

## Overview

The OGC integration consists of:

1. **CKAN OGC Extension** (`ckanext-ogc`) - Detects geospatial datasets and manages OGC endpoints
2. **pygeoapi** - Provides OGC API services (WFS, WMS, CSW)
3. **CLI Commands** - For manual synchronization and management

## Architecture

```
CKAN Datastore → OGC Extension → pygeoapi → OGC Endpoints
     ↓              ↓              ↓           ↓
  PostgreSQL → Detection → Configuration → WFS/WMS/CSW
```

## Features

- **Automatic Detection**: Identifies geospatial datasets in CKAN
- **Dynamic Endpoints**: Creates OGC collections automatically
- **Standards Compliance**: Implements OGC API standards
- **Background Processing**: Asynchronous synchronization via job queues
- **CLI Management**: Command-line tools for manual operations

## Installation

### 1. Build and Start Services

```bash
# Start CKAN with integrated OGC and pygeoapi
docker-compose -f docker-compose.integrated.yml up -d

# Or use the standard docker-compose (pygeoapi runs in CKAN container)
docker-compose up -d
```

### 2. Initialize OGC Extension

The OGC extension is automatically installed and configured during CKAN startup.

## Configuration

### CKAN Configuration

The following configuration options are added to `ckan.ini`:

```ini
# OGC Configuration
ckanext.ogc.pygeoapi_url = http://pygeoapi:5000
ckanext.ogc.auto_sync = false
ckanext.ogc.collections_base_path = /api/v1/collections
```

### pygeoapi Configuration

pygeoapi is configured via `pygeoapi/local.config.yml` and includes:

- Server settings (host, port, CORS)
- Metadata (title, description, contact info)
- API version and endpoints
- Resource templates for CKAN datasets

## Usage

### CLI Commands

#### Synchronize Datasets

```bash
# Sync all geospatial datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync

# Sync specific dataset
ckan -c /srv/app/ckan.ini pygeoapi-sync --dataset-id=<id>

# Dry run (show what would be done)
ckan -c /srv/app/ckan.ini pygeoapi-sync --dry-run

# Force recreation of existing endpoints
ckan -c /srv/app/ckan.ini pygeoapi-sync --force
```

#### Cleanup

```bash
# Clean up OGC endpoints for deleted datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync cleanup

# Clean up specific dataset
ckan -c /srv/app/ckan.ini pygeoapi-sync cleanup --dataset-id=<id>
```

#### Information

```bash
# Show OGC info for all datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync info

# Show OGC info for specific dataset
ckan -c /srv/app/ckan.ini pygeoapi-sync info --dataset-id=<id>
```

### Automatic Synchronization

When `ckanext.ogc.auto_sync = true`, the extension automatically:

- Detects new geospatial datasets
- Creates OGC collections
- Updates existing collections
- Cleans up deleted datasets

### Manual Synchronization

For manual control, use the CLI commands or disable auto-sync:

```ini
ckanext.ogc.auto_sync = false
```

## OGC Endpoints

### Collections

Each CKAN dataset becomes an OGC collection:

```
GET /api/v1/collections/dataset-{ckan-dataset-id}
```

### Features

Access dataset features via:

```
GET /api/v1/collections/dataset-{ckan-dataset-id}/items
GET /api/v1/collections/dataset-{ckan-dataset-id}/items/{feature-id}
```

### Spatial Queries

Filter features by spatial extent:

```
GET /api/v1/collections/dataset-{ckan-dataset-id}/items?bbox=west,south,east,north
```

## Data Detection

### Geospatial Dataset Detection

A dataset is considered geospatial if it has:

1. **Resources with known geospatial formats**:
   - GeoJSON
   - Shapefile
   - KML
   - GML

2. **CSV resources with geometry columns**:
   - PostGIS geometry columns (`the_geom`, `geometry`)
   - Coordinate columns (`lat`, `lon`, `latitude`, `longitude`)

### Column Detection

The extension automatically detects:

- Geometry columns and their SRID
- Coordinate columns
- Spatial extent (bounding box)

## Development

### Project Structure

```
ckanext-ogc/
├── ckanext/
│   └── ogc/
│       ├── __init__.py      # Template helpers
│       ├── plugin.py        # Main plugin
│       ├── commands.py      # CLI commands
│       └── utils.py         # Utility functions
└── setup.py                 # Package configuration
```

### Adding New Features

1. **Extend the plugin** in `plugin.py`
2. **Add CLI commands** in `commands.py`
3. **Implement utilities** in `utils.py`
4. **Update configuration** in `setup.py`

### Testing

```bash
# Test OGC extension
python -c "import ckanext.ogc"

# Test CLI commands
ckan -c /srv/app/ckan.ini pygeoapi-sync --help
```

## Troubleshooting

### Common Issues

1. **Extension not loaded**:
   - Check `ckan.ini` for `ogc` in plugins list
   - Verify extension installation

2. **pygeoapi not accessible**:
   - Check pygeoapi service status
   - Verify network connectivity
   - Check configuration URLs

3. **Datasets not detected**:
   - Verify datastore is active
   - Check for geometry columns
   - Review detection logic

### Logs

Check CKAN logs for OGC-related messages:

```bash
docker exec -it ckan tail -f /var/log/ckan/ckan.log | grep -i ogc
```

### Health Checks

Test pygeoapi health:

```bash
curl http://localhost:5001/health
```

## API Reference

### OGC API Endpoints

- **Landing Page**: `GET /`
- **API**: `GET /api`
- **Conformance**: `GET /conformance`
- **Collections**: `GET /collections`
- **Collection**: `GET /collections/{collectionId}`
- **Items**: `GET /collections/{collectionId}/items`
- **Item**: `GET /collections/{collectionId}/items/{itemId}`

### Response Formats

- **GeoJSON**: Default format for spatial data
- **JSON**: Metadata and non-spatial information
- **HTML**: Human-readable pages

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support and questions:

- **Issues**: GitHub Issues
- **Documentation**: This README and inline code comments
- **Community**: Dataizen team and contributors

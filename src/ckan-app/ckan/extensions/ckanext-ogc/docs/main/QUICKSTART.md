# OGC Integration Quick Start Guide

This guide will get you up and running with OGC integration in under 10 minutes.

## Prerequisites

- Docker and Docker Compose installed
- CKAN instance running
- Some geospatial datasets in CKAN

## Quick Setup

### 1. Start Integrated Services

```bash
# From your project root (recommended)
docker-compose -f docker-compose.integrated.yml up -d

# Or use standard docker-compose (pygeoapi runs in CKAN container)
docker-compose up -d
```

### 2. Verify pygeoapi is Running

```bash
# Check service status
docker ps | grep pygeoapi

# Test health endpoint
curl http://localhost:5001/health
```

### 3. Test OGC Commands

```bash
# Enter CKAN container
docker exec -it ckan bash

# Test OGC commands
ckan -c /srv/app/ckan.ini pygeoapi-sync --help

# Show OGC info for all datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync info
```

### 4. Sync Your First Dataset

```bash
# Dry run to see what would be synced
ckan -c /srv/app/ckan.ini pygeoapi-sync --dry-run

# Sync all geospatial datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync

# Or sync a specific dataset
ckan -c /srv/app/ckan.ini pygeoapi-sync --dataset-id=<your-dataset-id>
```

### 5. Access OGC Endpoints

```bash
# List all collections
curl http://localhost:5001/collections

# Access a specific collection
curl http://localhost:5001/collections/dataset-<your-dataset-id>

# Get features from a collection
curl http://localhost:5001/collections/dataset-<your-dataset-id>/items
```

## What You Get

- **OGC API endpoints** for each geospatial dataset
- **WFS-compatible** feature services
- **Spatial queries** with bounding box filters
- **Standards compliance** with OGC API specifications

## Next Steps

1. **Enable auto-sync** in CKAN configuration
2. **Customize pygeoapi** configuration
3. **Add authentication** if needed
4. **Integrate with GIS clients** (QGIS, ArcGIS, etc.)

## Troubleshooting

### Common Issues

**pygeoapi not accessible**:
```bash
# Check if service is running
docker-compose ps

# Check logs
docker exec -it ckan tail -f /var/log/ckan/ckan.log | grep -i pygeoapi
```

**OGC commands not found**:
```bash
# Verify extension is installed
docker exec -it ckan python -c "import ckanext.ogc"

# Check CKAN configuration
docker exec -it ckan grep -i ogc /srv/app/ckan.ini
```

**No geospatial datasets detected**:
```bash
# Check dataset info
ckan -c /srv/app/ckan.ini pygeoapi-sync info --dataset-id=<your-dataset-id>

# Verify datastore is active
ckan -c /srv/app/ckan.ini datastore_info --id=<resource-id>
```

## Examples

### Sync Specific Dataset

```bash
# Find your dataset ID
ckan -c /srv/app/ckan.ini pygeoapi-sync info

# Sync specific dataset
ckan -c /srv/app/ckan.ini pygeoapi-sync --dataset-id=dcf2cf11-333f-4f89-b0ad-e6df96b19884
```

### Force Recreation

```bash
# Force recreation of existing endpoints
ckan -c /srv/app/ckan.ini pygeoapi-sync --force
```

### Cleanup

```bash
# Clean up deleted datasets
ckan -c /srv/app/ckan.ini pygeoapi-sync cleanup --dry-run
```

## Integration Examples

### QGIS

1. Add WFS layer
2. URL: `http://localhost:5000/collections/dataset-<id>/items`
3. Layer name: Your dataset name

### Web Application

```javascript
// Fetch features from OGC endpoint
fetch('http://localhost:5000/collections/dataset-<id>/items')
  .then(response => response.json())
  .then(data => {
    console.log('Features:', data.features);
  });
```

### Spatial Query

```bash
# Get features within bounding box
curl "http://localhost:5001/collections/dataset-<id>/items?bbox=5.0,45.0,6.0,46.0"
```

## Support

- **Documentation**: See `README.md` for detailed information
- **Issues**: Check GitHub Issues for known problems
- **Community**: Ask questions in Dataizen community channels

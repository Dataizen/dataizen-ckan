# OGC Plugin Architecture

## Overview

The OGC integration is now completely contained within the `ckanext-ogc` extension, providing a clean and organized structure.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        CKAN Container                           │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────────────────────────┐ │
│  │   CKAN Core     │    │        OGC Extension                │ │
│  │                 │    │                                     │ │
│  │  - Web UI       │    │  ┌─────────────────────────────────┐ │ │
│  │  - API          │    │  │        Plugin                   │ │ │
│  │  - Datastore    │    │  │                                 │ │ │
│  │  - Search       │    │  │  - Auto startup                 │ │ │
│  │                 │    │  │  - Background sync              │ │ │
│  │                 │    │  │  - CRS detection                │ │ │
│  │                 │    │  │  - Dataset management           │ │ │
│  │                 │    │  └─────────────────────────────────┘ │ │
│  │                 │    │                                     │ │
│  │                 │    │  ┌─────────────────────────────────┐ │ │
│  │                 │    │  │      pygeoapi                   │ │ │
│  │                 │    │  │                                 │ │ │
│  │                 │    │  │  - OGC API endpoints            │ │ │
│  │                 │    │  │  - WFS services                 │ │ │
│  │                 │    │  │  - WMS services                 │ │ │
│  │                 │    │  │  - WMTS services                │ │ │
│  │                 │    │  │  - Port 5001                    │ │ │
│  │                 │    │  └─────────────────────────────────┘ │ │
│  │                 │    │                                     │ │
│  │                 │    │  ┌─────────────────────────────────┐ │ │
│  │                 │    │  │    CKAN Provider                │ │ │
│  │                 │    │  │                                 │ │ │
│  │                 │    │  │  - Data access                  │ │ │
│  │                 │    │  │  - Format conversion           │ │ │
│  │                 │    │  │  - Spatial queries              │ │ │
│  │                 │    │  │  - CRS handling                 │ │ │
│  │                 │    │  └─────────────────────────────────┘ │ │
│  └─────────────────┘    └─────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```
ckanext-ogc/
├── ckanext/
│   └── ogc/
│       ├── __init__.py
│       ├── plugin.py          # Main plugin (startup, sync, management)
│       ├── commands.py        # CLI commands
│       ├── controllers.py     # Route controllers
│       ├── ogc_manager.py     # OGC management
│       └── utils.py           # Utility functions
├── pygeoapi/
│   └── local.config.yml       # pygeoapi configuration
├── pygeoapi-providers/
│   └── ckan_provider/
│       ├── __init__.py
│       ├── ckan_sync.py       # Synchronization logic
│       ├── provider.py        # Main provider
│       ├── sync_tool.py       # Sync tool
│       ├── wfs_provider.py    # WFS provider
│       ├── wms_provider.py    # WMS provider
│       ├── simple_map_provider.py
│       ├── setup.py
│       ├── requirements.txt
│       └── README.md
├── setup.py                   # Extension setup
└── README.md                  # Extension documentation
```

## Data Flow

```
1. CKAN starts with OGC plugin enabled
   ↓
2. Plugin starts pygeoapi on port 5001
   ↓
3. Plugin waits for CKAN and pygeoapi to be ready
   ↓
4. Plugin runs background synchronization
   ↓
5. CKAN Provider connects to CKAN datastore
   ↓
6. OGC services available on port 5001
```

## Benefits of New Structure

### **Organization**
- All OGC code in one place
- Clear separation of concerns
- Easy to find and maintain

### **Modularity**
- Plugin can be easily enabled/disabled
- Self-contained functionality
- No external dependencies

### **Maintainability**
- Single source of truth
- Consistent configuration
- Easy to update and debug

### **Deployment**
- Simple installation process
- No external scripts needed
- Automatic startup and configuration

## Configuration

The plugin is configured in `ckan.ini`:

```ini
# OGC Extension Configuration
ckanext.ogc.pygeoapi_url = http://localhost:5001
ckanext.ogc.pygeoapi_config_path = /srv/app/pygeoapi/local.config.yml
ckanext.ogc.ckan_api_key = ckan-local-dev-apikey
ckanext.ogc.auto_sync = true
ckanext.ogc.collections_base_path = /srv/app/pygeoapi
```

## Testing

Test the complete integration:

```bash
# Test structure
./test-ogc-structure.sh

# Test integration
./test-ogc-integration.sh

# Test services
docker compose exec ckan /srv/app/test-ogc.sh
```

## Access Points

- **CKAN Web UI**: http://localhost:8080
- **CKAN API**: http://localhost:8080/api
- **OGC API**: http://localhost:5001
- **OGC Collections**: http://localhost:5001/collections
- **OGC API Info**: http://localhost:5001/api
- **OpenAPI Spec**: http://localhost:5001/openapi

The OGC integration is now completely self-contained and organized within the extension!

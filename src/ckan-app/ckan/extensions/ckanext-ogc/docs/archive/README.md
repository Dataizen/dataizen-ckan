# OGC Archive

This directory contains historical documentation and configuration files that are no longer used but kept for reference.

## Archived Files

### `README-OGC.md`
Historical documentation describing the manual OGC workflow. This workflow has been replaced by the integrated OGC plugin that handles everything automatically.

**Status:** Superseded by the integrated plugin
**Replacement:** See main `README.md` in the extension root

### `ogc-plugin.conf`
Historical configuration file for the OGC plugin. Configuration is now handled directly in `ckan.ini` during Docker build.

**Status:** Superseded by `ckan.ini` configuration
**Replacement:** Configuration in `ckan.ini` via Dockerfile

## Migration Notes

The OGC integration has evolved from a manual workflow to an automatic plugin-based system:

- **Before:** Manual scripts and separate configuration files
- **After:** Integrated plugin with automatic startup and synchronization

All functionality is now contained within the `ckanext-ogc` extension.











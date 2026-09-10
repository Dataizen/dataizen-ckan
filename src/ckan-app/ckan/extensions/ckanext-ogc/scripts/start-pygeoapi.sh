#!/bin/bash

# Configure matplotlib for optimal rendering
export MPLCONFIGDIR=/tmp/matplotlib-cache
export FONTCONFIG_CACHE=/tmp/fontconfig-cache

# Update pygeoapi config server.url with public URL before starting
PYGEOAPI_CONFIG_FILE="${PYGEOAPI_CONFIG:-/srv/app/pygeoapi/local.config.yml}"

if [ -f "$PYGEOAPI_CONFIG_FILE" ]; then
    # Get public URL from environment variables
    PYGEOAPI_PUBLIC_URL="${PYGEOAPI_PUBLIC_URL:-${PYGEOAPI_URL:-http://localhost:5001}}"
    
    # If URL contains localhost, try to build from CKAN_SITE_URL
    if [[ "$PYGEOAPI_PUBLIC_URL" == *"localhost"* ]]; then
        CKAN_URL="${CKAN_SITE_URL:-${CKAN_URL:-}}"
        if [ -n "$CKAN_URL" ] && [[ "$CKAN_URL" != *"localhost"* ]]; then
            # Extract domain from CKAN URL and build pygeoapi URL
            # Example: https://ckan2.qualif-data.example.org -> https://ogc.ckan2.qualif-data.example.org
            DOMAIN=$(echo "$CKAN_URL" | sed -E 's|https?://||' | sed -E 's|/.*||')
            SCHEME=$(echo "$CKAN_URL" | sed -E 's|://.*||')
            if [[ "$DOMAIN" == *"ckan2"* ]]; then
                PYGEOAPI_PUBLIC_URL="${SCHEME}://ogc.${DOMAIN#ckan2.}"
            else
                PYGEOAPI_PUBLIC_URL="${SCHEME}://ogc.ckan2.${DOMAIN}"
            fi
        fi
    fi
    
    # Remove trailing slash
    PYGEOAPI_PUBLIC_URL="${PYGEOAPI_PUBLIC_URL%/}"
    
    # Update server.url in config file using Python
    python3 << EOF
import yaml
import os

config_file = os.environ.get('PYGEOAPI_CONFIG', '/srv/app/pygeoapi/local.config.yml')
public_url = os.environ.get('PYGEOAPI_PUBLIC_URL', 'http://localhost:5001')

try:
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    
    if 'server' not in config:
        config['server'] = {}
    
    config['server']['url'] = public_url
    
    with open(config_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, indent=2, allow_unicode=True)
    
    print(f"Updated pygeoapi server.url to: {public_url}")
except Exception as e:
    print(f" Could not update pygeoapi config: {e}")
EOF
fi

# Start pygeoapi with optimized configuration
cd /srv/app/pygeoapi && PYGEOAPI_CONFIG=local.config.yml PYGEOAPI_OPENAPI=/srv/app/pygeoapi/openapi.yml pygeoapi serve --flask
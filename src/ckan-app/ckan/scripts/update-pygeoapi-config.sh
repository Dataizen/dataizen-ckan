#!/bin/bash
# Script pour mettre à jour la configuration pygeoapi avec l'URL publique

set -e

CONFIG_FILE="${PYGEOAPI_CONFIG:-/srv/app/pygeoapi/local.config.yml}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo " Configuration file not found: $CONFIG_FILE"
    exit 0
fi

echo "Mise à jour de server.url dans la configuration pygeoapi..."

# Déterminer l'URL publique
PYGEOAPI_PUBLIC_URL="${PYGEOAPI_PUBLIC_URL:-${PYGEOAPI_URL:-http://localhost:5001}}"

# Si URL contient localhost, construire depuis CKAN_SITE_URL
if [[ "$PYGEOAPI_PUBLIC_URL" == *"localhost"* ]]; then
    CKAN_URL="${CKAN_SITE_URL:-${CKAN_URL:-}}"
    if [ -n "$CKAN_URL" ] && [[ "$CKAN_URL" != *"localhost"* ]]; then
        DOMAIN=$(echo "$CKAN_URL" | sed -E 's|https?://||' | sed -E 's|/.*||')
        SCHEME=$(echo "$CKAN_URL" | sed -E 's|://.*||')
        if [[ "$DOMAIN" == *"ckan2"* ]]; then
            PYGEOAPI_PUBLIC_URL="${SCHEME}://ogc.${DOMAIN#ckan2.}"
        else
            PYGEOAPI_PUBLIC_URL="${SCHEME}://ogc.ckan2.${DOMAIN}"
        fi
    fi
fi

# Enlever le trailing slash
PYGEOAPI_PUBLIC_URL="${PYGEOAPI_PUBLIC_URL%/}"
export PYGEOAPI_PUBLIC_URL

# Mettre à jour la configuration avec Python
python3 << 'PYTHON_UPDATE'
import yaml
import os
import sys

config_file = os.environ.get('PYGEOAPI_CONFIG', '/srv/app/pygeoapi/local.config.yml')
public_url = os.environ.get('PYGEOAPI_PUBLIC_URL', 'http://localhost:5001')

try:
    # Charger la configuration existante
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    
    # Mettre à jour server.url
    if 'server' not in config:
        config['server'] = {}
    
    config['server']['url'] = public_url
    
    # Sauvegarder la configuration
    with open(config_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, indent=2, allow_unicode=True)
    
    print(f"Updated pygeoapi server.url to: {public_url}")
    
    # Regenerate openapi.yml if pygeoapi is available
    try:
        import subprocess
        result = subprocess.run(
            ['pygeoapi', 'openapi', 'generate', config_file],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(config_file)
        )
        if result.returncode == 0:
            print(f"Regenerated openapi.yml")
            # Update servers URLs in openapi.yml
            openapi_file = os.path.join(os.path.dirname(config_file), 'openapi.yml')
            if os.path.exists(openapi_file):
                with open(openapi_file, 'r', encoding='utf-8') as f:
                    openapi_data = yaml.safe_load(f) or {}
                if 'servers' not in openapi_data:
                    openapi_data['servers'] = []
                # Replace localhost URLs with public URL
                updated_servers = []
                for server in openapi_data.get('servers', []):
                    if isinstance(server, dict) and 'url' in server:
                        if 'localhost' in server['url']:
                            server['url'] = public_url
                            server['description'] = 'Production server'
                    updated_servers.append(server)
                # Ensure public URL is first
                if not any(isinstance(s, dict) and s.get('url') == public_url for s in updated_servers):
                    updated_servers.insert(0, {'url': public_url, 'description': 'Production server'})
                openapi_data['servers'] = updated_servers
                with open(openapi_file, 'w', encoding='utf-8') as f:
                    yaml.dump(openapi_data, f, default_flow_style=False, indent=2, allow_unicode=True)
                print(f"Updated openapi.yml servers to: {public_url}")
        else:
            print(f" Failed to regenerate openapi.yml: {result.stderr}", file=sys.stderr)
    except Exception as e:
        print(f" Could not regenerate openapi.yml: {e}", file=sys.stderr)
        # Non-fatal, continue
except Exception as e:
    print(f" Could not update pygeoapi config: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_UPDATE


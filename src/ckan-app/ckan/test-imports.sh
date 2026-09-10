#!/bin/bash

# Test script for imports
echo "Testing imports..."

cd /srv/app/pygeoapi-providers/ckan_provider

echo "Testing provider imports..."
python3 -c "
from provider import CKANProvider, WFSProvider, WMSProvider, WMTSProvider
print('Provider classes imported successfully')
"

echo "Testing ckan_sync imports..."
python3 -c "
from ckan_sync import CKANSync
print('CKANSync imported successfully')
"

echo "Testing restore_ogc_links imports..."
python3 -c "
from restore_ogc_links import restore_ogc_links
print('restore_ogc_links imported successfully')
"

echo "Testing sync_tool imports..."
python3 -c "
from sync_tool import CKANSyncTool
print('CKANSyncTool imported successfully')
"

echo "All imports successful!"


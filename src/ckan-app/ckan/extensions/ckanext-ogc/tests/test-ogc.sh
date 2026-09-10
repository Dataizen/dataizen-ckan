#!/bin/bash

echo "Testing OGC services..."

# Test CKAN connectivity
echo "Testing CKAN connectivity..."
curl -s "http://localhost:5000/api/action/site_read" > /dev/null
if [ $? -eq 0 ]; then
    echo "CKAN is accessible"
else
    echo "CKAN is not accessible"
    exit 1
fi

# Test dataset access
echo "Testing dataset access..."
curl -s "http://localhost:5000/api/action/package_show?id=testlmo_avec_records_count" > /dev/null
if [ $? -eq 0 ]; then
    echo "Dataset is accessible"
else
    echo "Dataset is not accessible"
    exit 1
fi

# Test pygeoapi
echo "Testing pygeoapi..."
curl -s "http://localhost:5001/" > /dev/null
if [ $? -eq 0 ]; then
    echo "pygeoapi is accessible"
else
    echo "pygeoapi is not accessible"
    exit 1
fi

# Test collection
echo "Testing collection..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count" > /dev/null
if [ $? -eq 0 ]; then
    echo "Collection is accessible"
else
    echo "Collection is not accessible"
    exit 1
fi

# Test items
echo "Testing items..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/items" > /dev/null
if [ $? -eq 0 ]; then
    echo "Items are accessible"
else
    echo "Items are not accessible"
    exit 1
fi

# Test individual item
echo "Testing individual item..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/items/1" > /dev/null
if [ $? -eq 0 ]; then
    echo "Individual item is accessible"
else
    echo "Individual item is not accessible"
fi

# Test OGC endpoints
echo "Testing OGC endpoints..."

# WFS
echo "  Testing WFS..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wfs" > /dev/null
if [ $? -eq 0 ]; then
    echo "    WFS endpoint accessible"
else
    echo "    WFS endpoint not accessible"
fi

# WMS
echo "  Testing WMS..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wms" > /dev/null
if [ $? -eq 0 ]; then
    echo "    WMS endpoint accessible"
else
    echo "    WMS endpoint not accessible"
fi

# WMTS
echo "  Testing WMTS..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wmts" > /dev/null
if [ $? -eq 0 ]; then
    echo "    WMTS endpoint accessible"
else
    echo "    WMTS endpoint not accessible"
fi

echo "Testing complete"
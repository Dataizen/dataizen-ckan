#!/bin/bash

# Test script for specific issues identified in logs
echo "Testing specific issues from logs..."

# Test 1: Check if individual items work
echo "Test 1: Individual item access..."
curl -s "http://localhost:5001/collections/testlmo_avec_records_count/items/1" > /dev/null
if [ $? -eq 0 ]; then
    echo "Individual item accessible"
else
    echo "Individual item not accessible (expected due to language parameter issue)"
fi

# Test 2: Check if collection page shows OGC links
echo "Test 2: Collection page OGC links..."
response=$(curl -s "http://localhost:5001/collections/testlmo_avec_records_count")
if echo "$response" | grep -q "WFS Service"; then
    echo "WFS link found in collection"
else
    echo "WFS link not found in collection"
fi

if echo "$response" | grep -q "WMS Service"; then
    echo "WMS link found in collection"
else
    echo "WMS link not found in collection"
fi

if echo "$response" | grep -q "WMTS Service"; then
    echo "WMTS link found in collection"
else
    echo "WMTS link not found in collection"
fi

# Test 3: Check CRS issues
echo "Test 3: CRS configuration..."
if echo "$response" | grep -q "CRS84"; then
    echo "CRS84 found in collection"
else
    echo "CRS84 not found in collection"
fi

# Test 4: Check dataset ID
echo "Test 4: Dataset ID configuration..."
if echo "$response" | grep -q "d49d9e41-2d73-4385-9778-f60a35c0e179"; then
    echo "Correct dataset ID found"
else
    echo "Correct dataset ID not found"
fi

# Test 5: Check OGC endpoints directly
echo "Test 5: Direct OGC endpoint access..."

# WFS
echo "  Testing WFS endpoint..."
wfs_response=$(curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wfs")
if [ $? -eq 0 ]; then
    echo "    WFS endpoint accessible"
    if echo "$wfs_response" | grep -q "WFS"; then
        echo "    WFS response contains WFS content"
    else
        echo "     WFS response may not be correct"
    fi
else
    echo "    WFS endpoint not accessible"
fi

# WMS
echo "  Testing WMS endpoint..."
wms_response=$(curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wms")
if [ $? -eq 0 ]; then
    echo "    WMS endpoint accessible"
    if echo "$wms_response" | grep -q "WMS"; then
        echo "    WMS response contains WMS content"
    else
        echo "     WMS response may not be correct"
    fi
else
    echo "    WMS endpoint not accessible"
fi

# WMTS
echo "  Testing WMTS endpoint..."
wmts_response=$(curl -s "http://localhost:5001/collections/testlmo_avec_records_count/wmts")
if [ $? -eq 0 ]; then
    echo "    WMTS endpoint accessible"
    if echo "$wmts_response" | grep -q "WMTS"; then
        echo "    WMTS response contains WMTS content"
    else
        echo "     WMTS response may not be correct"
    fi
else
    echo "    WMTS endpoint not accessible"
fi

echo "Specific issue testing complete"

# Show current configuration
echo "Current configuration summary:"
echo "  Collection: testlmo_avec_records_count"
echo "  Dataset ID: d49d9e41-2d73-4385-9778-f60a35c0e179"
echo "  CRS: http://www.opengis.net/def/crs/OGC/1.3/CRS84"
echo "  BBox: [-5.0, 41.0, 10.0, 51.0]"


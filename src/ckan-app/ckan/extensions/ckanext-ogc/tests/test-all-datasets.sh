#!/bin/bash

# Test script for all datasets in pygeoapi
echo "Testing all datasets in pygeoapi..."

# Get list of collections
echo "Getting list of collections..."
collections_response=$(curl -s "http://localhost:5001/collections")

if [ $? -ne 0 ]; then
    echo "Cannot access pygeoapi collections"
    exit 1
fi

# Extract collection names (simplified - in production you'd use jq)
collection_names=$(echo "$collections_response" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$collection_names" ]; then
    echo "No collections found"
    exit 1
fi

echo "Found collections: $collection_names"

# Test each collection
for collection in $collection_names; do
    echo ""
    echo "Testing collection: $collection"
    
    # Test collection page
    echo "  Testing collection page..."
    collection_response=$(curl -s "http://localhost:5001/collections/$collection")
    if [ $? -eq 0 ]; then
        echo "    Collection page accessible"
        
        # Check for OGC links
        if echo "$collection_response" | grep -q "WFS Service"; then
            echo "    WFS link found"
        else
            echo "    WFS link missing"
        fi
        
        if echo "$collection_response" | grep -q "WMS Service"; then
            echo "    WMS link found"
        else
            echo "    WMS link missing"
        fi
        
        if echo "$collection_response" | grep -q "WMTS Service"; then
            echo "    WMTS link found"
        else
            echo "    WMTS link missing"
        fi
        
        # Check for download links
        if echo "$collection_response" | grep -q "Download as Shapefile"; then
            echo "    Shapefile download link found"
        else
            echo "    Shapefile download link missing"
        fi
        
        if echo "$collection_response" | grep -q "Download as GeoPackage"; then
            echo "    GeoPackage download link found"
        else
            echo "    GeoPackage download link missing"
        fi
    else
        echo "    Collection page not accessible"
    fi
    
    # Test items
    echo "  Testing items..."
    items_response=$(curl -s "http://localhost:5001/collections/$collection/items")
    if [ $? -eq 0 ]; then
        echo "    Items accessible"
        
        # Check if items contain features
        if echo "$items_response" | grep -q '"features"'; then
            feature_count=$(echo "$items_response" | grep -o '"features":\[[^]]*\]' | wc -l)
            echo "    Features found: $feature_count"
        else
            echo "     No features found"
        fi
    else
        echo "    Items not accessible"
    fi
    
    # Test OGC endpoints
    echo "  Testing OGC endpoints..."
    
    # WFS
    wfs_response=$(curl -s "http://localhost:5001/collections/$collection/wfs")
    if [ $? -eq 0 ]; then
        echo "    WFS endpoint accessible"
    else
        echo "    WFS endpoint not accessible"
    fi
    
    # WMS
    wms_response=$(curl -s "http://localhost:5001/collections/$collection/wms")
    if [ $? -eq 0 ]; then
        echo "    WMS endpoint accessible"
    else
        echo "    WMS endpoint not accessible"
    fi
    
    # WMTS
    wmts_response=$(curl -s "http://localhost:5001/collections/$collection/wmts")
    if [ $? -eq 0 ]; then
        echo "    WMTS endpoint accessible"
    else
        echo "    WMTS endpoint not accessible"
    fi
    
    # Test individual item (if features exist)
    if echo "$items_response" | grep -q '"id"'; then
        first_id=$(echo "$items_response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
        if [ -n "$first_id" ]; then
            echo "  Testing individual item: $first_id"
            item_response=$(curl -s "http://localhost:5001/collections/$collection/items/$first_id")
            if [ $? -eq 0 ]; then
                echo "    Individual item accessible"
            else
                echo "    Individual item not accessible"
            fi
        fi
    fi
done

echo ""
echo "Testing complete for all datasets"
echo ""
echo "Summary:"
echo "  - All collections should have OGC service links"
echo "  - All collections should have download links"
echo "  - All OGC endpoints should be accessible"
echo "  - Individual items should be accessible"


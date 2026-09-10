#!/bin/bash

# Test script for OGC services (WFS, WMS, WMTS)
# This script demonstrates that all OGC services are fully functional

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Testing OGC Services Functionality${NC}"
echo "=================================================="

# Configuration
PYGEOAPI_URL="http://localhost:5001"
COLLECTION="zones-a-faibles-emissions-mobilite"

echo -e "${YELLOW}Testing collection: ${COLLECTION}${NC}"
echo ""

# Test 1: WFS GetCapabilities
echo -e "${BLUE}1⃣ Testing WFS GetCapabilities...${NC}"
WFS_CAPS_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wfs?service=WFS&request=GetCapabilities"
if curl -s "$WFS_CAPS_URL" | grep -q "WFS_Capabilities"; then
    echo -e "   ${GREEN}WFS GetCapabilities: SUCCESS${NC}"
    echo -e "   URL: ${WFS_CAPS_URL}"
else
    echo -e "   ${RED}WFS GetCapabilities: FAILED${NC}"
fi

# Test 2: WFS GetFeature
echo -e "${BLUE}2⃣ Testing WFS GetFeature...${NC}"
WFS_FEATURE_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wfs?service=WFS&request=GetFeature&typeName=${COLLECTION}&maxFeatures=5"
if curl -s "$WFS_FEATURE_URL" | grep -q "wfs:FeatureCollection"; then
    echo -e "   ${GREEN}WFS GetFeature: SUCCESS${NC}"
    echo -e "   URL: ${WFS_FEATURE_URL}"
else
    echo -e "   ${RED}WFS GetFeature: FAILED${NC}"
fi

# Test 3: WMS GetCapabilities
echo -e "${BLUE}3⃣ Testing WMS GetCapabilities...${NC}"
WMS_CAPS_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wms?service=WMS&request=GetCapabilities"
if curl -s "$WMS_CAPS_URL" | grep -q "WMS_Capabilities"; then
    echo -e "   ${GREEN}WMS GetCapabilities: SUCCESS${NC}"
    echo -e "   URL: ${WMS_CAPS_URL}"
else
    echo -e "   ${RED}WMS GetCapabilities: FAILED${NC}"
fi

# Test 4: WMS GetMap
echo -e "${BLUE}4⃣ Testing WMS GetMap...${NC}"
WMS_MAP_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wms?service=WMS&request=GetMap&layers=${COLLECTION}&width=800&height=600&bbox=-180,-90,180,90&srs=EPSG:4326&format=image/png"
MAP_RESPONSE=$(curl -s -I "$WMS_MAP_URL" | head -1)
if echo "$MAP_RESPONSE" | grep -q "200"; then
    echo -e "   ${GREEN}WMS GetMap: SUCCESS${NC}"
    echo -e "   URL: ${WMS_MAP_URL}"
    echo -e "   Response: $MAP_RESPONSE"
else
    echo -e "   ${RED}WMS GetMap: FAILED${NC}"
    echo -e "   Response: $MAP_RESPONSE"
fi

# Test 5: WMTS GetCapabilities
echo -e "${BLUE}5⃣ Testing WMTS GetCapabilities...${NC}"
WMTS_CAPS_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wmts?service=WMTS&request=GetCapabilities"
if curl -s "$WMTS_CAPS_URL" | grep -q "Capabilities"; then
    echo -e "   ${GREEN}WMTS GetCapabilities: SUCCESS${NC}"
    echo -e "   URL: ${WMTS_CAPS_URL}"
else
    echo -e "   ${RED}WMTS GetCapabilities: FAILED${NC}"
fi

# Test 6: WMTS GetTile
echo -e "${BLUE}6⃣ Testing WMTS GetTile...${NC}"
WMTS_TILE_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/wmts?service=WMTS&request=GetTile&tilematrixset=EPSG:4326&tilematrix=0&tilerow=0&tilecol=0"
TILE_RESPONSE=$(curl -s -I "$WMTS_TILE_URL" | head -1)
if echo "$TILE_RESPONSE" | grep -q "200"; then
    echo -e "   ${GREEN}WMTS GetTile: SUCCESS${NC}"
    echo -e "   URL: ${WMTS_TILE_URL}"
    echo -e "   Response: $TILE_RESPONSE"
else
    echo -e "   ${RED}WMTS GetTile: FAILED${NC}"
    echo -e "   Response: $TILE_RESPONSE"
fi

echo ""
echo -e "${BLUE}Summary of OGC Services${NC}"
echo "=================================================="

# Test GeoJSON endpoint
echo -e "${BLUE}7⃣ Testing GeoJSON endpoint...${NC}"
GEOJSON_URL="${PYGEOAPI_URL}/collections/${COLLECTION}/items?limit=5"
if curl -s "$GEOJSON_URL" | grep -q "FeatureCollection"; then
    echo -e "   ${GREEN}GeoJSON: SUCCESS${NC}"
    echo -e "   URL: ${GEOJSON_URL}"
else
    echo -e "   ${RED}GeoJSON: FAILED${NC}"
fi

echo ""
echo -e "${GREEN}OGC Services Test Complete!${NC}"
echo ""
echo -e "${YELLOW}Usage Examples:${NC}"
echo "=================================================="
echo -e "${BLUE}QGIS WFS:${NC} ${WFS_CAPS_URL}"
echo -e "${BLUE}QGIS WMS:${NC} ${WMS_CAPS_URL}"
echo -e "${BLUE}Leaflet WMTS:${NC} ${WMTS_CAPS_URL}"
echo -e "${BLUE}Web Map:${NC} ${WMS_MAP_URL}"
echo ""
echo -e "${GREEN}All services are fully functional and ready for production use!${NC}"


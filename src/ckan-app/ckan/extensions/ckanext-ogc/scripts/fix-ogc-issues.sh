#!/bin/bash

# Script pour diagnostiquer et corriger les problèmes OGC
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Diagnostic et correction des problèmes OGC${NC}"
echo "=================================================="

# 1. Vérifier que pygeoapi est arrêté
echo -e "${YELLOW}1⃣ Arrêt de pygeoapi...${NC}"
pkill -f pygeoapi || true
sleep 2

# 2. Synchroniser les données CKAN
echo -e "${YELLOW}2⃣ Synchronisation des données CKAN...${NC}"
python3 /srv/app/pygeoapi-providers/ckan_provider/sync_tool.py --action sync-all --limit 20

# 3. Restaurer les liens OGC
echo -e "${YELLOW}3⃣ Restauration des liens OGC...${NC}"
python3 /srv/app/pygeoapi-providers/ckan_provider/restore_ogc_links.py

# 4. Vérifier la configuration
echo -e "${YELLOW}4⃣ Vérification de la configuration...${NC}"
echo "Collections dans la config:"
grep -A 5 "type: collection" /srv/app/pygeoapi/local.config.yml | grep "title:" || echo "Aucune collection trouvée"

# 5. Redémarrer pygeoapi
echo -e "${YELLOW}5⃣ Redémarrage de pygeoapi...${NC}"
/srv/app/start-pygeoapi.sh &
sleep 5

# 6. Test des endpoints
echo -e "${YELLOW}6⃣ Test des endpoints...${NC}"
echo "Test de la collection 0609_wfs_v1:"
curl -s "http://localhost:5001/collections/0609_wfs_v1/items?limit=1" | jq '.features[0].geometry' 2>/dev/null || echo "Erreur ou pas de géométrie"

echo -e "${GREEN}Diagnostic terminé !${NC}"
echo ""
echo -e "${BLUE}Prochaines étapes :${NC}"
echo "1. Vérifier que les collections sont bien synchronisées"
echo "2. Tester les services OGC :"
echo "   - WFS: http://localhost:5001/collections/0609_wfs_v1/wfs"
echo "   - WMS: http://localhost:5001/collections/0609_wfs_v1/wms"
echo "   - WMTS: http://localhost:5001/collections/0609_wfs_v1/wmts"


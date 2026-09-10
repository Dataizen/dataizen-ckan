#!/bin/bash

# Script simple pour lister les noms des dumps

echo "Liste des Dumps SQL Disponibles"
echo "==================================="
echo ""

echo "Dumps directement liés à CKAN:"
echo "  ckan_20250124.sql"
echo "  datastore_20250124.sql"
echo ""

echo "Dumps complémentaires (données externes):"
echo "  catalogue_20250123.sql (26M) - Métadonnées ISO 19139"
echo "  datagis_20250124.sql (42G) - Base géospatiale majeure"
echo "  idgo_admin_20250123.sql (662M) - Données administratives"
echo "  onegeo_admin_20250123.sql (214M) - Système de recherche"
echo "  collab_20250123.sql (25M) - Collaboration géographique"
echo "  extractor_20250123.sql (444K) - Fonctions PostGIS"
echo ""

echo "Pour plus de détails, voir: NOMS_DUMPS_ET_RELATIONS.md"




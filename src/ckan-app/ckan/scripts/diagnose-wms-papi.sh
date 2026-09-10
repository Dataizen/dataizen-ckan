#!/bin/bash
# Script pour diagnostiquer le problème WMS PAPI

echo "DIAGNOSTIC WMS PAPI"
echo "=" | head -c 80 && echo ""
echo ""

# Exécuter les requêtes SQL
PGPASSWORD=ckan psql -h db -U ckan -d datagis -f /srv/app/scripts/diagnose-wms-papi.sql

echo ""
echo "=" | head -c 80 && echo ""
echo "RÉSUMÉ"
echo ""
echo "Si le SRID des géométries est 900914 (invalide) :"
echo "  → Les géométries ne peuvent pas être lues par MapServer"
echo "  → Solution: Réimporter les données avec le bon SRID"
echo ""
echo "Si l'extent ne correspond pas :"
echo "  → Régénérer le mapfile avec le bon extent"
echo ""
echo "Si les géométries sont invalides :"
echo "  → Corriger avec ST_MakeValid()"
echo ""

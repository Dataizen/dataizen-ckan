#!/bin/bash
# Script de vérification rapide des performances système

echo "Vérification des performances système CKAN"
echo "=============================================="

# CPU
echo ""
echo "CPU:"
echo "   Utilisation:"
top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print "   - Idle: " 100 - $1 "%"}'
echo "   Processus CKAN:"
ps aux | grep -E "ckan|gunicorn|paster" | grep -v grep | wc -l | awk '{print "   - Nombre de processus: " $1}'

# Mémoire
echo ""
echo "Mémoire:"
free -h | grep Mem | awk '{print "   - Total: " $2}'
free -h | grep Mem | awk '{print "   - Utilisé: " $3 " (" $3/$2*100 "%)"}'
free -h | grep Mem | awk '{print "   - Disponible: " $7}'

# Disque
echo ""
echo "Disque:"
df -h / | tail -1 | awk '{print "   - Utilisation: " $5 " (" $3 "/" $2 ")"}'
df -h /mapserver/mapfiles 2>/dev/null | tail -1 | awk '{print "   - Mapfiles: " $5 " (" $3 "/" $2 ")"}' || echo "   - Mapfiles: N/A"

# OSError dans les logs récents
echo ""
echo "OSError récents (dernières 1000 lignes):"
if [ -f /var/log/ckan/ckan.log ]; then
    tail -1000 /var/log/ckan/ckan.log | grep -i "oserror\|write error" | wc -l | awk '{print "   - Nombre: " $1}'
elif command -v docker &> /dev/null; then
    echo "   Utilisez: docker compose logs --tail=1000 ckan | grep -i 'oserror\|write error' | wc -l"
else
    echo "   - Logs non disponibles"
fi

# Connexions DB
echo ""
echo "Base de données:"
if command -v psql &> /dev/null; then
    psql -h db -U ckan -d ckan -c "SELECT count(*) as active_connections FROM pg_stat_activity WHERE state = 'active';" 2>/dev/null | tail -3 | head -1 | awk '{print "   - Connexions actives: " $1}'
else
    echo "   - psql non disponible"
fi

# Temps de réponse (si curl disponible)
echo ""
echo "Temps de réponse API:"
if command -v curl &> /dev/null; then
    START=$(date +%s.%N)
    curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/api/3/action/status_show 2>/dev/null
    END=$(date +%s.%N)
    DURATION=$(echo "$END - $START" | bc 2>/dev/null || echo "N/A")
    echo "   - API status_show: ${DURATION}s"
else
    echo "   - curl non disponible"
fi

echo ""
echo "=============================================="

#!/bin/bash
set -euo pipefail

echo "Configuration du cron job pour les harvests planifiés..."

# Créer le répertoire de log si nécessaire (avec sudo si nécessaire)
sudo mkdir -p /var/log 2>/dev/null || mkdir -p /var/log 2>/dev/null || true
sudo touch /var/log/harvest-run.log 2>/dev/null || touch /var/log/harvest-run.log 2>/dev/null || true
sudo chmod 0644 /var/log/harvest-run.log 2>/dev/null || chmod 0644 /var/log/harvest-run.log 2>/dev/null || true

# --- 1) Fichier pour cron standard (/etc/cron.d) si cron Debian est utilisé ---
CRON_D_CONTENT="# Vérifier et lancer les harvests planifiés toutes les heures
# Ce cron appelle 'ckan harvester run' qui vérifie les sources avec next_run <= NOW()
# Utiliser l'utilisateur ckan (pas root) pour que busybox crond et cron en conteneur fonctionnent
0 * * * * ckan bash -c \"cd /srv/app && ckan -c /srv/app/ckan.ini harvester run\" >> /var/log/harvest-run.log 2>&1"

if ! echo "$CRON_D_CONTENT" | sudo tee /etc/cron.d/harvest-run > /dev/null 2>&1; then
    echo "Impossible de créer /etc/cron.d/harvest-run avec sudo, tentative alternative..."
    echo "$CRON_D_CONTENT" > /tmp/harvest-run.tmp
    sudo cp /tmp/harvest-run.tmp /etc/cron.d/harvest-run 2>/dev/null || {
        echo "Impossible de créer le fichier cron /etc/cron.d, le cron ne lancera pas les harvests"
        rm -f /tmp/harvest-run.tmp
    }
fi
sudo chmod 0644 /etc/cron.d/harvest-run 2>/dev/null || chmod 0644 /etc/cron.d/harvest-run 2>/dev/null || true

# --- 2) Crontab pour l'utilisateur ckan (lu par BusyBox crond) ---
# BusyBox crond ne lit PAS /etc/cron.d, il lit uniquement /var/spool/cron/crontabs/<user>
# Format: minute heure jour mois jour-semaine commande (pas de champ "user")
CRONTAB_LINE="0 * * * * cd /srv/app && ckan -c /srv/app/ckan.ini harvester run >> /var/log/harvest-run.log 2>&1"
CRONTAB_DIR="/var/spool/cron/crontabs"
CRONTAB_FILE="${CRONTAB_DIR}/ckan"

sudo mkdir -p "${CRONTAB_DIR}" 2>/dev/null || true
if sudo test -d "${CRONTAB_DIR}"; then
    echo "# Harvests planifiés (next_run) - exécution toutes les heures" | sudo tee "${CRONTAB_FILE}" > /dev/null
    echo "$CRONTAB_LINE" | sudo tee -a "${CRONTAB_FILE}" > /dev/null
    sudo chown ckan:ckan "${CRONTAB_FILE}" 2>/dev/null || true
    sudo chmod 0600 "${CRONTAB_FILE}" 2>/dev/null || true
    echo "Crontab ckan créé (pour BusyBox crond): ${CRONTAB_FILE}"
else
    echo "Impossible de créer ${CRONTAB_DIR}, BusyBox crond ne lira pas de crontab harvest"
fi

echo "Cron job configuré pour les harvests planifiés"
echo "Le cron vérifie toutes les heures les sources prêtes à être moissonnées (next_run)"
echo "Logs: /var/log/harvest-run.log"
echo "Si les moissonnages ne se lancent pas, le programme supervisor 'harvest-scheduler' est une alternative (voir harvester.conf)"


#!/bin/bash

echo "Configuration du cron job pour la maintenance plugin_extras..."

# Créer le fichier cron
cat > /etc/cron.d/fix-plugin-extras << EOF
# Maintenance plugin_extras toutes les heures
0 * * * * root /srv/app/fix-plugin-extras.sh >> /var/log/fix-plugin-extras.log 2>&1
EOF

# Donner les permissions
chmod 0644 /etc/cron.d/fix-plugin-extras

# Créer le fichier de log
touch /var/log/fix-plugin-extras.log
chmod 0644 /var/log/fix-plugin-extras.log

echo "Cron job configuré pour la maintenance automatique"

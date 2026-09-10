#!/bin/bash
# Script wrapper pour démarrer supervisor après avoir configuré SECRET_KEY

set -euo pipefail

echo "Démarrage de supervisor avec configuration préalable..."

# Charger les variables d'environnement du .env si le fichier existe
if [ -f "/srv/app/.env" ]; then
    echo "Chargement des variables depuis .env..."
    export $(grep -v '^#' /srv/app/.env | xargs)
fi

# Exécuter les scripts docker-entrypoint.d AVANT de démarrer supervisor
# Cela garantit que SECRET_KEY est configuré avant que les consumers démarrent
if [[ -d "/docker-entrypoint.d" ]]; then
    echo "Exécution des scripts d'initialisation..."
    for f in /docker-entrypoint.d/*; do
        case "$f" in
            *.sh)
                if [ -x "$f" ]; then
                    echo "Exécution de $f..."
                    # Utiliser bash au lieu de source pour éviter que exit termine le script parent
                    bash "$f" || echo "Erreur lors de l'exécution de $f"
                fi
                ;;
            *.py)
                if [ -x "$f" ]; then
                    echo "Exécution de $f..."
                    python3 "$f" || echo "Erreur lors de l'exécution de $f"
                fi
                ;;
            *)
                echo "Ignoré: $f (pas un script sh ou py)"
                ;;
        esac
    done
    echo "Scripts d'initialisation terminés"
fi

# Vérifier que SECRET_KEY est configuré
if ! grep -qE "^SECRET_KEY\s*=" /srv/app/ckan.ini || grep -qE "^SECRET_KEY\s*=\s*$" /srv/app/ckan.ini; then
    echo "SECRET_KEY n'est toujours pas configuré, configuration d'urgence..."
    ckan config-tool /srv/app/ckan.ini "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')"
    ckan config-tool /srv/app/ckan.ini "WTF_CSRF_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')"
    JWT_SECRET=$(python3 -c 'import secrets; print("string:" + secrets.token_urlsafe())')
    ckan config-tool /srv/app/ckan.ini "api_token.jwt.encode.secret=${JWT_SECRET}"
    ckan config-tool /srv/app/ckan.ini "api_token.jwt.decode.secret=${JWT_SECRET}"
    echo "SECRET_KEY configuré d'urgence"
fi

# Attendre que les plugins soient chargés avant de démarrer supervisor
echo "Attente que les plugins CKAN soient chargés..."
MAX_WAIT=30
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    # Vérifier que les harvesters sont disponibles
    if python3 -c "
import sys
sys.path.insert(0, '/srv/app/src/ckan')
try:
    from ckan.plugins import PluginImplementations
    from ckanext.harvest.interfaces import IHarvester
    harvesters = list(PluginImplementations(IHarvester))
    if len(harvesters) > 0:
        exit(0)
    else:
        exit(1)
except Exception:
    exit(1)
" 2>/dev/null; then
        echo "Plugins harvesters chargés"
        break
    fi
    echo "Attente des plugins... (${WAITED}s/${MAX_WAIT}s)"
    sleep 2
    WAITED=$((WAITED + 2))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "Timeout: Les plugins ne sont pas encore chargés, mais on démarre supervisor quand même"
fi

echo "Configuration terminée, démarrage de supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf


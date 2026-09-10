#!/bin/bash
# Wrapper script pour démarrer cron avec les bonnes permissions pour le fichier PID
# Dans Docker avec USER ckan, cron standard nécessite des capacités spéciales (seteuid)
# Solution: désactiver cron si seteuid échoue pour éviter les boucles de redémarrage

set +e  # Ne pas échouer si cron ne peut pas démarrer
set -u  # Utiliser les variables non définies comme erreur
set -o pipefail  # Les pipelines échouent si une commande échoue

# Créer le répertoire /tmp pour le PID si /var/run n'est pas accessible
PID_DIR="/tmp"
PID_FILE="${PID_DIR}/crond.pid"

# Créer le répertoire pour le PID si nécessaire
mkdir -p "${PID_DIR}" 2>/dev/null || true
chmod 1777 "${PID_DIR}" 2>/dev/null || true

# Vérifier si busybox crond est disponible (plus simple, pas de seteuid)
BUSYBOX_CROND_AVAILABLE=false
if command -v busybox >/dev/null 2>&1; then
    # Vérifier si busybox a l'applet crond compilé
    # Tester directement si busybox crond peut être exécuté
    # Si l'applet n'existe pas, busybox retourne "applet not found"
    TEST_OUTPUT=$(busybox crond 2>&1)
    TEST_EXIT_CODE=$?
    # Si l'applet existe, même avec des arguments invalides, il ne dira pas "applet not found"
    if ! echo "$TEST_OUTPUT" | grep -q "applet not found"; then
        BUSYBOX_CROND_AVAILABLE=true
    fi
fi

if [ "$BUSYBOX_CROND_AVAILABLE" = true ]; then
    echo "Utilisation de busybox crond (plus simple, pas de seteuid)"
    # busybox crond lit les fichiers cron depuis /etc/cron.d et /var/spool/cron/crontabs
    # Il ne nécessite pas de changer d'utilisateur
    exec busybox crond -f -l 2
elif [ -x /usr/sbin/cron ]; then
    # Essayer de lancer cron standard
    # Note: cela peut échouer avec seteuid si on n'est pas root
    echo "Lancement de cron standard..."
    
    # Tester si cron peut démarrer sans erreur seteuid
    # En redirigeant stderr vers stdout pour capturer l'erreur
    /usr/sbin/cron -f 2>&1 &
    CRON_PID=$!
    
    # Attendre un peu pour voir si cron démarre correctement
    sleep 2
    
    # Vérifier si le processus cron est toujours en vie
    if kill -0 $CRON_PID 2>/dev/null; then
        echo "Cron démarré avec succès (PID: $CRON_PID)"
        # Attendre que cron se termine (normalement jamais)
        wait $CRON_PID
    else
        echo "Cron a échoué (probablement seteuid: Operation not permitted)"
        echo "Les tâches cron ne seront pas exécutées automatiquement"
        echo "Utilisez 'ckan harvester run' manuellement ou via un autre mécanisme"
        echo "Pour activer cron, lancez le conteneur avec --cap-add=SYS_ADMIN ou en tant que root"
        # Garder le processus en vie pour que supervisor ne le redémarre pas en boucle
        # mais avec autorestart=false dans supervisor.d/cron.conf
        sleep infinity
    fi
else
    echo "Cron non trouvé, désactivation..."
    echo "Les tâches cron ne seront pas exécutées automatiquement"
    sleep infinity
fi

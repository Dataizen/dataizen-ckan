#!/bin/bash

# TEMPORAIREMENT DÉSACTIVÉ - Pour réactiver Keycloak, décommenter le contenu ci-dessous
echo " Configuration Keycloak temporairement désactivée"
# Le script se termine ici, pas besoin de exit car il est exécuté dans un sous-shell

# ===== DÉBUT DU CODE KEYCLOAK (DÉSACTIVÉ TEMPORAIREMENT) =====
# echo "Configuration Keycloak au démarrage..."

# Configuration permanente au build (sans variables d'environnement)
echo "Configuration permanente Keycloak au build..."

# Configuration des templates personnalisés
echo "Configuration des templates personnalisés..."
ckan config-tool /srv/app/ckan.ini "extra_template_paths = /srv/app/templates"

# Configuration des paramètres Keycloak utiles
echo "Configuration des paramètres Keycloak..."
ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.scope = openid profile email"
ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.debug = true"

# Configuration des variables d'environnement au runtime (si disponibles)
if [ -f "/srv/app/.env" ]; then
    echo "Chargement des variables depuis .env..."
    export $(grep -v '^#' /srv/app/.env | xargs)
fi

# Configurer Keycloak si les variables sont définies
if [ ! -z "$KEYCLOAK_ISSUER_URL" ]; then
    echo "Configuration du serveur Keycloak: $KEYCLOAK_ISSUER_URL"
    # Extraire le realm name de l'URL issuer (format: https://keycloak.example.com/auth/realms/dataizen)
    REALM_NAME=$(echo "$KEYCLOAK_ISSUER_URL" | sed -n 's|.*/realms/\([^/]*\)|\1|p')
    echo "Nom du realm: $REALM_NAME"
    ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.realm_name = $REALM_NAME"
    
    # Utiliser l'URL de base du serveur Keycloak (sans /realms/dataizen)
    SERVER_URL="${KEYCLOAK_ISSUER_URL%/realms/$REALM_NAME}"
    echo "URL du serveur Keycloak: $SERVER_URL"
    ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.server_url = $SERVER_URL"
else
    echo "KEYCLOAK_ISSUER_URL non défini"
fi

if [ ! -z "$CKAN_OIDC_REDIRECT_URI" ]; then
    echo "Configuration de l'URI de redirection: $CKAN_OIDC_REDIRECT_URI"
    ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.redirect_uri = $CKAN_OIDC_REDIRECT_URI"
else
    echo "CKAN_OIDC_REDIRECT_URI non défini"
fi

if [ ! -z "$CKAN_OIDC_CLIENT_SECRET" ]; then
    echo "Configuration du secret client"
    ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.client_secret_key = $CKAN_OIDC_CLIENT_SECRET"
else
    echo "CKAN_OIDC_CLIENT_SECRET non défini"
fi

if [ ! -z "$CKAN_OIDC_CLIENT_ID" ]; then
    echo "Configuration du client ID"
    ckan config-tool /srv/app/ckan.ini "ckanext.keycloak.client_id = $CKAN_OIDC_CLIENT_ID"
else
    echo "CKAN_OIDC_CLIENT_ID non défini"
fi

echo "Configuration Keycloak terminée"

# Fix pour éviter les données plugin_extras corrompues
echo "Correction du type de colonne plugin_extras si nécessaire..."

# Vérifier et convertir le type de la colonne plugin_extras de text à jsonb
python3 << 'EOF'
import psycopg2
import os

try:
    # Connexion à la base de données
    conn_str = os.environ.get('CKAN_SQLALCHEMY_URL', '')
    if not conn_str:
        print('CKAN_SQLALCHEMY_URL non défini')
        exit(0)
    
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()
    
    # Vérifier le type actuel de la colonne
    cursor.execute("""
        SELECT data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'public' 
        AND table_name = 'user' 
        AND column_name = 'plugin_extras'
    """)
    
    result = cursor.fetchone()
    if result and result[0] == 'text':
        print('Conversion de plugin_extras de text à jsonb...')
        cursor.execute("""
            ALTER TABLE public."user" 
            ALTER COLUMN plugin_extras TYPE jsonb USING plugin_extras::jsonb
        """)
        conn.commit()
        print('Colonne plugin_extras convertie en jsonb')
    elif result and result[0] == 'jsonb':
        print('Colonne plugin_extras déjà en jsonb')
    else:
        print(f'Type de colonne inattendu: {result[0] if result else "colonne absente"}')
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f'Erreur lors de la conversion: {e}')
EOF

echo "Application du correctif plugin_extras..."
python3 -c "
import ckan.model as model
from ckan.model import User
import json

# Nettoyer les plugin_extras corrompus au démarrage
try:
    users = model.Session.query(User).filter(User.plugin_extras.isnot(None)).all()
    fixed_count = 0
    
    for user in users:
        try:
            extras = user.plugin_extras
            
            # Vérifier si c'est une chaîne (corrompu)
            if isinstance(extras, str):
                print(f'Correction utilisateur {user.name}: plugin_extras était une chaîne')
                user.plugin_extras = None
                fixed_count += 1
            # Vérifier si c'est un dict avec 'idp': 'google' (incorrect)
            elif isinstance(extras, dict) and extras.get('idp') == 'google':
                print(f'Correction utilisateur {user.name}: idp était google au lieu de keycloak')
                user.plugin_extras = {'idp': 'keycloak'}
                fixed_count += 1
            # Vérifier si c'est un dict valide mais sans idp
            elif isinstance(extras, dict) and 'idp' not in extras:
                print(f'Correction utilisateur {user.name}: ajout de idp keycloak')
                user.plugin_extras = {'idp': 'keycloak'}
                fixed_count += 1
                
        except Exception as e:
            print(f'Erreur avec utilisateur {user.name}: {e}')
            user.plugin_extras = None
            fixed_count += 1
    
    if fixed_count > 0:
        model.Session.commit()
        print(f'{fixed_count} utilisateurs corrigés')
    else:
        print('Aucun problème détecté')
        
except Exception as e:
    print(f'Erreur lors du nettoyage: {e}')
"
# ===== FIN DU CODE KEYCLOAK (DÉSACTIVÉ TEMPORAIREMENT) =====

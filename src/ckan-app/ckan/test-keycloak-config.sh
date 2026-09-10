#!/bin/bash

echo "Lancement du script de test Keycloak pour CKAN..."

# Charger les variables d'environnement
if [ -f "/srv/app/.env" ]; then
    echo "Fichier .env trouvé (non chargé directement, mais les variables devraient être disponibles)"
else
    echo "Fichier .env non trouvé. Assurez-vous qu'il est monté ou que les variables sont définies."
fi

# Vérifier les variables d'environnement nécessaires
echo ""
echo "Vérification des variables d'environnement..."
if [ -z "$KEYCLOAK_ISSUER_URL" ]; then echo "KEYCLOAK_ISSUER_URL non défini"; else echo "KEYCLOAK_ISSUER_URL: $KEYCLOAK_ISSUER_URL"; fi
if [ -z "$CKAN_OIDC_CLIENT_SECRET" ]; then echo "CKAN_OIDC_CLIENT_SECRET non défini"; else echo "CKAN_OIDC_CLIENT_SECRET est défini"; fi
if [ -z "$CKAN_OIDC_REDIRECT_URI" ]; then echo "CKAN_OIDC_REDIRECT_URI non défini"; else echo "CKAN_OIDC_REDIRECT_URI: $CKAN_OIDC_REDIRECT_URI"; fi

# Tester la connectivité Keycloak
echo ""
echo "Test de connectivité Keycloak..."
KEYCLOAK_REALM_URL_TEST="${KEYCLOAK_ISSUER_URL}"
if curl -s -o /dev/null -w "%{http_code}" "$KEYCLOAK_REALM_URL_TEST" | grep -q "200"; then
    echo "Keycloak Realm URL accessible: $KEYCLOAK_REALM_URL_TEST"
else
    echo "Keycloak Realm URL non accessible: $KEYCLOAK_REALM_URL_TEST"
fi

KEYCLOAK_AUTH_ENDPOINT_TEST="${KEYCLOAK_ISSUER_URL}/protocol/openid-connect/auth"
if curl -s -o /dev/null -w "%{http_code}" "$KEYCLOAK_AUTH_ENDPOINT_TEST" | grep -q "200"; then
    echo "Endpoint d'autorisation accessible"
else
    echo "Endpoint d'autorisation non accessible"
fi

KEYCLOAK_TOKEN_ENDPOINT_TEST="${KEYCLOAK_ISSUER_URL}/protocol/openid-connect/token"
if curl -s -o /dev/null -w "%{http_code}" "$KEYCLOAK_TOKEN_ENDPOINT_TEST" | grep -q "200"; then
    echo "Endpoint de token accessible"
else
    echo "Endpoint de token non accessible"
fi

KEYCLOAK_USERINFO_ENDPOINT_TEST="${KEYCLOAK_ISSUER_URL}/protocol/openid-connect/userinfo"
if curl -s -o /dev/null -w "%{http_code}" "$KEYCLOAK_USERINFO_ENDPOINT_TEST" | grep -q "200"; then
    echo "Endpoint UserInfo accessible"
else
    echo "Endpoint UserInfo non accessible"
fi

KEYCLOAK_JWKS_URI_TEST="${KEYCLOAK_ISSUER_URL}/protocol/openid-connect/certs"
if curl -s -o /dev/null -w "%{http_code}" "$KEYCLOAK_JWKS_URI_TEST" | grep -q "200"; then
    echo "Endpoint JWKS accessible"
else
    echo "Endpoint JWKS non accessible"
fi

# Vérifier la configuration CKAN
echo ""
echo "Vérification de la configuration CKAN..."

if [ -f "/srv/app/ckan.ini" ]; then
    echo "Fichier de configuration CKAN trouvé"

    # Vérifier que le plugin keycloak est activé
    if grep -q "keycloak" /srv/app/ckan.ini; then
        echo "Plugin Keycloak activé dans CKAN"
    else
        echo "Plugin Keycloak non activé dans CKAN"
    fi

    # Vérifier la configuration Keycloak
    if grep -q "ckanext.keycloak.client_id" /srv/app/ckan.ini; then
        CLIENT_ID=$(grep "ckanext.keycloak.client_id" /srv/app/ckan.ini | cut -d'=' -f2 | tr -d ' ')
        echo "Client ID Keycloak configuré: $CLIENT_ID"
    else
        echo "Client ID Keycloak non configuré"
    fi

    if grep -q "ckanext.keycloak.realm_name" /srv/app/ckan.ini; then
        REALM=$(grep "ckanext.keycloak.realm_name" /srv/app/ckan.ini | cut -d'=' -f2 | tr -d ' ')
        echo "Realm Keycloak configuré: $REALM"
    else
        echo "Realm Keycloak non configuré"
    fi

    if grep -q "ckanext.keycloak.server_url" /srv/app/ckan.ini; then
        SERVER_URL=$(grep "ckanext.keycloak.server_url" /srv/app/ckan.ini | cut -d'=' -f2 | tr -d ' ')
        echo "Server URL Keycloak configuré: $SERVER_URL"
    else
        echo "Server URL Keycloak non configuré"
    fi

    if grep -q "ckanext.keycloak.redirect_uri" /srv/app/ckan.ini; then
        REDIRECT_URI=$(grep "ckanext.keycloak.redirect_uri" /srv/app/ckan.ini | cut -d'=' -f2 | tr -d ' ')
        echo "Redirect URI configuré: $REDIRECT_URI"
    else
        echo "Redirect URI non configuré"
    fi
else
    echo "Fichier de configuration CKAN non trouvé"
fi

echo ""
echo "Résumé du test:"
echo "- Variables d'environnement: "
echo "- Connectivité Keycloak: "
echo "- Configuration CKAN Keycloak: "
echo ""
echo "Prochaines étapes:"
echo "1. Créer le client 'ckan-dataizen' dans Keycloak"
echo "2. Configurer les Valid Redirect URIs: http://localhost:8080/user/sso_login"
echo "3. Tester la connexion SSO"

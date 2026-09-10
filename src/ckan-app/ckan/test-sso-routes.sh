#!/bin/bash

echo "Test des routes SSO disponibles..."

# Tester les routes possibles
echo "Test de /user/sso..."
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/user/sso

echo ""
echo "Test de /user/sso_login..."
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/user/sso_login

echo ""
echo "Test de /keycloak/sso..."
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/keycloak/sso

echo ""
echo "Routes testées !"

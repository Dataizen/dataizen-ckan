#!/bin/bash
set -e

echo "Configuration du pool de connexions SQLAlchemy..."

# Définir CKAN_INI si non défini
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Configuration du pool de connexions SQLAlchemy dans la section [app:main]
# Ces paramètres permettent de gérer les connexions à la base de données
echo "Configuration du pool_size SQLAlchemy..."
ckan config-tool "$CKAN_INI" "sqlalchemy.pool_size=30" || echo "Impossible de configurer pool_size"

echo "Configuration du max_overflow SQLAlchemy..."
ckan config-tool "$CKAN_INI" "sqlalchemy.max_overflow=50" || echo "Impossible de configurer max_overflow"

echo "Configuration du pool_timeout SQLAlchemy..."
ckan config-tool "$CKAN_INI" "sqlalchemy.pool_timeout=180" || echo "Impossible de configurer pool_timeout"

echo "Configuration du pool_recycle SQLAlchemy..."
ckan config-tool "$CKAN_INI" "sqlalchemy.pool_recycle=1800" || echo "Impossible de configurer pool_recycle"

ckan config-tool "$CKAN_INI" "sqlalchemy.pool_pre_ping=true"
ckan config-tool "$CKAN_INI" "qlalchemy.pool_reset_on_return=rollback"






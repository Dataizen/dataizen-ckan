#!/bin/bash
# Script pour créer les tables manquantes de l'extension showcase

set -euo pipefail

echo "Correction des tables showcase..."

# Variables d'environnement
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-ckan}"
POSTGRES_USER="${POSTGRES_USER:-ckan}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ckan}"

# Vérifier si on est dans un conteneur Docker ou localement
if [ -f /.dockerenv ] || [ -n "${DOCKER_CONTAINER:-}" ]; then
    # Dans un conteneur Docker
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" << 'SQL'
-- Créer la table showcase_admin si elle n'existe pas
CREATE TABLE IF NOT EXISTS showcase_admin (
    user_id TEXT NOT NULL PRIMARY KEY,
    CONSTRAINT showcase_admin_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES "user"(id) ON DELETE CASCADE
);

-- Créer la table showcase si elle n'existe pas
CREATE TABLE IF NOT EXISTS showcase (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    title TEXT,
    notes TEXT,
    image_url TEXT,
    created TIMESTAMP,
    modified TIMESTAMP,
    author TEXT,
    author_email TEXT,
    maintainer TEXT,
    maintainer_email TEXT,
    url TEXT,
    tags TEXT,
    license_id TEXT,
    license_title TEXT,
    license_url TEXT,
    extras TEXT
);

-- Vérifier que les tables existent
SELECT 'showcase_admin' as table_name, COUNT(*) as row_count FROM showcase_admin
UNION ALL
SELECT 'showcase' as table_name, COUNT(*) as row_count FROM showcase;
SQL
else
    # Localement, utiliser docker compose exec
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" << 'SQL'
-- Créer la table showcase_admin si elle n'existe pas
CREATE TABLE IF NOT EXISTS showcase_admin (
    user_id TEXT NOT NULL PRIMARY KEY,
    CONSTRAINT showcase_admin_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES "user"(id) ON DELETE CASCADE
);

-- Créer la table showcase si elle n'existe pas
CREATE TABLE IF NOT EXISTS showcase (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    title TEXT,
    notes TEXT,
    image_url TEXT,
    created TIMESTAMP,
    modified TIMESTAMP,
    author TEXT,
    author_email TEXT,
    maintainer TEXT,
    maintainer_email TEXT,
    url TEXT,
    tags TEXT,
    license_id TEXT,
    license_title TEXT,
    license_url TEXT,
    extras TEXT
);

-- Vérifier que les tables existent
SELECT 'showcase_admin' as table_name, COUNT(*) as row_count FROM showcase_admin
UNION ALL
SELECT 'showcase' as table_name, COUNT(*) as row_count FROM showcase;
SQL
fi

echo "Tables showcase créées/vérifiées"












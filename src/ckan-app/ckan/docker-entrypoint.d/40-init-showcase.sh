#!/bin/bash
# Script pour initialiser les tables de l'extension showcase.
# Crée toujours showcase_admin et showcase (CREATE TABLE IF NOT EXISTS) quand
# l'extension est activée, car "ckan db upgrade -p showcase" peut ne pas les créer.

set -euo pipefail

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"
echo "Initialisation de l'extension showcase..."

# Vérifier si l'extension showcase est activée (ckan.plugins contient "showcase")
if ! ckan -c "$CKAN_INI" config-tool 2>/dev/null | grep -q "showcase"; then
    echo "Extension showcase non activée, initialisation ignorée"
    exit 0
fi

# 1) Tenter les migrations officielles
echo "Exécution des migrations showcase..."
ckan -c "$CKAN_INI" db upgrade -p showcase 2>&1 || true

# 2) Toujours créer les tables si absentes (idempotent). Certaines versions
#    de ckanext-showcase n'ont pas de migration pour showcase_admin.
echo "Vérification/création des tables showcase_admin et showcase..."
python3 << 'PYTHON'
import os
import sys

def get_conn():
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    host = os.getenv('POSTGRES_HOST', 'db')
    port = int(os.getenv('POSTGRES_PORT', '5432'))
    user = os.getenv('POSTGRES_USER', 'ckan')
    password = os.getenv('POSTGRES_PASSWORD', 'ckan')
    database = os.getenv('POSTGRES_DB', os.getenv('CKAN_DB', 'ckan'))
    conn = psycopg2.connect(
        host=host, port=port, user=user, password=password, dbname=database
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn

try:
    conn = get_conn()
    cur = conn.cursor()
    # showcase_admin : requise pour /ckan-admin/showcase_admins
    cur.execute("""
        CREATE TABLE IF NOT EXISTS showcase_admin (
            user_id TEXT NOT NULL PRIMARY KEY,
            CONSTRAINT showcase_admin_user_id_fkey FOREIGN KEY (user_id)
                REFERENCES "user"(id) ON DELETE CASCADE
        );
    """)
    print("Table showcase_admin créée/vérifiée")
    # showcase : table principale des réutilisations
    cur.execute("""
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
    """)
    print("Table showcase créée/vérifiée")
    cur.close()
    conn.close()
except Exception as e:
    print("Erreur création tables showcase:", e, file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
PYTHON

echo "Initialisation showcase terminée"


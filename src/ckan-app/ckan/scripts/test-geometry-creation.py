#!/usr/bin/env python3
"""
Script de test pour créer la colonne geometry depuis les colonnes GeoJSON
"""

import os
import sys
import logging
import requests
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration
CKAN_URL = os.getenv('CKAN_URL', 'http://ckan:5000')
CKAN_API_KEY = os.getenv('CKAN_API_KEY', '')
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'db')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.getenv('POSTGRES_DB', 'datastore')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'ckan')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'ckan')

RESOURCE_ID = 'c19fa9f3-d125-40c3-ac1d-d76eb119660c'

def check_postgis():
    """Vérifier PostGIS"""
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        cur.execute("SELECT PostGIS_version();")
        version = cur.fetchone()[0]
        logger.info(f"PostGIS activé: {version}")
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"PostGIS non disponible: {e}")
        return False

def get_resource_fields():
    """Récupérer les champs de la ressource"""
    try:
        url = f"{CKAN_URL}/api/action/datastore_search"
        params = {'resource_id': RESOURCE_ID, 'limit': 0}
        headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Erreur API: {response.status_code}")
            return None
        
        result = response.json()
        if not result.get('success'):
            logger.error(f"Erreur API: {result.get('error')}")
            return None
        
        fields = result.get('result', {}).get('fields', [])
        logger.info(f"{len(fields)} colonnes trouvées")
        
        geojson_fields = []
        for field in fields:
            field_name = field.get('id', '').lower()
            field_type = field.get('type', '').lower()
            logger.info(f"   - {field.get('id')}: {field_type}")
            
            if any(kw in field_name for kw in ['geo_point', 'st_asgeojson', 'geojson']):
                if 'text' in field_type:
                    geojson_fields.append(field.get('id'))
                    logger.info(f"      Colonne GeoJSON détectée: {field.get('id')}")
        
        return geojson_fields
        
    except Exception as e:
        logger.error(f"Erreur récupération champs: {e}")
        return None

def check_geometry_column():
    """Vérifier si la colonne geometry existe"""
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        
        table_name = RESOURCE_ID
        cur.execute(f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = %s
            AND column_name = 'geometry';
        """, (table_name,))
        
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            logger.info(f"Colonne geometry existe: {result[0]} ({result[1]})")
            return True
        else:
            logger.warning(f"Colonne geometry n'existe pas")
            return False
            
    except Exception as e:
        logger.error(f"Erreur vérification colonne: {e}")
        return False

def create_geometry_from_geojson(geojson_field: str):
    """Créer la colonne geometry depuis une colonne GeoJSON"""
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        
        table_name = RESOURCE_ID
        geojson_field_escaped = geojson_field.replace('"', '\\"')
        
        # Créer la colonne
        logger.info(f"Création de la colonne geometry...")
        cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS geometry geometry(Geometry, 4326);')
        logger.info(f"Colonne geometry créée")
        
        # Vérifier quelques exemples de valeurs
        logger.info(f"Analyse des valeurs dans '{geojson_field}'...")
        cur.execute(f'SELECT "{geojson_field}" FROM "{table_name}" WHERE "{geojson_field}" IS NOT NULL AND "{geojson_field}" != \'\' LIMIT 5;')
        samples = cur.fetchall()
        logger.info(f"   Exemples de valeurs:")
        for i, sample in enumerate(samples, 1):
            value = sample[0] if sample else None
            if value:
                preview = str(value)[:100] + "..." if len(str(value)) > 100 else str(value)
                logger.info(f"   {i}. {preview}")
        
        # Essayer de créer les géométries
        logger.info(f"Création des géométries depuis '{geojson_field}'...")
        
        # Format 1: "lon,lat"
        update_query_1 = f'''
            UPDATE "{table_name}" 
            SET geometry = ST_SetSRID(ST_MakePoint(
                CAST(SPLIT_PART("{geojson_field_escaped}", ',', 1) AS FLOAT),
                CAST(SPLIT_PART("{geojson_field_escaped}", ',', 2) AS FLOAT)
            ), 4326)
            WHERE "{geojson_field_escaped}" ~ '^[0-9.-]+,[0-9.-]+$'
              AND geometry IS NULL;
        '''
        
        try:
            cur.execute(update_query_1)
            rows_1 = cur.rowcount
            logger.info(f"Format 'lon,lat': {rows_1} géométries créées")
        except Exception as e:
            logger.warning(f"Format 'lon,lat' échoué: {e}")
        
        # Format 2: GeoJSON valide
        update_query_2 = f'''
            UPDATE "{table_name}" 
            SET geometry = ST_SetSRID(ST_GeomFromGeoJSON("{geojson_field_escaped}"), 4326)
            WHERE "{geojson_field_escaped}" IS NOT NULL 
              AND "{geojson_field_escaped}" != ''
              AND "{geojson_field_escaped}" != 'null'
              AND geometry IS NULL
              AND ST_GeomFromGeoJSON("{geojson_field_escaped}") IS NOT NULL;
        '''
        
        try:
            cur.execute(update_query_2)
            rows_2 = cur.rowcount
            logger.info(f"Format GeoJSON: {rows_2} géométries créées")
        except Exception as e:
            logger.warning(f"Format GeoJSON échoué: {e}")
        
        # Vérifier le résultat
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE geometry IS NOT NULL;')
        geometry_count = cur.fetchone()[0]
        
        if geometry_count > 0:
            logger.info(f"Total: {geometry_count} géométries créées")
            
            # Créer l'index spatial
            index_name = f'idx_{table_name.replace("-", "_")}_geometry'
            cur.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table_name}" USING GIST (geometry);')
            logger.info(f"Index spatial créé")
            
            conn.commit()
            cur.close()
            conn.close()
            return True
        else:
            logger.warning(f"Aucune géométrie créée")
            conn.rollback()
            cur.close()
            conn.close()
            return False
            
    except Exception as e:
        logger.error(f"Erreur création geometry: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    logger.info("=" * 80)
    logger.info("TEST DE CRÉATION DE LA COLONNE GEOMETRY")
    logger.info(f"Ressource: {RESOURCE_ID}")
    logger.info("=" * 80)
    
    # Étape 1: Vérifier PostGIS
    logger.info("\nÉtape 1: Vérification PostGIS")
    if not check_postgis():
        sys.exit(1)
    
    # Étape 2: Vérifier la colonne geometry
    logger.info("\nÉtape 2: Vérification colonne geometry")
    has_geometry = check_geometry_column()
    
    # Étape 3: Récupérer les colonnes GeoJSON
    logger.info("\nÉtape 3: Analyse des colonnes")
    geojson_fields = get_resource_fields()
    
    if not geojson_fields:
        logger.error("Aucune colonne GeoJSON trouvée")
        sys.exit(1)
    
    # Étape 4: Créer la colonne geometry si nécessaire
    if not has_geometry:
        logger.info("\nÉtape 4: Création de la colonne geometry")
        # Utiliser la première colonne GeoJSON trouvée
        geojson_field = geojson_fields[0]
        logger.info(f"   Utilisation de la colonne: {geojson_field}")
        
        if create_geometry_from_geojson(geojson_field):
            logger.info("\nColonne geometry créée avec succès!")
            
            # Vérifier à nouveau
            if check_geometry_column():
                logger.info("Vérification: colonne geometry confirmée")
        else:
            logger.error("\nÉchec de la création de la colonne geometry")
            sys.exit(1)
    else:
        logger.info("\nColonne geometry existe déjà")
    
    logger.info("\n" + "=" * 80)
    logger.info("TEST TERMINÉ")
    logger.info("=" * 80)

if __name__ == '__main__':
    main()












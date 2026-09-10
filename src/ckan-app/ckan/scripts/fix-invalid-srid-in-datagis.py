#!/usr/bin/env python3
"""
Script pour corriger les SRID invalides dans les tables datagis
Remplace les SRID invalides (comme 900914) par 4326 (WGS84)
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor

# Configuration de la base de données datagis
DATAGIS_CONFIG = {
    'host': 'db',
    'port': 5432,
    'dbname': 'datagis',
    'user': 'ckan',
    'password': 'ckan'
}

# SRID invalides connus
INVALID_SRIDS = [900914, 999999, 0, -1]

# SRID par défaut à utiliser
DEFAULT_SRID = 4326  # WGS84

def validate_srid(srid):
    """Valide qu'un SRID est un code EPSG valide"""
    if srid is None:
        return False
    
    # Codes EPSG valides sont généralement entre 2000 et 99999
    if srid < 2000 or srid > 99999:
        return False
    
    # Codes EPSG invalides connus
    if srid in INVALID_SRIDS:
        return False
    
    return True

def fix_table_srid(conn, table_name, geom_column, old_srid, new_srid):
    """Corrige le SRID d'une table en mettant à jour geometry_columns et les géométries"""
    cur = conn.cursor()
    
    try:
        print(f"   Correction du SRID pour {table_name}.{geom_column}: {old_srid} → {new_srid}")
        
        # 1. Mettre à jour geometry_columns
        cur.execute("""
            UPDATE geometry_columns
            SET srid = %s
            WHERE f_table_schema = 'public'
            AND f_table_name = %s
            AND f_geometry_column = %s;
        """, (new_srid, table_name, geom_column))
        
        # 2. Transformer les géométries vers le nouveau SRID
        # Note: ST_Transform nécessite que les deux SRID soient valides
        # Si l'ancien SRID est invalide, on ne peut pas transformer
        # On va simplement mettre à jour le SRID dans geometry_columns
        # Les géométries seront réinterprétées avec le nouveau SRID
        
        # Vérifier si des géométries existent
        cur.execute(f"""
            SELECT COUNT(*) 
            FROM "{table_name}"
            WHERE "{geom_column}" IS NOT NULL;
        """)
        count = cur.fetchone()[0]
        
        if count > 0:
            print(f"       {count} géométrie(s) trouvée(s) - Le SRID sera mis à jour dans geometry_columns")
            print(f"       Les géométries existantes ne seront PAS transformées (SRID source invalide)")
            print(f"      Pour une transformation correcte, réimporter les données avec le bon SRID")
        
        conn.commit()
        print(f"      SRID corrigé dans geometry_columns")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"      Erreur lors de la correction: {e}")
        return False
    finally:
        cur.close()

def main():
    """Fonction principale"""
    print("=" * 80)
    print("CORRECTION DES SRID INVALIDES DANS DATAGIS")
    print("=" * 80)
    print()
    
    # Connexion à la base de données
    try:
        conn = psycopg2.connect(**DATAGIS_CONFIG)
        print("Connexion à la base datagis réussie")
    except Exception as e:
        print(f"Erreur de connexion à la base datagis: {e}")
        sys.exit(1)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Trouver toutes les tables avec des SRID invalides
        cur.execute("""
            SELECT f_table_name, f_geometry_column, srid
            FROM geometry_columns
            WHERE f_table_schema = 'public'
            ORDER BY f_table_name;
        """)
        
        tables = cur.fetchall()
        print(f"{len(tables)} table(s) trouvée(s) dans geometry_columns")
        print()
        
        invalid_tables = []
        for table in tables:
            table_name = table['f_table_name']
            geom_column = table['f_geometry_column']
            srid = table['srid']
            
            if not validate_srid(srid):
                invalid_tables.append({
                    'table_name': table_name,
                    'geom_column': geom_column,
                    'srid': srid
                })
        
        if not invalid_tables:
            print("Aucune table avec SRID invalide trouvée")
            return
        
        print(f" {len(invalid_tables)} table(s) avec SRID invalide trouvée(s):")
        print()
        
        for table in invalid_tables:
            print(f"   {table['table_name']}.{table['geom_column']}: SRID={table['srid']} (INVALIDE)")
        
        print()
        
        # Demander confirmation
        if len(sys.argv) > 1 and sys.argv[1] == '--fix':
            fix = True
            print("Mode correction activé (--fix)")
        else:
            response = input(f"Voulez-vous corriger ces {len(invalid_tables)} table(s) ? (oui/non): ")
            fix = response.lower() in ['oui', 'o', 'yes', 'y']
        
        if not fix:
            print("Correction annulée")
            return
        
        print()
        print("=" * 80)
        print("CORRECTION EN COURS...")
        print("=" * 80)
        print()
        
        fixed_count = 0
        for table in invalid_tables:
            success = fix_table_srid(
                conn,
                table['table_name'],
                table['geom_column'],
                table['srid'],
                DEFAULT_SRID
            )
            if success:
                fixed_count += 1
            print()
        
        print("=" * 80)
        print(f"Correction terminée: {fixed_count}/{len(invalid_tables)} table(s) corrigée(s)")
        print("=" * 80)
        print()
        print("Pour régénérer les mapfiles avec les nouveaux SRID:")
        print("   python3 /srv/app/ckanext-ogc/scripts/generate-mapfile.py --all")
        print()
        
    except Exception as e:
        print(f"Erreur: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == '__main__':
    main()

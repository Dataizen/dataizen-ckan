#!/usr/bin/env python3
"""
Script de correction pour initialiser alembic_version quand la base 
a déjà été migrée mais que la table alembic_version n'existe pas.

Ce script vérifie l'état actuel de la base et marque les migrations 
appropriées comme exécutées.
"""

import psycopg2
import sys
import os
import shutil

def get_db_connection():
    """Récupère la connexion à la base de données depuis les variables d'environnement"""
    conn_str = os.environ.get('CKAN_SQLALCHEMY_URL', '')
    if not conn_str:
        # Fallback pour développement local
        conn_str = 'postgresql://ckan:ckan@db:5432/ckan'
    
    return psycopg2.connect(conn_str)

def check_column_exists(cursor, table, column):
    """Vérifie si une colonne existe dans une table"""
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = %s AND column_name = %s
        )
    """, (table, column))
    return cursor.fetchone()[0]

def check_table_exists(cursor, table):
    """Vérifie si une table existe"""
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.tables 
            WHERE table_name = %s
        )
    """, (table,))
    return cursor.fetchone()[0]

def patch_migration_generic(migration_file, table_name, column_name, update_sql=None):
    """
    Patche génériquement une migration pour qu'elle soit idempotente
    (vérifie si la colonne existe avant de l'ajouter)
    
    Args:
        migration_file: Chemin vers le fichier de migration
        table_name: Nom de la table
        column_name: Nom de la colonne à ajouter
        update_sql: SQL optionnel à exécuter après l'ajout de la colonne
    """
    try:
        with open(migration_file, 'r') as f:
            content = f.read()
        
        # Vérifier si le patch est déjà appliqué
        if 'information_schema.columns' in content and 'column_exists' in content:
            return False
        
        # Extraire la fonction upgrade originale
        import re
        upgrade_match = re.search(r'def upgrade\(\):(.*?)def downgrade\(\):', content, re.DOTALL)
        if not upgrade_match:
            return False
        
        original_upgrade = upgrade_match.group(1)
        
        # Indenter correctement le code original
        indented_upgrade = '\n'.join('        ' + line if line.strip() else line 
                                     for line in original_upgrade.split('\n'))
        
        # Créer une version patchée qui vérifie l'existence de la colonne avec SQL
        patched_upgrade = f'''def upgrade():
    # Vérifier si la colonne existe déjà (pour éviter l'erreur DuplicateColumn)
    conn = op.get_bind()
    result = conn.execute(u"""
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}' AND column_name = '{column_name}'
        )
    """)
    column_exists = result.fetchone()[0]
    
    if not column_exists:
        # Colonne n'existe pas, exécuter la migration originale
{indented_upgrade}
'''
        
        # Ajouter le SQL de mise à jour si fourni
        if update_sql:
            patched_upgrade += f'''    else:
        # Colonne existe déjà, exécuter la mise à jour si nécessaire
        op.execute(u"{update_sql}")
'''
        
        # Remplacer la fonction upgrade
        pattern = r'def upgrade\(\):.*?def downgrade\(\):'
        replacement = patched_upgrade + '\n\ndef downgrade():'
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        # Sauvegarder le fichier original et créer le patch
        backup_file = migration_file + '.backup'
        if not os.path.exists(backup_file):
            shutil.copy2(migration_file, backup_file)
        
        with open(migration_file, 'w') as f:
            f.write(new_content)
        
        return True
        
    except Exception as e:
        print(f"Impossible de patcher {migration_file}: {e}")
        return False

def patch_migration_094():
    """Patche la migration 094 pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/094_588d7cfb9a41_add_metadata_modified_to_resource_table.py'
    return patch_migration_generic(
        migration_file, 
        'resource', 
        'metadata_modified',
        update_sql="UPDATE resource SET metadata_modified = created WHERE metadata_modified IS NULL"
    )

def patch_migration_096():
    """Patche la migration 096 pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/096_19ddad52b500_add_plugin_extras_to_user_table.py'
    return patch_migration_generic(
        migration_file,
        'user',
        'plugin_extras'
    )

def patch_migration_098():
    """Patche la migration 098 pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/098_ddbd0a9a4489_add_image_url_field_to_user_table.py'
    return patch_migration_generic(
        migration_file,
        'user',
        'image_url'
    )

def patch_migration_101():
    """Patche la migration 101 pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/101_d111f446733b_add_last_active_column_in_user_table.py'
    return patch_migration_generic(
        migration_file,
        'user',
        'last_active'
    )

def patch_migration_103():
    """Patche la migration 103 pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/103_353aaf2701f0_add_plugin_data_to_package_table.py'
    return patch_migration_generic(
        migration_file,
        'package',
        'plugin_data'
    )

def patch_activity_migration_71713a055d5c():
    """Patche la migration activity 71713a055d5c pour qu'elle soit idempotente"""
    migration_file = '/srv/app/src/ckan/ckanext/activity/migration/activity/versions/71713a055d5c_add_permission_labels_in_activity_table.py'
    return patch_migration_generic(
        migration_file,
        'activity',
        'permission_labels'
    )

def patch_migration_104():
    """Patche la migration 104 pour qu'elle soit idempotente (création d'index)"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/104_9f33a0280c51_resource_view_resource_id_index.py'
    try:
        with open(migration_file, 'r') as f:
            content = f.read()
        
        # Vérifier si le patch est déjà appliqué
        if 'pg_indexes' in content or 'index_exists' in content:
            return False
        
        import re
        
        # Pattern pour trouver op.create_index avec le nom de l'index
        # Format: op.create_index(u'idx_view_resource_id', u'resource_view', [u'resource_id'])
        pattern = r"op\.create_index\s*\(\s*u'([^']+)'\s*,\s*u'([^']+)'\s*,\s*\[([^\]]+)\]\s*\)"
        
        def replace_create_index(match):
            index_name = match.group(1)
            table_name = match.group(2)
            columns = match.group(3)
            
            return f'''# Vérifier si l'index existe déjà
    conn = op.get_bind()
    result = conn.execute(u"""
        SELECT EXISTS (
            SELECT 1 
            FROM pg_indexes 
            WHERE indexname = '{index_name}'
        )
    """)
    index_exists = result.fetchone()[0]
    
    if not index_exists:
        op.create_index(u'{index_name}', u'{table_name}', [{columns}])
'''
        
        new_content = re.sub(pattern, replace_create_index, content)
        
        # Sauvegarder le fichier original et créer le patch
        backup_file = migration_file + '.backup'
        if not os.path.exists(backup_file):
            shutil.copy2(migration_file, backup_file)
        
        with open(migration_file, 'w') as f:
            f.write(new_content)
        
        return True
        
    except Exception as e:
        print(f"Impossible de patcher {migration_file}: {e}")
        import traceback
        traceback.print_exc()
        return False

def patch_migration_105():
    """Patche la migration 105 pour qu'elle soit idempotente (suppression d'index/tables)"""
    migration_file = '/srv/app/src/ckan/ckan/migration/versions/105_4a5e3465beb6_autogenerate_sync.py'
    try:
        with open(migration_file, 'r') as f:
            content = f.read()
        
        # Vérifier si le patch est déjà appliqué
        if 'pg_indexes' in content or 'pg_tables' in content:
            return False
        
        import re
        
        # Remplacer op.drop_index par une version qui vérifie d'abord
        def replace_drop_index(match):
            index_name = match.group(1)
            table_name = match.group(2) if len(match.groups()) > 1 else None
            
            check_sql = f"SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = '{index_name}')"
            if table_name:
                check_sql = f"SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = '{index_name}' AND tablename = '{table_name}')"
            
            return f'''# Vérifier si l'index existe avant de le supprimer
    conn = op.get_bind()
    result = conn.execute(u"""{check_sql}""")
    index_exists = result.fetchone()[0]
    
    if index_exists:
        op.drop_index('{index_name}'{f", table_name='{table_name}'" if table_name else ""})
'''
        
        # Pattern pour op.drop_index
        pattern1 = r"op\.drop_index\s*\(\s*u'([^']+)'\s*,\s*table_name\s*=\s*u'([^']+)'\s*\)"
        pattern2 = r"op\.drop_index\s*\(\s*u'([^']+)'\s*\)"
        
        content = re.sub(pattern1, replace_drop_index, content)
        content = re.sub(pattern2, replace_drop_index, content)
        
        # Remplacer op.drop_table par une version qui vérifie d'abord
        def replace_drop_table(match):
            table_name = match.group(1)
            
            return f'''# Vérifier si la table existe avant de la supprimer
    conn = op.get_bind()
    result = conn.execute(u"""
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.tables 
            WHERE table_name = '{table_name}'
        )
    """)
    table_exists = result.fetchone()[0]
    
    if table_exists:
        op.drop_table('{table_name}')
'''
        
        pattern_table = r"op\.drop_table\s*\(\s*u'([^']+)'\s*\)"
        content = re.sub(pattern_table, replace_drop_table, content)
        
        # Sauvegarder le fichier original et créer le patch
        backup_file = migration_file + '.backup'
        if not os.path.exists(backup_file):
            shutil.copy2(migration_file, backup_file)
        
        with open(migration_file, 'w') as f:
            f.write(content)
        
        return True
        
    except Exception as e:
        print(f"Impossible de patcher {migration_file}: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_and_patch_migrations(cursor):
    """
    Vérifie et patche toutes les migrations problématiques
    Retourne la liste des migrations qui ont été patchées ou qui doivent être marquées comme exécutées
    """
    patched_migrations = []
    
    # Migration 094 - ajoute metadata_modified à resource
    if check_column_exists(cursor, 'resource', 'metadata_modified'):
        print("Colonne metadata_modified existe déjà dans resource")
        patch_migration_094()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('588d7cfb9a41')
    
    # Migration 096 - ajoute plugin_extras à user
    if check_column_exists(cursor, 'user', 'plugin_extras'):
        print("Colonne plugin_extras existe déjà dans user")
        patch_migration_096()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('19ddad52b500')
    
    # Migration 098 - ajoute image_url à user
    if check_column_exists(cursor, 'user', 'image_url'):
        print("Colonne image_url existe déjà dans user")
        patch_migration_098()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('ddbd0a9a4489')
    
    # Migration 101 - ajoute last_active à user
    if check_column_exists(cursor, 'user', 'last_active'):
        print("Colonne last_active existe déjà dans user")
        patch_migration_101()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('d111f446733b')
    
    # Migration 103 - ajoute plugin_data à package
    if check_column_exists(cursor, 'package', 'plugin_data'):
        print("Colonne plugin_data existe déjà dans package")
        patch_migration_103()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('353aaf2701f0')
    
    # Migration 104 - crée un index (peut déjà exister)
    # Vérifier si l'index existe déjà
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 
            FROM pg_indexes 
            WHERE indexname = 'idx_view_resource_id'
        )
    """)
    if cursor.fetchone()[0]:
        print("Index idx_view_resource_id existe déjà")
        patch_migration_104()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('9f33a0280c51')
    
    # Migration 105 - supprime des index/tables (peut ne pas exister)
    # Vérifier si la table rating existe (elle devrait être supprimée)
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.tables 
            WHERE table_name = 'rating'
        )
    """)
    rating_exists = cursor.fetchone()[0]
    if not rating_exists:
        print("Table rating n'existe pas (déjà supprimée)")
        patch_migration_105()  # Patch même si déjà patché (idempotent)
        patched_migrations.append('4a5e3465beb6')
    
    # Migration activity 71713a055d5c - ajoute permission_labels à activity
    if check_column_exists(cursor, 'activity', 'permission_labels'):
        print("Colonne permission_labels existe déjà dans activity")
        patch_activity_migration_71713a055d5c()  # Patch même si déjà patché (idempotent)
        # Note: Cette migration est de l'extension activity, pas de CKAN core
        # On ne l'ajoute pas à patched_migrations car elle est gérée séparément
    
    return patched_migrations

def determine_current_version(cursor):
    """
    Détermine la version actuelle de la base en vérifiant 
    quelles migrations ont déjà été appliquées (colonnes/tables existantes)
    """
    # Vérifier et patcher toutes les migrations problématiques
    patched_migrations = check_and_patch_migrations(cursor)
    
    # Retourner la migration la plus récente parmi celles patchées
    # (ordre approximatif : 094 < 096 < 098 < 101 < 103 < 104 < 105)
    if '4a5e3465beb6' in patched_migrations:
        return '4a5e3465beb6'
    elif '9f33a0280c51' in patched_migrations:
        return '9f33a0280c51'
    elif '353aaf2701f0' in patched_migrations:
        return '353aaf2701f0'
    elif 'd111f446733b' in patched_migrations:
        return 'd111f446733b'
    elif 'ddbd0a9a4489' in patched_migrations:
        return 'ddbd0a9a4489'
    elif '19ddad52b500' in patched_migrations:
        return '19ddad52b500'
    elif '588d7cfb9a41' in patched_migrations:
        return '588d7cfb9a41'
    
    # Si aucune colonne spécifique n'est trouvée, 
    # la base est vide ou très ancienne - on laisse CKAN gérer
    return None

def main():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Vérifier si la table alembic_version existe
        if not check_table_exists(cursor, 'alembic_version'):
            print("Table alembic_version n'existe pas, création...")
            
            # Créer la table alembic_version
            cursor.execute("""
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """)
            print("Table alembic_version créée")
            
            # Déterminer la version actuelle
            current_version = determine_current_version(cursor)
            
            if current_version:
                # Marquer la migration comme exécutée
                cursor.execute("""
                    INSERT INTO alembic_version (version_num) 
                    VALUES (%s)
                """, (current_version,))
                print(f"Migration {current_version} marquée comme exécutée")
                print(f"CKAN continuera avec les migrations suivantes si nécessaire")
            else:
                print("Base vide ou état inconnu - CKAN gérera les migrations depuis le début")
        else:
            print("Table alembic_version existe déjà")
            
            # Vérifier si elle contient des données
            cursor.execute("SELECT COUNT(*) FROM alembic_version")
            count = cursor.fetchone()[0]
            if count == 0:
                print("Table alembic_version vide")
                current_version = determine_current_version(cursor)
                if current_version:
                    cursor.execute("""
                        INSERT INTO alembic_version (version_num) 
                        VALUES (%s)
                    """, (current_version,))
                    print(f"Migration {current_version} marquée comme exécutée")
            else:
                # Vérifier si la valeur est incorrecte (avec préfixe 094_ ou 096_)
                cursor.execute("SELECT version_num FROM alembic_version")
                existing_version = cursor.fetchone()[0] if cursor.rowcount > 0 else None
                if existing_version and (existing_version.startswith('094_') or existing_version.startswith('096_')):
                    print(f"Version incorrecte trouvée: {existing_version}")
                    # Corriger la version
                    correct_version = existing_version.replace('094_', '').replace('096_', '')
                    cursor.execute("""
                        UPDATE alembic_version 
                        SET version_num = %s 
                        WHERE version_num = %s
                    """, (correct_version, existing_version))
                    print(f"Version corrigée: {correct_version}")
                    conn.commit()
                    return 0
                elif existing_version:
                    print(f"Version actuelle dans alembic_version: {existing_version}")
                    
                    # Vérifier si les migrations problématiques doivent être marquées comme exécutées
                    patched_migrations = check_and_patch_migrations(cursor)
                    
                    # Vérifier si les migrations doivent être ajoutées (dans l'ordre)
                    # On garde seulement la dernière migration dans alembic_version
                    if patched_migrations:
                        # Supprimer toutes les versions existantes et ajouter la plus récente
                        cursor.execute("DELETE FROM alembic_version")
                        latest = patched_migrations[-1]  # La dernière dans la liste
                        cursor.execute("""
                            INSERT INTO alembic_version (version_num) 
                            VALUES (%s)
                        """, (latest,))
                        print(f"Migration {latest} marquée comme exécutée (dernière migration patchée)")
        
        conn.commit()
        print("Correction terminée avec succès")
        return 0
        
    except Exception as e:
        print(f"Erreur: {e}")
        import traceback
        traceback.print_exc()
        if 'conn' in locals():
            conn.rollback()
        return 1
    finally:
        if 'conn' in locals():
            cursor.close()
            conn.close()

if __name__ == '__main__':
    sys.exit(main())


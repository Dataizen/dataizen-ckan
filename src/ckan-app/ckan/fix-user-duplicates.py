#!/usr/bin/env python3
"""
Script de nettoyage des doublons dans la table user
pour permettre la création de l'index unique idx_only_one_active_email

Ce script identifie les utilisateurs avec le même email et state='active',
garde le plus récent (ou celui avec le plus d'activité) et désactive les autres.
"""

import psycopg2
import sys
import os
from datetime import datetime

def get_db_connection():
    """Récupère la connexion à la base de données depuis les variables d'environnement"""
    conn_str = os.environ.get('CKAN_SQLALCHEMY_URL', '')
    if not conn_str:
        # Fallback pour développement local
        conn_str = 'postgresql://ckan:ckan@db:5432/ckan'
    
    return psycopg2.connect(conn_str)

def find_duplicate_active_users(cursor):
    """
    Trouve les utilisateurs avec le même email et state='active'
    
    Returns:
        List of tuples: (email, count, user_ids)
    """
    cursor.execute("""
        SELECT email, COUNT(*) as count, array_agg(id ORDER BY created DESC) as user_ids
        FROM "user"
        WHERE email IS NOT NULL 
          AND email != ''
          AND state = 'active'
        GROUP BY email
        HAVING COUNT(*) > 1
        ORDER BY count DESC
    """)
    return cursor.fetchall()

def get_user_details(cursor, user_id):
    """Récupère les détails d'un utilisateur"""
    cursor.execute("""
        SELECT id, name, email, state, created
        FROM "user"
        WHERE id = %s
    """, (user_id,))
    return cursor.fetchone()

def choose_keep_user(cursor, user_ids):
    """
    Choisit quel utilisateur garder parmi une liste d'IDs
    
    Stratégie:
    1. Garder le plus récent (created le plus récent)
    2. Sinon, garder le premier dans l'ordre alphabétique (name)
    """
    users = []
    for user_id in user_ids:
        user = get_user_details(cursor, user_id)
        if user:
            users.append({
                'id': user[0],
                'name': user[1],
                'email': user[2],
                'state': user[3],
                'created': user[4] if user[4] else datetime.min
            })
    
    if not users:
        return None
    
    # Trier par date de création (plus récent en premier), puis par nom
    users.sort(key=lambda x: (
        x['created'] if x['created'] else datetime.min,
        x['name'] or ''
    ), reverse=True)
    
    return users[0]['id']

def deactivate_user(cursor, user_id, reason):
    """Désactive un utilisateur"""
    cursor.execute("""
        UPDATE "user"
        SET state = 'deleted',
            email = email || '_deleted_' || %s
        WHERE id = %s
    """, (int(datetime.now().timestamp()), user_id))
    print(f"  Utilisateur {user_id} désactivé: {reason}")

def main():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print("Recherche des doublons dans la table user...")
        duplicates = find_duplicate_active_users(cursor)
        
        if not duplicates:
            print("Aucun doublon trouvé")
            return 0
        
        print(f"Trouvé {len(duplicates)} emails avec plusieurs utilisateurs actifs")
        
        total_fixed = 0
        for email, count, user_ids in duplicates:
            print(f"\nEmail: {email} ({count} utilisateurs actifs)")
            
            # Choisir quel utilisateur garder
            keep_id = choose_keep_user(cursor, user_ids)
            
            if not keep_id:
                print(f"  Impossible de déterminer quel utilisateur garder")
                continue
            
            keep_user = get_user_details(cursor, keep_id)
            print(f"  Garde: {keep_user[1]} (id={keep_id}, créé={keep_user[4]})")
            
            # Désactiver les autres
            for user_id in user_ids:
                if user_id != keep_id:
                    user = get_user_details(cursor, user_id)
                    reason = f"Doublon de {email}, garde {keep_user[1]} (id={keep_id})"
                    deactivate_user(cursor, user_id, reason)
                    total_fixed += 1
        
        # Vérifier qu'il n'y a plus de doublons
        print("\nVérification finale...")
        remaining_duplicates = find_duplicate_active_users(cursor)
        
        if remaining_duplicates:
            print(f"Il reste {len(remaining_duplicates)} doublons après nettoyage")
            for email, count, user_ids in remaining_duplicates:
                print(f"  - {email}: {count} utilisateurs actifs")
            return 1
        else:
            print(f"Nettoyage terminé: {total_fixed} utilisateurs désactivés")
            conn.commit()
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


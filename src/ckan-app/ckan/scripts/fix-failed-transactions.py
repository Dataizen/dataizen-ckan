#!/usr/bin/env python3
"""
Script pour diagnostiquer et corriger les transactions PostgreSQL en échec

Ce script vérifie et corrige les connexions PostgreSQL qui sont dans un état
de transaction abortée, ce qui cause l'erreur "current transaction is aborted".
"""

import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration depuis les variables d'environnement
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'db')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.getenv('POSTGRES_DB', 'ckan')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'ckan')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'ckan')


def check_and_fix_transactions():
    """
    Vérifie et corrige les transactions en échec
    """
    try:
        # Se connecter avec autocommit pour éviter les problèmes de transaction
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        logger.info("Vérification des transactions actives...")
        
        # Vérifier les transactions actives
        cursor.execute("""
            SELECT 
                pid,
                usename,
                application_name,
                state,
                query_start,
                state_change,
                wait_event_type,
                wait_event,
                query
            FROM pg_stat_activity
            WHERE datname = %s
              AND state != 'idle'
            ORDER BY query_start;
        """, (POSTGRES_DB,))
        
        active_transactions = cursor.fetchall()
        
        if active_transactions:
            logger.warning(f" {len(active_transactions)} transactions actives trouvées:")
            for trans in active_transactions:
                logger.warning(f"   PID: {trans[0]}, User: {trans[1]}, State: {trans[3]}")
                logger.warning(f"   Query: {trans[8][:100] if trans[8] else 'N/A'}...")
        else:
            logger.info("Aucune transaction active problématique trouvée")
        
        # Vérifier les verrous bloquants
        cursor.execute("""
            SELECT 
                blocked_locks.pid AS blocked_pid,
                blocked_activity.usename AS blocked_user,
                blocking_locks.pid AS blocking_pid,
                blocking_activity.usename AS blocking_user,
                blocked_activity.query AS blocked_statement,
                blocking_activity.query AS blocking_statement
            FROM pg_catalog.pg_locks blocked_locks
            JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
            JOIN pg_catalog.pg_locks blocking_locks 
                ON blocking_locks.locktype = blocked_locks.locktype
                AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                AND blocking_locks.pid != blocked_locks.pid
            JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
            WHERE NOT blocked_locks.granted;
        """)
        
        blocked_locks = cursor.fetchall()
        
        if blocked_locks:
            logger.warning(f" {len(blocked_locks)} verrous bloquants trouvés:")
            for lock in blocked_locks:
                logger.warning(f"   PID bloqué: {lock[0]}, PID bloquant: {lock[2]}")
        else:
            logger.info("Aucun verrou bloquant trouvé")
        
        # Vérifier que les tables critiques existent
        logger.info("Vérification des tables critiques...")
        
        critical_tables = ['user', 'package', 'resource', 'group', 'system_info', 'showcase_admin']
        missing_tables = []
        
        for table in critical_tables:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                );
            """, (table,))
            exists = cursor.fetchone()[0]
            
            if not exists:
                missing_tables.append(table)
                logger.error(f"Table manquante: {table}")
            else:
                logger.info(f"Table existe: {table}")
        
        if missing_tables:
            logger.error(f"Tables manquantes: {', '.join(missing_tables)}")
            logger.error("Exécutez les scripts de correction appropriés:")
            if 'showcase_admin' in missing_tables:
                logger.error("   /srv/app/scripts/fix-showcase-tables.sh")
            return False
        
        # Test de connexion simple
        logger.info("Test de connexion simple...")
        cursor.execute("SELECT 1;")
        result = cursor.fetchone()
        
        if result[0] == 1:
            logger.info("Connexion fonctionnelle")
        else:
            logger.error("Problème de connexion")
            return False
        
        cursor.close()
        conn.close()
        
        logger.info("Vérification terminée avec succès")
        return True
        
    except psycopg2.OperationalError as e:
        logger.error(f"Erreur de connexion PostgreSQL: {e}")
        return False
    except Exception as e:
        logger.error(f"Erreur inattendue: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def kill_idle_transactions():
    """
    Tue les transactions idle in transaction qui peuvent causer des problèmes
    """
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        logger.info("Recherche des transactions idle in transaction...")
        
        # Trouver les transactions idle in transaction de plus de 5 minutes
        cursor.execute("""
            SELECT pid, usename, application_name, state_change, query
            FROM pg_stat_activity
            WHERE datname = %s
              AND state = 'idle in transaction'
              AND state_change < NOW() - INTERVAL '5 minutes'
            ORDER BY state_change;
        """, (POSTGRES_DB,))
        
        idle_transactions = cursor.fetchall()
        
        if idle_transactions:
            logger.warning(f" {len(idle_transactions)} transactions idle in transaction trouvées:")
            killed_count = 0
            for trans in idle_transactions:
                pid = trans[0]
                logger.warning(f"   PID: {pid}, User: {trans[1]}, State change: {trans[3]}")
                try:
                    cursor.execute(f"SELECT pg_terminate_backend({pid});")
                    result = cursor.fetchone()
                    if result[0]:
                        logger.info(f"Transaction {pid} terminée")
                        killed_count += 1
                    else:
                        logger.warning(f" Impossible de terminer la transaction {pid}")
                except Exception as e:
                    logger.error(f"Erreur lors de la terminaison de {pid}: {e}")
            
            logger.info(f"{killed_count}/{len(idle_transactions)} transactions terminées")
        else:
            logger.info("Aucune transaction idle in transaction problématique trouvée")
        
        cursor.close()
        conn.close()
        
        return True
        
    except Exception as e:
        logger.error(f"Erreur lors de la vérification des transactions idle: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Diagnostiquer et corriger les transactions PostgreSQL en échec')
    parser.add_argument('--kill-idle', action='store_true', help='Tuer les transactions idle in transaction')
    parser.add_argument('--check-only', action='store_true', help='Vérifier uniquement sans corriger')
    
    args = parser.parse_args()
    
    logger.info("Diagnostic des transactions PostgreSQL...")
    
    # Vérification de base
    if not check_and_fix_transactions():
        logger.error("Problèmes détectés lors de la vérification")
        sys.exit(1)
    
    # Tuer les transactions idle si demandé
    if args.kill_idle:
        logger.info("Nettoyage des transactions idle in transaction...")
        kill_idle_transactions()
    
    logger.info("Diagnostic terminé")
    
    # Recommandation
    logger.info("")
    logger.info("Si le problème persiste:")
    logger.info("   1. Redémarrez CKAN: docker compose restart ckan")
    logger.info("   2. Vérifiez les logs: docker compose logs ckan | tail -100")
    logger.info("   3. Vérifiez les tables manquantes: /srv/app/scripts/fix-showcase-tables.sh")


if __name__ == '__main__':
    main()












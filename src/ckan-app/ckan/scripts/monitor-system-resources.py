#!/usr/bin/env python3
"""
Script de monitoring des ressources système pour CKAN
Surveille CPU, mémoire, disque, erreurs OSError et connexions PostgreSQL
"""

import os
import sys
import time
import shutil
import psutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Configuration
CKAN_LOG_DIR = Path(os.getenv('CKAN_LOG_DIR', '/var/log/ckan'))
CKAN_LOG_PATTERN = 'ckan*.log'
MAPFILES_DIR = Path('/mapserver/mapfiles')
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'db')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.getenv('POSTGRES_DB', 'ckan')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'ckan')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')

def get_cpu_usage() -> Dict[str, float]:
    """Récupère l'utilisation CPU"""
    cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
    cpu_avg = sum(cpu_percent) / len(cpu_percent)
    cpu_count = psutil.cpu_count()
    return {
        'average': cpu_avg,
        'per_core': cpu_percent,
        'count': cpu_count,
        'load_avg': os.getloadavg() if hasattr(os, 'getloadavg') else None
    }

def get_memory_usage() -> Dict[str, float]:
    """Récupère l'utilisation mémoire"""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        'total_gb': mem.total / (1024**3),
        'used_gb': mem.used / (1024**3),
        'available_gb': mem.available / (1024**3),
        'percent': mem.percent,
        'swap_total_gb': swap.total / (1024**3),
        'swap_used_gb': swap.used / (1024**3),
        'swap_percent': swap.percent
    }

def get_disk_usage(path: Path) -> Dict[str, float]:
    """Récupère l'utilisation disque pour un chemin"""
    try:
        usage = shutil.disk_usage(path)
        return {
            'total_gb': usage.total / (1024**3),
            'used_gb': usage.used / (1024**3),
            'free_gb': usage.free / (1024**3),
            'percent': (usage.used / usage.total) * 100
        }
    except Exception as e:
        return {'error': str(e)}

def count_oserrors_in_logs(log_dir: Path, pattern: str, last_minutes: int = 5) -> Dict[str, int]:
    """Compte les erreurs OSError dans les logs récents"""
    oserror_count = 0
    write_error_count = 0
    total_lines = 0
    
    try:
        log_files = list(log_dir.glob(pattern))
        cutoff_time = time.time() - (last_minutes * 60)
        
        for log_file in log_files:
            try:
                stat = log_file.stat()
                # Ne lire que si le fichier a été modifié récemment
                if stat.st_mtime < cutoff_time:
                    continue
                    
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    # Lire les dernières lignes seulement
                    lines = f.readlines()
                    total_lines += len(lines)
                    
                    for line in lines:
                        if 'OSError' in line:
                            oserror_count += 1
                        if 'OSError: write error' in line:
                            write_error_count += 1
            except Exception as e:
                pass  # Ignorer les erreurs de lecture
                
    except Exception as e:
        pass
    
    return {
        'oserror_total': oserror_count,
        'oserror_write_error': write_error_count,
        'log_files_checked': len(log_files) if 'log_files' in locals() else 0,
        'total_lines': total_lines
    }

def get_postgres_connections() -> Optional[Dict[str, int]]:
    """Récupère le nombre de connexions PostgreSQL actives"""
    try:
        try:
            import psycopg2
        except ImportError:
            return {'error': 'psycopg2 not available'}
            
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE state = 'active') as active,
                COUNT(*) FILTER (WHERE state = 'idle') as idle,
                COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction
            FROM pg_stat_activity
            WHERE datname = %s
        """, (POSTGRES_DB,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        return {
            'total': result[0],
            'active': result[1],
            'idle': result[2],
            'idle_in_transaction': result[3]
        }
    except Exception as e:
        return {'error': str(e)}

def get_mapfiles_info() -> Dict[str, any]:
    """Récupère des informations sur les mapfiles"""
    try:
        if not MAPFILES_DIR.exists():
            return {'error': f'Directory {MAPFILES_DIR} does not exist'}
        
        mapfiles = list(MAPFILES_DIR.glob('*.map'))
        total_size = sum(f.stat().st_size for f in mapfiles if f.is_file())
        
        return {
            'count': len(mapfiles),
            'total_size_mb': total_size / (1024**2),
            'directory': str(MAPFILES_DIR)
        }
    except Exception as e:
        return {'error': str(e)}

def format_report(metrics: Dict) -> str:
    """Formate un rapport de monitoring"""
    report = []
    report.append("=" * 80)
    report.append(f"RAPPORT DE MONITORING SYSTÈME - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 80)
    
    # CPU
    cpu = metrics['cpu']
    report.append(f"\n CPU:")
    report.append(f"   Utilisation moyenne: {cpu['average']:.1f}%")
    report.append(f"   Cœurs: {cpu['count']}")
    if cpu['load_avg']:
        report.append(f"   Load average: {cpu['load_avg'][0]:.2f}, {cpu['load_avg'][1]:.2f}, {cpu['load_avg'][2]:.2f}")
    
    # Mémoire
    mem = metrics['memory']
    report.append(f"\nMÉMOIRE:")
    report.append(f"   Total: {mem['total_gb']:.2f} GB")
    report.append(f"   Utilisée: {mem['used_gb']:.2f} GB ({mem['percent']:.1f}%)")
    report.append(f"   Disponible: {mem['available_gb']:.2f} GB")
    if mem['swap_total_gb'] > 0:
        report.append(f"   Swap: {mem['swap_used_gb']:.2f} GB / {mem['swap_total_gb']:.2f} GB ({mem['swap_percent']:.1f}%)")
    
    # Disque
    disk = metrics['disk']
    if 'error' not in disk:
        report.append(f"\nDISQUE ({MAPFILES_DIR}):")
        report.append(f"   Total: {disk['total_gb']:.2f} GB")
        report.append(f"   Utilisé: {disk['used_gb']:.2f} GB ({disk['percent']:.1f}%)")
        report.append(f"   Libre: {disk['free_gb']:.2f} GB")
    else:
        report.append(f"\nDISQUE: Erreur - {disk['error']}")
    
    # Mapfiles
    mapfiles = metrics['mapfiles']
    if 'error' not in mapfiles:
        report.append(f"\n MAPFILES:")
        report.append(f"   Nombre: {mapfiles['count']}")
        report.append(f"   Taille totale: {mapfiles['total_size_mb']:.2f} MB")
    
    # PostgreSQL
    pg = metrics['postgres']
    if 'error' not in pg:
        report.append(f"\nPOSTGRESQL:")
        report.append(f"   Connexions totales: {pg['total']}")
        report.append(f"   Actives: {pg['active']}")
        report.append(f"   Idle: {pg['idle']}")
        report.append(f"   Idle in transaction: {pg['idle_in_transaction']}")
    else:
        report.append(f"\nPOSTGRESQL: Erreur - {pg['error']}")
    
    # Erreurs OSError
    errors = metrics['oserrors']
    report.append(f"\n ERREURS OSError (dernières 5 minutes):")
    report.append(f"   Total OSError: {errors['oserror_total']}")
    report.append(f"   OSError: write error: {errors['oserror_write_error']}")
    report.append(f"   Fichiers logs vérifiés: {errors['log_files_checked']}")
    
    # Alertes
    alerts = []
    if cpu['average'] > 80:
        alerts.append(f"CPU élevé: {cpu['average']:.1f}%")
    if mem['percent'] > 85:
        alerts.append(f"Mémoire élevée: {mem['percent']:.1f}%")
    if 'percent' in disk and disk['percent'] > 90:
        alerts.append(f"Disque presque plein: {disk['percent']:.1f}%")
    if errors['oserror_write_error'] > 10:
        alerts.append(f"Nombre élevé d'erreurs 'write error': {errors['oserror_write_error']}")
    if 'total' in pg and pg['total'] > 50:
        alerts.append(f"Nombre élevé de connexions PostgreSQL: {pg['total']}")
    
    if alerts:
        report.append(f"\nALERTES:")
        for alert in alerts:
            report.append(f"   {alert}")
    else:
        report.append(f"\nAucune alerte")
    
    report.append("=" * 80)
    return "\n".join(report)

def main():
    """Fonction principale"""
    try:
        metrics = {
            'cpu': get_cpu_usage(),
            'memory': get_memory_usage(),
            'disk': get_disk_usage(MAPFILES_DIR),
            'mapfiles': get_mapfiles_info(),
            'postgres': get_postgres_connections(),
            'oserrors': count_oserrors_in_logs(CKAN_LOG_DIR, CKAN_LOG_PATTERN)
        }
        
        report = format_report(metrics)
        print(report)
        
        return 0
    except Exception as e:
        print(f"Erreur lors du monitoring: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
Script de monitoring des performances CKAN
Analyse les logs pour identifier les problèmes de performance et les OSError
"""

import sys
import os
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import Dict, List, Tuple
import argparse

# Ajouter le chemin de CKAN au PYTHONPATH
sys.path.insert(0, '/srv/app')

try:
    from ckan.config.environment import load_environment
    from ckan import model
    from ckan.common import config
    load_environment(config)
except Exception as e:
    print(f"Impossible de charger l'environnement CKAN: {e}")
    print("   Le script fonctionnera en mode standalone (analyse de logs uniquement)")


class PerformanceMonitor:
    """Moniteur de performance pour CKAN"""
    
    def __init__(self, log_file: str = None):
        self.log_file = log_file or '/var/log/ckan/ckan.log'
        self.oserrors = []
        self.slow_requests = []
        self.db_queries = []
        self.errors = []
        
    def analyze_logs(self, hours: int = 1, tail_lines: int = None):
        """
        Analyse les logs CKAN
        
        Args:
            hours: Nombre d'heures à analyser (si tail_lines n'est pas spécifié)
            tail_lines: Nombre de lignes à analyser depuis la fin du fichier
        """
        print(f"Analyse des logs CKAN...")
        print(f"   Fichier: {self.log_file}")
        
        if not os.path.exists(self.log_file):
            print(f"Fichier de log non trouvé: {self.log_file}")
            print("   Tentative avec les logs Docker...")
            # Essayer de lire depuis stdout/stderr si on est dans un conteneur
            return self._analyze_docker_logs()
        
        try:
            with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
                if tail_lines:
                    # Lire les N dernières lignes
                    lines = f.readlines()[-tail_lines:]
                else:
                    # Lire toutes les lignes depuis X heures
                    lines = f.readlines()
                    cutoff_time = datetime.now() - timedelta(hours=hours)
                    filtered_lines = []
                    for line in lines:
                        # Extraire le timestamp si présent
                        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})', line)
                        if timestamp_match:
                            try:
                                line_time = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
                                if line_time >= cutoff_time:
                                    filtered_lines.append(line)
                            except Exception:
                                filtered_lines.append(line)
                        else:
                            # Si pas de timestamp, inclure la ligne
                            filtered_lines.append(line)
                    lines = filtered_lines
                
                self._parse_lines(lines)
        except Exception as e:
            print(f"Erreur lors de la lecture du log: {e}")
            return False
        
        return True
    
    def _analyze_docker_logs(self):
        """Analyse les logs depuis Docker (stdout/stderr)"""
        print("   Mode Docker: analyse des logs depuis stdout/stderr...")
        # Dans Docker, les logs sont souvent sur stdout/stderr
        # On peut utiliser journalctl ou docker logs
        print("   Utilisez: docker compose logs --tail=10000 ckan | python3 monitor-performance.py --stdin")
        return False
    
    def _parse_lines(self, lines: List[str]):
        """Parse les lignes de log pour extraire les informations"""
        print(f"   Analyse de {len(lines)} lignes...")
        
        for line in lines:
            # Détecter les OSError
            if 'OSError' in line or 'write error' in line.lower():
                self.oserrors.append({
                    'line': line.strip(),
                    'timestamp': self._extract_timestamp(line)
                })
            
            # Détecter les requêtes lentes (si on a des métriques de timing)
            slow_match = re.search(r'(\d+\.\d+)s.*(GET|POST|PUT|DELETE)\s+([^\s]+)', line)
            if slow_match:
                duration = float(slow_match.group(1))
                if duration > 2.0:  # Plus de 2 secondes
                    self.slow_requests.append({
                        'duration': duration,
                        'method': slow_match.group(2),
                        'path': slow_match.group(3),
                        'line': line.strip(),
                        'timestamp': self._extract_timestamp(line)
                    })
            
            # Détecter les erreurs
            if re.search(r'\b(ERROR|CRITICAL|Exception|Traceback)\b', line, re.IGNORECASE):
                self.errors.append({
                    'line': line.strip(),
                    'timestamp': self._extract_timestamp(line)
                })
            
            # Détecter les requêtes DB lentes (si on a des logs SQL)
            db_match = re.search(r'(\d+\.\d+)s.*SELECT|INSERT|UPDATE|DELETE', line, re.IGNORECASE)
            if db_match:
                duration = float(db_match.group(1))
                if duration > 1.0:  # Plus d'1 seconde
                    self.db_queries.append({
                        'duration': duration,
                        'line': line.strip(),
                        'timestamp': self._extract_timestamp(line)
                    })
    
    def _extract_timestamp(self, line: str) -> str:
        """Extrait le timestamp d'une ligne de log"""
        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})', line)
        if timestamp_match:
            return timestamp_match.group(1)
        return "N/A"
    
    def generate_report(self):
        """Génère un rapport de performance"""
        print("\n" + "="*80)
        print("RAPPORT DE PERFORMANCE CKAN")
        print("="*80)
        
        # OSError analysis
        print(f"\nOSError 'write error': {len(self.oserrors)} occurrences")
        if self.oserrors:
            print("   Les OSError peuvent indiquer:")
            print("      - Saturation de stdout/stderr")
            print("      - Problème de buffer système")
            print("      - Trop de logs générés simultanément")
            
            # Grouper par type d'erreur
            error_types = Counter()
            for err in self.oserrors:
                if 'write error' in err['line'].lower():
                    error_types['write error'] += 1
                elif 'Broken pipe' in err['line']:
                    error_types['Broken pipe'] += 1
                else:
                    error_types['Other OSError'] += 1
            
            print("\n   Répartition des erreurs:")
            for err_type, count in error_types.most_common():
                print(f"      - {err_type}: {count}")
            
            if len(self.oserrors) > 100:
                print(f"\n   ATTENTION: {len(self.oserrors)} OSError détectés!")
                print("      Cela peut significativement ralentir l'application.")
                print("      Solutions possibles:")
                print("      1. Réduire le niveau de log (DEBUG -> INFO)")
                print("      2. Utiliser un handler de log asynchrone")
                print("      3. Augmenter les buffers système")
                print("      4. Vérifier l'espace disque")
        
        # Slow requests
        print(f"\nRequêtes lentes (>2s): {len(self.slow_requests)}")
        if self.slow_requests:
            # Grouper par endpoint
            endpoints = Counter()
            for req in self.slow_requests:
                endpoints[req['path']] += 1
            
            print("   Top 5 des endpoints les plus lents:")
            for endpoint, count in endpoints.most_common(5):
                avg_duration = sum(r['duration'] for r in self.slow_requests if r['path'] == endpoint) / count
                print(f"      - {endpoint}: {count} requêtes, moyenne {avg_duration:.2f}s")
        
        # DB queries
        print(f"\nRequêtes DB lentes (>1s): {len(self.db_queries)}")
        if self.db_queries:
            avg_db_time = sum(q['duration'] for q in self.db_queries) / len(self.db_queries)
            max_db_time = max(q['duration'] for q in self.db_queries)
            print(f"   Temps moyen: {avg_db_time:.2f}s")
            print(f"   Temps max: {max_db_time:.2f}s")
        
        # Errors
        print(f"\nErreurs totales: {len(self.errors)}")
        if self.errors:
            # Grouper par type d'erreur
            error_patterns = Counter()
            for err in self.errors:
                if 'Exception' in err['line']:
                    exc_match = re.search(r'(\w+Exception)', err['line'])
                    if exc_match:
                        error_patterns[exc_match.group(1)] += 1
                    else:
                        error_patterns['Exception'] += 1
                elif 'ERROR' in err['line']:
                    error_patterns['ERROR'] += 1
            
            print("   Types d'erreurs:")
            for err_type, count in error_patterns.most_common(10):
                print(f"      - {err_type}: {count}")
        
        # Recommandations
        print("\n" + "="*80)
        print("RECOMMANDATIONS")
        print("="*80)
        
        if len(self.oserrors) > 50:
            print("\n1. OSError fréquents:")
            print("   - Vérifier l'espace disque: df -h")
            print("   - Réduire le niveau de log dans ckan.ini")
            print("   - Considérer l'utilisation de logging asynchrone")
        
        if len(self.slow_requests) > 10:
            print("\n2. Requêtes lentes:")
            print("   - Activer le cache Redis si pas déjà fait")
            print("   - Vérifier les index de base de données")
            print("   - Optimiser les requêtes Solr")
        
        if len(self.db_queries) > 5:
            print("\n3. Requêtes DB lentes:")
            print("   - Analyser avec EXPLAIN ANALYZE")
            print("   - Vérifier les index manquants")
            print("   - Considérer la mise en cache des résultats")
        
        print("\n" + "="*80)
    
    def export_json(self, output_file: str):
        """Exporte les résultats en JSON"""
        data = {
            'timestamp': datetime.now().isoformat(),
            'oserrors_count': len(self.oserrors),
            'oserrors': self.oserrors[:100],  # Limiter à 100 pour éviter des fichiers trop gros
            'slow_requests_count': len(self.slow_requests),
            'slow_requests': self.slow_requests[:50],
            'db_queries_count': len(self.db_queries),
            'db_queries': self.db_queries[:50],
            'errors_count': len(self.errors),
            'errors': self.errors[:100]
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"\nRésultats exportés dans: {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Monitor CKAN performance')
    parser.add_argument('--log-file', '-f', help='Chemin du fichier de log', 
                       default='/var/log/ckan/ckan.log')
    parser.add_argument('--hours', '-h', type=int, default=1, 
                       help='Nombre d\'heures à analyser')
    parser.add_argument('--tail', '-n', type=int, 
                       help='Nombre de lignes à analyser depuis la fin')
    parser.add_argument('--stdin', action='store_true',
                       help='Lire depuis stdin (pour docker logs)')
    parser.add_argument('--json', '-j', help='Exporter en JSON')
    
    args = parser.parse_args()
    
    monitor = PerformanceMonitor(log_file=args.log_file)
    
    if args.stdin:
        # Lire depuis stdin
        print("Analyse des logs depuis stdin...")
        lines = sys.stdin.readlines()
        monitor._parse_lines(lines)
    else:
        # Analyser le fichier de log
        monitor.analyze_logs(hours=args.hours, tail_lines=args.tail)
    
    # Générer le rapport
    monitor.generate_report()
    
    # Exporter en JSON si demandé
    if args.json:
        monitor.export_json(args.json)


if __name__ == '__main__':
    main()

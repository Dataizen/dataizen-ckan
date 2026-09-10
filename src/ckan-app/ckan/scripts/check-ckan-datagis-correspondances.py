#!/usr/bin/env python3
"""
Script pour vérifier les correspondances entre les ressources CKAN et les tables datagis

Ce script :
1. Liste les ressources géospatiales dans CKAN
2. Liste les tables dans datagis
3. Essaie de faire correspondre les ressources CKAN avec les tables datagis
4. Affiche les correspondances trouvées
"""

import os
import sys
import logging
import requests
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    print("psycopg2 non disponible, installation requise")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class CorrespondanceChecker:
    """Vérifie les correspondances entre CKAN et datagis"""
    
    def __init__(self, ckan_url: str, ckan_api_key: str,
                 postgis_host: str = 'db', postgis_port: int = 5432,
                 datagis_db: str = 'datagis', postgis_user: str = 'ckan',
                 postgis_password: str = ''):
        """
        Initialise le vérificateur de correspondances
        
        Args:
            ckan_url: URL de l'API CKAN
            ckan_api_key: Clé API CKAN
            postgis_host: Host PostGIS
            postgis_port: Port PostGIS
            datagis_db: Base de données datagis
            postgis_user: Utilisateur PostGIS
            postgis_password: Mot de passe PostGIS
        """
        self.ckan_url = ckan_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.postgis_host = postgis_host
        self.postgis_port = postgis_port
        self.datagis_db = datagis_db
        self.postgis_user = postgis_user
        self.postgis_password = postgis_password
        
        self.headers = {'Authorization': self.ckan_api_key} if self.ckan_api_key else {}
    
    def get_ckan_geospatial_resources(self) -> List[Dict[str, Any]]:
        """
        Récupère toutes les ressources géospatiales depuis CKAN
        
        Returns:
            Liste des ressources géospatiales
        """
        try:
            url = f"{self.ckan_url}/api/action/package_search"
            params = {
                'rows': 1000,
                'fq': 'res_format:(SHP OR GeoJSON OR GPKG OR KML OR KMZ OR Shapefile OR ZIP)',
                'include_private': False
            }
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                logger.error(f"Erreur API CKAN: {result.get('error', {})}")
                return []
            
            resources = []
            for dataset in result.get('result', {}).get('results', []):
                for resource in dataset.get('resources', []):
                    format_ = resource.get('format', '').upper()
                    if format_ in {'SHP', 'SHAPEFILE', 'GEOJSON', 'GPKG', 'GEOPACKAGE', 'KML', 'KMZ', 'ZIP'}:
                        resource['dataset_name'] = dataset.get('name')
                        resource['dataset_title'] = dataset.get('title')
                        resource['organization'] = dataset.get('organization', {}).get('name', '')
                        resources.append(resource)
            
            logger.info(f"{len(resources)} ressources géospatiales trouvées dans CKAN")
            return resources
            
        except Exception as e:
            logger.error(f"Erreur récupération ressources CKAN: {e}")
            return []
    
    def get_datagis_tables(self) -> List[Dict[str, Any]]:
        """
        Récupère toutes les tables avec géométrie depuis datagis
        
        Returns:
            Liste des tables avec leurs métadonnées
        """
        if not HAS_PSYCOPG2:
            logger.error("psycopg2 non disponible")
            return []
        
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    f_table_name,
                    f_geometry_column,
                    srid,
                    (SELECT COUNT(*) FROM information_schema.columns 
                     WHERE table_name = f_table_name AND table_schema = 'public') as nb_cols
                FROM geometry_columns
                WHERE f_table_schema = 'public'
                ORDER BY f_table_name;
            """)
            
            tables = []
            for row in cur.fetchall():
                tables.append({
                    'table_name': row[0],
                    'geom_column': row[1],
                    'srid': row[2],
                    'nb_cols': row[3]
                })
            
            cur.close()
            conn.close()
            
            logger.info(f"{len(tables)} tables géospatiales trouvées dans datagis")
            return tables
            
        except Exception as e:
            logger.error(f"Erreur récupération tables datagis: {e}")
            return []
    
    def extract_keywords(self, text: str) -> List[str]:
        """
        Extrait les mots-clés significatifs d'un texte
        
        Args:
            text: Texte à analyser
            
        Returns:
            Liste des mots-clés significatifs
        """
        if not text:
            return []
        
        # Nettoyer et séparer
        clean_text = text.lower().replace('-', '_').replace(' ', '_')
        keywords = clean_text.split('_')
        
        # Filtrer les mots courts et communs
        common_words = {'de', 'du', 'des', 'la', 'le', 'les', 'et', 'en', 'pour', 'avec', 'sur', 'via', 'une', 'aux', 'par', 'dans'}
        significant = [kw for kw in keywords if len(kw) >= 3 and kw not in common_words]
        
        return significant
    
    def calculate_similarity(self, resource: Dict[str, Any], table: Dict[str, Any]) -> float:
        """
        Calcule un score de similarité entre une ressource CKAN et une table datagis
        
        Args:
            resource: Dictionnaire de la ressource CKAN
            table: Dictionnaire de la table datagis
            
        Returns:
            Score de similarité (0.0 à 1.0)
        """
        score = 0.0
        
        # Extraire les mots-clés
        resource_name = resource.get('name', '').lower()
        resource_dataset = resource.get('dataset_name', '').lower()
        table_name = table['table_name'].lower()
        
        # Mots-clés de la ressource
        resource_keywords = set(self.extract_keywords(resource_name) + self.extract_keywords(resource_dataset))
        
        # Mots-clés de la table (sans le hash)
        # Le hash est généralement les 8 derniers caractères après le dernier underscore
        table_parts = table_name.rsplit('_', 1)
        if len(table_parts) == 2 and len(table_parts[1]) == 8:
            # Probablement un hash, l'enlever
            table_base = table_parts[0]
        else:
            table_base = table_name
        
        table_keywords = set(self.extract_keywords(table_base))
        
        # Calculer la similarité
        if not resource_keywords or not table_keywords:
            return 0.0
        
        # Intersection des mots-clés
        common_keywords = resource_keywords & table_keywords
        if not common_keywords:
            return 0.0
        
        # Score basé sur l'intersection
        intersection_score = len(common_keywords) / max(len(resource_keywords), len(table_keywords))
        
        # Bonus si le nom de la ressource ou du dataset correspond au début de la table
        if resource_name and table_base.startswith(resource_name.replace('-', '_').replace(' ', '_')[:20]):
            score += 0.3
        
        if resource_dataset and table_base.startswith(resource_dataset.replace('-', '_')[:20]):
            score += 0.3
        
        # Score final
        score += intersection_score * 0.4
        
        return min(score, 1.0)
    
    def find_correspondances(self) -> List[Dict[str, Any]]:
        """
        Trouve les correspondances entre ressources CKAN et tables datagis
        
        Returns:
            Liste des correspondances trouvées
        """
        logger.info("Recherche des correspondances...")
        
        # Récupérer les ressources CKAN
        ckan_resources = self.get_ckan_geospatial_resources()
        
        # Récupérer les tables datagis
        datagis_tables = self.get_datagis_tables()
        
        correspondances = []
        
        # Pour chaque ressource CKAN, chercher des correspondances dans datagis
        for resource in ckan_resources:
            resource_id = resource.get('id')
            resource_name = resource.get('name', '')
            dataset_name = resource.get('dataset_name', '')
            format_ = resource.get('format', '')
            
            # Calculer le hash attendu (pour les nouvelles tables)
            hash_expected = hashlib.md5(resource_id.encode()).hexdigest()[:8]
            
            best_match = None
            best_score = 0.0
            
            # Chercher la meilleure correspondance
            for table in datagis_tables:
                score = self.calculate_similarity(resource, table)
                
                # Bonus si le hash correspond (pour les nouvelles tables)
                if table['table_name'].endswith(f'_{hash_expected}'):
                    score += 0.2
                
                if score > best_score:
                    best_score = score
                    best_match = table
            
            # Si score > 0.3, considérer comme correspondance
            if best_match and best_score >= 0.3:
                correspondances.append({
                    'resource_id': resource_id,
                    'resource_name': resource_name,
                    'resource_format': format_,
                    'dataset_name': dataset_name,
                    'dataset_title': resource.get('dataset_title', ''),
                    'organization': resource.get('organization', ''),
                    'datastore_active': resource.get('datastore_active', False),
                    'table_name': best_match['table_name'],
                    'geom_column': best_match['geom_column'],
                    'srid': best_match['srid'],
                    'nb_cols': best_match['nb_cols'],
                    'similarity_score': best_score,
                    'hash_match': best_match['table_name'].endswith(f'_{hash_expected}')
                })
        
        logger.info(f"{len(correspondances)} correspondances trouvées")
        return correspondances
    
    def generate_report(self, correspondances: List[Dict[str, Any]]) -> str:
        """
        Génère un rapport des correspondances
        
        Args:
            correspondances: Liste des correspondances
            
        Returns:
            Rapport formaté
        """
        report = []
        report.append("=" * 80)
        report.append("RAPPORT DE CORRESPONDANCES CKAN ↔ DATAGIS")
        report.append("=" * 80)
        report.append(f"Généré le: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Statistiques
        total_resources = len(correspondances)
        with_hash_match = sum(1 for c in correspondances if c.get('hash_match'))
        non_datastore = sum(1 for c in correspondances if not c.get('datastore_active'))
        
        report.append("STATISTIQUES")
        report.append("-" * 80)
        report.append(f"Total correspondances trouvées: {total_resources}")
        report.append(f"  - Avec hash correspondant: {with_hash_match}")
        report.append(f"  - Ressources non-datastore: {non_datastore}")
        report.append("")
        
        # Correspondances par score
        report.append("CORRESPONDANCES PAR SCORE DE SIMILARITÉ")
        report.append("-" * 80)
        
        # Trier par score décroissant
        correspondances_sorted = sorted(correspondances, key=lambda x: x['similarity_score'], reverse=True)
        
        for i, corr in enumerate(correspondances_sorted, 1):
            hash_indicator = "" if corr.get('hash_match') else "  "
            datastore_indicator = "" if corr.get('datastore_active') else ""
            
            report.append(f"\n{i}. {hash_indicator} {datastore_indicator} Score: {corr['similarity_score']:.2f}")
            report.append(f"   CKAN:")
            report.append(f"     - Dataset: {corr['dataset_name']}")
            report.append(f"     - Titre: {corr['dataset_title']}")
            report.append(f"     - Ressource: {corr['resource_name']} ({corr['resource_format']})")
            report.append(f"     - ID: {corr['resource_id']}")
            report.append(f"     - Organisation: {corr.get('organization', 'N/A')}")
            report.append(f"   DATAGIS:")
            report.append(f"     - Table: {corr['table_name']}")
            report.append(f"     - Géométrie: {corr['geom_column']} (SRID: {corr['srid']})")
            report.append(f"     - Colonnes: {corr['nb_cols']}")
            if corr.get('hash_match'):
                report.append(f"     - Hash correspondant (table probablement créée par notre script)")
        
        report.append("")
        report.append("=" * 80)
        report.append("LÉGENDE")
        report.append("  = Hash correspondant (table créée par notre script)")
        report.append("  = Ressource dans datastore")
        report.append("  = Ressource non-datastore (candidate pour import datagis)")
        report.append("=" * 80)
        
        return "\n".join(report)


def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Vérifie les correspondances entre CKAN et datagis')
    parser.add_argument('--ckan-url', default=os.getenv('CKAN_URL', 'http://localhost:5000'),
                       help='URL de l\'API CKAN')
    parser.add_argument('--ckan-api-key', default=os.getenv('CKAN_API_KEY', ''),
                       help='Clé API CKAN')
    parser.add_argument('--postgis-host', default=os.getenv('POSTGIS_HOST', 'db'),
                       help='Host PostGIS')
    parser.add_argument('--postgis-port', type=int, default=int(os.getenv('POSTGIS_PORT', '5432')),
                       help='Port PostGIS')
    parser.add_argument('--datagis-db', default=os.getenv('DATAGIS_DB', 'datagis'),
                       help='Base de données datagis')
    parser.add_argument('--postgis-user', default=os.getenv('POSTGIS_USER', 'ckan'),
                       help='Utilisateur PostGIS')
    parser.add_argument('--postgis-password', default=os.getenv('POSTGIS_PASSWORD', ''),
                       help='Mot de passe PostGIS')
    parser.add_argument('--output', default=None,
                       help='Fichier de sortie pour le rapport (par défaut: stdout)')
    parser.add_argument('--min-score', type=float, default=0.3,
                       help='Score minimum de similarité pour considérer une correspondance (0.0-1.0)')
    
    args = parser.parse_args()
    
    checker = CorrespondanceChecker(
        ckan_url=args.ckan_url,
        ckan_api_key=args.ckan_api_key,
        postgis_host=args.postgis_host,
        postgis_port=args.postgis_port,
        datagis_db=args.datagis_db,
        postgis_user=args.postgis_user,
        postgis_password=args.postgis_password
    )
    
    # Trouver les correspondances
    correspondances = checker.find_correspondances()
    
    # Filtrer par score minimum
    correspondances = [c for c in correspondances if c['similarity_score'] >= args.min_score]
    
    # Générer le rapport
    report = checker.generate_report(correspondances)
    
    # Afficher ou sauvegarder
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Rapport sauvegardé dans: {args.output}")
    else:
        print(report)
    
    sys.exit(0 if correspondances else 1)


if __name__ == '__main__':
    main()


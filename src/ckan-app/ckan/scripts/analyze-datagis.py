#!/usr/bin/env python3
"""
Script pour analyser la base de données datagis

Ce script :
1. Liste toutes les tables dans datagis
2. Affiche leur structure (colonnes, types, nombre de lignes)
3. Affiche les métadonnées géospatiales (SRID, type de géométrie, BBOX)
4. Propose des noms de tables basés sur l'ID de ressource CKAN
5. Compare avec les noms actuels et suggère des améliorations
"""

import os
import sys
import logging
import requests
import json
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

try:
    import psycopg2
    from psycopg2 import OperationalError, DatabaseError
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    print("psycopg2 non disponible, installation requise")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DatagisAnalyzer:
    """Analyseur de la base de données datagis"""
    
    def __init__(self, ckan_url: str, ckan_api_key: str,
                 postgis_host: str = 'db', postgis_port: int = 5432,
                 datagis_db: str = 'datagis', postgis_user: str = 'ckan',
                 postgis_password: str = ''):
        """
        Initialise l'analyseur
        
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
    
    def connect(self):
        """Établit la connexion à datagis"""
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=10
            )
            return conn
        except OperationalError as e:
            error_msg = str(e)
            if 'database' in error_msg.lower() and 'does not exist' in error_msg.lower():
                logger.error(f"Base de données '{self.datagis_db}' n'existe pas")
                logger.info(f"Pour créer la base datagis:")
                logger.info(f"   1. Se connecter à PostgreSQL: docker exec -it db psql -U {self.postgis_user}")
                logger.info(f"   2. Créer la base: CREATE DATABASE {self.datagis_db} OWNER {self.postgis_user} ENCODING 'utf-8';")
                logger.info(f"   3. Activer PostGIS: \\c {self.datagis_db}; CREATE EXTENSION IF NOT EXISTS postgis;")
            else:
                logger.error(f"Erreur de connexion à datagis: {e}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la connexion à datagis: {e}")
            return None
    
    def get_all_tables(self, conn) -> List[Dict[str, Any]]:
        """
        Récupère toutes les tables géospatiales dans datagis
        
        Returns:
            Liste des tables avec leurs métadonnées
        """
        cur = conn.cursor()
        
        # Récupérer toutes les tables avec géométrie
        cur.execute("""
            SELECT 
                f_table_name,
                f_geometry_column,
                coord_dimension,
                srid,
                type
            FROM geometry_columns
            WHERE f_table_schema = 'public'
            ORDER BY f_table_name;
        """)
        
        tables = []
        for row in cur.fetchall():
            table_name, geom_col, coord_dim, srid, geom_type = row
            tables.append({
                'table_name': table_name,
                'geometry_column': geom_col,
                'coord_dimension': coord_dim,
                'srid': srid,
                'geometry_type': geom_type
            })
        
        cur.close()
        return tables
    
    def get_table_structure(self, conn, table_name: str) -> Dict[str, Any]:
        """
        Récupère la structure complète d'une table
        
        Args:
            conn: Connexion PostGIS
            table_name: Nom de la table
            
        Returns:
            Dictionnaire avec les informations de la table
        """
        cur = conn.cursor()
        
        # Récupérer les colonnes
        cur.execute("""
            SELECT 
                column_name,
                data_type,
                character_maximum_length,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = %s
            ORDER BY ordinal_position;
        """, (table_name,))
        
        columns = []
        for row in cur.fetchall():
            col_name, data_type, max_length, is_nullable = row
            columns.append({
                'name': col_name,
                'type': data_type,
                'max_length': max_length,
                'nullable': is_nullable == 'YES'
            })
        
        # Récupérer le nombre de lignes
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}";')
        row_count = cur.fetchone()[0]
        
        # Récupérer les métadonnées géospatiales
        cur.execute("""
            SELECT 
                f_geometry_column,
                coord_dimension,
                srid,
                type
            FROM geometry_columns
            WHERE f_table_schema = 'public'
            AND f_table_name = %s;
        """, (table_name,))
        
        geom_info = cur.fetchone()
        if geom_info:
            geom_col, coord_dim, srid, geom_type = geom_info
            
            # Récupérer le BBOX
            try:
                cur.execute(f"""
                    SELECT 
                        ST_XMin(ST_Extent(ST_Transform("{geom_col}", 4326))) as minx,
                        ST_YMin(ST_Extent(ST_Transform("{geom_col}", 4326))) as miny,
                        ST_XMax(ST_Extent(ST_Transform("{geom_col}", 4326))) as maxx,
                        ST_YMax(ST_Extent(ST_Transform("{geom_col}", 4326))) as maxy
                    FROM "{table_name}"
                    WHERE "{geom_col}" IS NOT NULL;
                """)
                bbox_result = cur.fetchone()
                if bbox_result and all(v is not None for v in bbox_result):
                    bbox = {
                        'minx': bbox_result[0],
                        'miny': bbox_result[1],
                        'maxx': bbox_result[2],
                        'maxy': bbox_result[3]
                    }
                else:
                    bbox = None
            except Exception as e:
                logger.debug(f"Erreur calcul BBOX pour {table_name}: {e}")
                bbox = None
        else:
            geom_col = None
            coord_dim = None
            srid = None
            geom_type = None
            bbox = None
        
        cur.close()
        
        return {
            'table_name': table_name,
            'columns': columns,
            'row_count': row_count,
            'geometry_column': geom_col,
            'coord_dimension': coord_dim,
            'srid': srid,
            'geometry_type': geom_type,
            'bbox': bbox
        }
    
    def get_ckan_resources(self) -> Dict[str, Dict[str, Any]]:
        """
        Récupère toutes les ressources géospatiales depuis CKAN
        
        Returns:
            Dictionnaire {resource_id: resource_info}
        """
        try:
            url = f"{self.ckan_url}/api/action/package_search"
            params = {
                'rows': 1000,
                'fq': 'res_format:(SHP OR GeoJSON OR GPKG OR KML OR KMZ OR Shapefile)',
                'include_private': False
            }
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                logger.error(f"Erreur API CKAN: {result.get('error', {})}")
                return {}
            
            resources = {}
            for dataset in result.get('result', {}).get('results', []):
                dataset_name = dataset.get('name', 'unknown')
                dataset_title = dataset.get('title', 'unknown')
                for resource in dataset.get('resources', []):
                    resource_id = resource.get('id')
                    if resource_id:
                        resources[resource_id] = {
                            'resource_id': resource_id,
                            'resource_name': resource.get('name', ''),
                            'format': resource.get('format', ''),
                            'url': resource.get('url', ''),
                            'dataset_name': dataset_name,
                            'dataset_title': dataset_title,
                            'datastore_active': resource.get('datastore_active', False)
                        }
            
            logger.info(f"{len(resources)} ressources géospatiales trouvées dans CKAN")
            return resources
        except Exception as e:
            logger.error(f"Erreur récupération ressources CKAN: {e}")
            return {}
    
    def suggest_table_name_from_resource_id(self, resource_id: str) -> str:
        """
        Suggère un nom de table basé sur l'ID de ressource
        
        Args:
            resource_id: ID de la ressource CKAN
            
        Returns:
            Nom de table suggéré (format: res_{resource_id_short})
        """
        # Utiliser directement l'ID de ressource (UUID ou hash)
        # Format: res_{8_premiers_caracteres}
        # PostgreSQL limite à 63 caractères, donc on prend les 8 premiers caractères
        resource_id_short = resource_id[:8] if len(resource_id) > 8 else resource_id
        table_name = f"res_{resource_id_short}"
        
        # Limiter à 63 caractères (limite PostgreSQL)
        if len(table_name) > 63:
            table_name = table_name[:63]
        
        return table_name
    
    def analyze(self) -> Dict[str, Any]:
        """
        Analyse complète de la base datagis
        
        Returns:
            Dictionnaire avec toutes les analyses
        """
        logger.info("=" * 80)
        logger.info("DÉBUT ANALYSE DATAGIS")
        logger.info("=" * 80)
        
        conn = self.connect()
        if not conn:
            return {}
        
        # Récupérer toutes les tables
        logger.info("Récupération des tables géospatiales...")
        tables = self.get_all_tables(conn)
        logger.info(f"{len(tables)} table(s) géospatiale(s) trouvée(s)")
        
        # Récupérer les ressources CKAN
        logger.info("Récupération des ressources CKAN...")
        ckan_resources = self.get_ckan_resources()
        
        # Analyser chaque table
        logger.info("Analyse détaillée des tables...")
        table_analyses = []
        for table in tables:
            table_name = table['table_name']
            logger.info(f"   Analyse de {table_name}...")
            structure = self.get_table_structure(conn, table_name)
            table_analyses.append(structure)
        
        conn.close()
        
        # Générer des suggestions de noms basés sur les ressources
        logger.info("Génération de suggestions de noms de tables...")
        suggestions = {}
        for resource_id, resource_info in ckan_resources.items():
            if not resource_info.get('datastore_active'):
                suggested_name = self.suggest_table_name_from_resource_id(resource_id)
                suggestions[resource_id] = {
                    'resource_id': resource_id,
                    'resource_name': resource_info.get('resource_name', ''),
                    'dataset_name': resource_info.get('dataset_name', ''),
                    'format': resource_info.get('format', ''),
                    'suggested_table_name': suggested_name,
                    'current_table_name': None  # À déterminer par correspondance
                }
        
        return {
            'tables': table_analyses,
            'ckan_resources': ckan_resources,
            'suggestions': suggestions,
            'summary': {
                'total_tables': len(table_analyses),
                'total_resources': len(ckan_resources),
                'total_suggestions': len(suggestions)
            }
        }
    
    def print_report(self, analysis: Dict[str, Any], output_format: str = 'text'):
        """
        Affiche un rapport d'analyse
        
        Args:
            analysis: Résultat de l'analyse
            output_format: Format de sortie ('text' ou 'json')
        """
        if output_format == 'json':
            print(json.dumps(analysis, indent=2, default=str))
            return
        
        # Format texte
        print("\n" + "=" * 80)
        print("RAPPORT D'ANALYSE DATAGIS")
        print("=" * 80)
        
        summary = analysis.get('summary', {})
        print(f"\nRÉSUMÉ:")
        print(f"   • Total tables géospatiales: {summary.get('total_tables', 0)}")
        print(f"   • Total ressources CKAN: {summary.get('total_resources', 0)}")
        print(f"   • Suggestions de noms: {summary.get('total_suggestions', 0)}")
        
        # Détails des tables
        print("\n" + "=" * 80)
        print("DÉTAILS DES TABLES")
        print("=" * 80)
        
        for table in analysis.get('tables', []):
            print(f"\nTable: {table['table_name']}")
            print(f"   • Colonnes: {len(table['columns'])}")
            print(f"   • Lignes: {table['row_count']:,}")
            
            if table.get('geometry_column'):
                print(f"   • Colonne géométrie: {table['geometry_column']}")
                print(f"   • Type géométrie: {table.get('geometry_type', 'N/A')}")
                print(f"   • SRID: {table.get('srid', 'N/A')}")
                print(f"   • Dimensions: {table.get('coord_dimension', 'N/A')}")
                
                if table.get('bbox'):
                    bbox = table['bbox']
                    print(f"   • BBOX: [{bbox['minx']:.6f}, {bbox['miny']:.6f}, {bbox['maxx']:.6f}, {bbox['maxy']:.6f}]")
            
            print(f"   • Colonnes:")
            for col in table['columns'][:10]:  # Afficher les 10 premières
                nullable = "NULL" if col['nullable'] else "NOT NULL"
                max_len = f"({col['max_length']})" if col['max_length'] else ""
                print(f"     - {col['name']}: {col['type']}{max_len} {nullable}")
            if len(table['columns']) > 10:
                print(f"     ... et {len(table['columns']) - 10} autres colonnes")
        
        # Suggestions de noms
        print("\n" + "=" * 80)
        print("SUGGESTIONS DE NOUVEAUX NOMS DE TABLES")
        print("=" * 80)
        print("Format proposé: res_{8_premiers_caracteres_resource_id}")
        print("Avantages:")
        print("  • Nom unique basé sur l'ID de ressource CKAN")
        print("  • Pas de collision possible")
        print("  • Facile à retrouver depuis CKAN")
        print("  • Format court (limite PostgreSQL: 63 caractères)")
        
        print("\nRessources CKAN sans datastore (candidates pour datagis):")
        suggestions = analysis.get('suggestions', {})
        for resource_id, suggestion in list(suggestions.items())[:20]:  # Afficher les 20 premières
            print(f"\n   Ressource: {suggestion['resource_name'] or resource_id}")
            print(f"      • ID: {resource_id}")
            print(f"      • Dataset: {suggestion['dataset_name']}")
            print(f"      • Format: {suggestion['format']}")
            print(f"      • Nom table suggéré: {suggestion['suggested_table_name']}")
        
        if len(suggestions) > 20:
            print(f"\n   ... et {len(suggestions) - 20} autres ressources")
        
        # Comparaison avec les tables existantes
        print("\n" + "=" * 80)
        print("ANALYSE DES NOMS ACTUELS")
        print("=" * 80)
        
        current_tables = {t['table_name'] for t in analysis.get('tables', [])}
        suggested_tables = {s['suggested_table_name'] for s in suggestions.values()}
        
        collisions = current_tables & suggested_tables
        if collisions:
            print(f" {len(collisions)} collision(s) détectée(s) entre noms actuels et suggérés:")
            for coll in collisions:
                print(f"   • {coll}")
        else:
            print("Aucune collision détectée entre noms actuels et suggérés")
        
        print("\nStatistiques des noms actuels:")
        name_patterns = defaultdict(int)
        for table in analysis.get('tables', []):
            table_name = table['table_name']
            if '_' in table_name:
                parts = table_name.split('_')
                if len(parts) >= 2:
                    # Pattern: {name}_{hash}
                    pattern = f"{parts[0]}_{parts[-1]}"
                    name_patterns[pattern] += 1
                else:
                    name_patterns['other'] += 1
            else:
                name_patterns['simple'] += 1
        
        for pattern, count in sorted(name_patterns.items(), key=lambda x: -x[1]):
            print(f"   • Pattern '{pattern}': {count} table(s)")
        
        print("\n" + "=" * 80)
        print("ANALYSE TERMINÉE")
        print("=" * 80)


def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Analyse la base de données datagis')
    parser.add_argument('--ckan-url', default=os.getenv('CKAN_URL', 'http://localhost:5000'),
                       help='URL de l\'API CKAN')
    parser.add_argument('--ckan-api-key', default=os.getenv('CKAN_API_KEY', ''),
                       help='Clé API CKAN')
    parser.add_argument('--postgis-host', default=os.getenv('POSTGIS_HOST', 'db'),
                       help='Host PostGIS')
    parser.add_argument('--postgis-port', type=int, default=int(os.getenv('POSTGRES_PORT', '5432')),
                       help='Port PostGIS')
    parser.add_argument('--datagis-db', default=os.getenv('DATAGIS_DB', 'datagis'),
                       help='Base de données datagis')
    parser.add_argument('--postgis-user', default=os.getenv('POSTGRES_USER', 'ckan'),
                       help='Utilisateur PostGIS')
    parser.add_argument('--postgis-password', default=os.getenv('POSTGRES_PASSWORD', ''),
                       help='Mot de passe PostGIS')
    parser.add_argument('--output-format', choices=['text', 'json'], default='text',
                       help='Format de sortie (text ou json)')
    parser.add_argument('--output-file', help='Fichier de sortie (optionnel)')
    
    args = parser.parse_args()
    
    analyzer = DatagisAnalyzer(
        ckan_url=args.ckan_url,
        ckan_api_key=args.ckan_api_key,
        postgis_host=args.postgis_host,
        postgis_port=args.postgis_port,
        datagis_db=args.datagis_db,
        postgis_user=args.postgis_user,
        postgis_password=args.postgis_password
    )
    
    analysis = analyzer.analyze()
    
    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            if args.output_format == 'json':
                json.dump(analysis, f, indent=2, default=str)
            else:
                import io
                output = io.StringIO()
                analyzer.print_report(analysis, output_format=args.output_format)
                f.write(output.getvalue())
        logger.info(f"Rapport sauvegardé dans {args.output_file}")
    else:
        analyzer.print_report(analysis, output_format=args.output_format)


if __name__ == '__main__':
    main()


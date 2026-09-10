#!/usr/bin/env python3
"""
Script pour lancer la synchronisation MapServer via l'API CKAN

Usage:
    python3 sync-mapserver.py [--url URL] [--token TOKEN]

Variables d'environnement:
    CKAN_URL: URL du serveur CKAN (défaut: https://ckan2.qualif-data.example.org)
    CKAN_TOKEN: Token d'authentification CKAN (requis)
"""

import requests
import json
import os
import sys
import argparse
from typing import Dict, Any, Optional

# Couleurs pour l'affichage
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def sync_mapserver(ckan_url: str, ckan_token: str, timeout: int = 600) -> Dict[str, Any]:
    """
    Lance la synchronisation MapServer
    
    Args:
        ckan_url: URL du serveur CKAN
        ckan_token: Token d'authentification CKAN
        timeout: Timeout en secondes (défaut: 600)
        
    Returns:
        Dictionnaire avec les résultats de la synchronisation
    """
    url = f"{ckan_url}/api/action/admin_sync_mapserver"
    headers = {
        "Authorization": ckan_token,
        "Content-Type": "application/json"
    }
    data = {}
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'error': f'Timeout - la synchronisation prend trop de temps (>{timeout}s)'
        }
    except requests.exceptions.HTTPError as e:
        try:
            error_data = response.json()
            return {
                'success': False,
                'error': error_data.get('error', {}).get('message', str(e))
            }
        except Exception:
            return {
                'success': False,
                'error': f'Erreur HTTP {response.status_code}: {str(e)}'
            }
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Erreur de connexion: {str(e)}'
        }

def print_results(result: Dict[str, Any], show_output: bool = True):
    """
    Affiche les résultats de la synchronisation
    
    Args:
        result: Résultat de la synchronisation
        show_output: Afficher la sortie du script (défaut: True)
    """
    if result.get('success'):
        print(f"{Colors.GREEN}{result.get('message', 'Synchronisation réussie')}{Colors.NC}")
        
        success_count = result.get('success_count')
        if success_count is not None and success_count != 0:
            print(f"   {Colors.GREEN}Mapfiles générés: {success_count}{Colors.NC}")
        
        error_count = result.get('error_count')
        if error_count and error_count != 0:
            print(f"   {Colors.YELLOW} Erreurs: {error_count}{Colors.NC}")
        
        # Afficher un extrait de la sortie si disponible
        if show_output and result.get('output'):
            output = result['output']
            if output and output != 'null':
                print(f"\n{Colors.BLUE}Sortie du script (dernières lignes):{Colors.NC}")
                output_lines = output.split('\n')
                for line in output_lines[-20:]:
                    if line.strip():
                        print(f"   {line}")
    else:
        error = result.get('error', 'Erreur inconnue')
        print(f"{Colors.RED}Erreur: {error}{Colors.NC}")
        
        # Afficher un extrait de la sortie si disponible
        if show_output and result.get('output'):
            output = result['output']
            if output and output != 'null':
                print(f"\n{Colors.BLUE}Sortie du script (dernières lignes):{Colors.NC}")
                output_lines = output.split('\n')
                for line in output_lines[-20:]:
                    if line.strip():
                        print(f"   {line}")

def main():
    """Fonction principale"""
    parser = argparse.ArgumentParser(
        description='Lance la synchronisation MapServer via l\'API CKAN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Avec variables d'environnement
  export CKAN_TOKEN="your-token-here"
  python3 sync-mapserver.py

  # Avec options en ligne de commande
  python3 sync-mapserver.py --url https://ckan.example.com --token your-token-here

  # Depuis Docker
  docker exec ckan python3 /path/to/sync-mapserver.py
        """
    )
    
    parser.add_argument(
        '-u', '--url',
        default=os.getenv('CKAN_URL', 'https://ckan2.qualif-data.example.org'),
        help='URL du serveur CKAN (défaut: https://ckan2.qualif-data.example.org ou CKAN_URL)'
    )
    
    parser.add_argument(
        '-t', '--token',
        default=os.getenv('CKAN_TOKEN', ''),
        help='Token d\'authentification CKAN (requis, ou utiliser CKAN_TOKEN)'
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=600,
        help='Timeout en secondes (défaut: 600)'
    )
    
    parser.add_argument(
        '--no-output',
        action='store_true',
        help='Ne pas afficher la sortie du script'
    )
    
    args = parser.parse_args()
    
    # Vérifier que le token est défini
    if not args.token:
        print(f"{Colors.RED}Erreur: CKAN_TOKEN non défini{Colors.NC}")
        print("")
        print("   Définir avec:")
        print("   export CKAN_TOKEN=your-token-here")
        print("")
        print("   Ou utiliser l'option:")
        print("   python3 sync-mapserver.py --token your-token-here")
        sys.exit(1)
    
    # Afficher les informations
    print(f"{Colors.BLUE} Lancement de la synchronisation MapServer...{Colors.NC}")
    print(f"   URL: {args.url}")
    print("")
    
    # Lancer la synchronisation
    result = sync_mapserver(args.url, args.token, timeout=args.timeout)
    
    # Afficher les résultats
    print_results(result, show_output=not args.no_output)
    
    # Code de sortie
    if not result.get('success'):
        sys.exit(1)

if __name__ == '__main__':
    main()



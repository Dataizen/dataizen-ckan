#!/usr/bin/env python3
"""
Script pour charger les licences depuis un fichier JSON dans CKAN.
CKAN stocke les licences dans la base de données, mais les charge depuis le code source par défaut.
Ce script permet de charger des licences personnalisées depuis un fichier JSON.
"""
import json
import sys
import os

# Ajouter le chemin de CKAN au PYTHONPATH
sys.path.insert(0, '/srv/app/src/ckan')

def load_licenses_from_file(licenses_file):
    """Charge les licences depuis un fichier JSON"""
    try:
        with open(licenses_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur lors de la lecture du fichier {licenses_file}: {e}")
        return None

def main():
    licenses_file = os.getenv('CKAN_LICENSES_FILE', '/srv/app/config_files/common/licenses.json')
    
    if not os.path.exists(licenses_file):
        print(f" Fichier de licences non trouvé: {licenses_file}")
        print("CKAN utilisera les licences par défaut")
        return 0
    
    licenses_data = load_licenses_from_file(licenses_file)
    if not licenses_data:
        return 1
    
    print(f"{len(licenses_data)} licences trouvées dans {licenses_file}")
    
    # Afficher la liste des licences
    print("\nLicences disponibles:")
    for license in licenses_data:
        print(f"  - {license.get('id', 'N/A')}: {license.get('title', 'N/A')}")
    
    print("\nFichier de licences chargé avec succès")
    print(f"Pour utiliser ces licences, vous devez configurer CKAN pour les charger")
    print(f"Le fichier est disponible à: {licenses_file}")
    print(f"Vous pouvez éditer ce fichier pour modifier les licences disponibles")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())







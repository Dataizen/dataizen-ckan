#!/usr/bin/env python3
"""
Script pour afficher le contenu de jobs.py de xloader, notamment la fonction _download_resource_data
À exécuter dans le conteneur CKAN
"""

import sys
import os

XLOADER_JOBS_FILE = "/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"

def show_file():
    """Affiche le contenu du fichier jobs.py"""
    if not os.path.exists(XLOADER_JOBS_FILE):
        print(f"Fichier non trouvé: {XLOADER_JOBS_FILE}")
        print("Ce script doit être exécuté dans le conteneur CKAN")
        return
    
    print(f"Fichier: {XLOADER_JOBS_FILE}\n")
    print("=" * 80)
    
    with open(XLOADER_JOBS_FILE, 'r') as f:
        content = f.read()
    
    # Afficher la fonction _download_resource_data si elle existe
    import re
    
    # Chercher la fonction _download_resource_data
    pattern = r'(def _download_resource_data\([^)]+\):.*?)(?=\n\ndef |\nclass |\Z)'
    match = re.search(pattern, content, re.DOTALL)
    
    if match:
        print("Fonction _download_resource_data trouvée:\n")
        print("=" * 80)
        func_body = match.group(1)
        
        # Afficher les lignes avec numéros
        lines = func_body.split('\n')
        start_line = content[:match.start()].count('\n') + 1
        
        for i, line in enumerate(lines, start=start_line):
            print(f"{i:4d} | {line}")
        
        print("=" * 80)
        
        # Vérifier si le patch est déjà appliqué
        if "# Patched by databfc: add Authorization header" in func_body:
            print("\nPatch d'authentification déjà appliqué")
        else:
            print("\n Patch d'authentification NON appliqué")
        
        # Chercher les appels requests.get
        requests_matches = re.finditer(r'(response = requests\.get\([^)]+\))', func_body)
        print("\nAppels requests.get trouvés:")
        for m in requests_matches:
            call = m.group(1)
            if 'headers=' in call:
                print(f"  Avec headers: {call[:100]}...")
            else:
                print(f"   SANS headers: {call[:100]}...")
    else:
        print(" Fonction _download_resource_data non trouvée")
        print("\nContenu complet du fichier (premiers 200 lignes):\n")
        lines = content.split('\n')
        for i, line in enumerate(lines[:200], start=1):
            print(f"{i:4d} | {line}")
        if len(lines) > 200:
            print(f"\n... ({len(lines) - 200} lignes supplémentaires)")

if __name__ == '__main__':
    show_file()






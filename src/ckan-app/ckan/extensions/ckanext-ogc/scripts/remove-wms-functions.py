#!/usr/bin/env python3
"""
Script pour supprimer toutes les fonctions WMS du fichier views.py
"""

import re
import sys

def remove_wms_functions(content):
    """Supprime toutes les fonctions WMS du contenu"""
    
    # Liste des fonctions WMS à supprimer
    wms_functions = [
        r'def _handle_wms_getmap.*?\n(?:.*?\n)*?(?=def |class |\Z)',
        r'def _handle_wms_getfeatureinfo.*?\n(?:.*?\n)*?(?=def |class |\Z)',
        r'def _create_wms_exception.*?\n(?:.*?\n)*?(?=def |class |\Z)',
        r'def _generate_wms_capabilities.*?\n(?:.*?\n)*?(?=def |class |\Z)',
        r'@wms_blueprint\.route.*?\n(?:.*?\n)*?(?=@|def |class |\Z)',
        r'def ogc_wms_proxy.*?\n(?:.*?\n)*?(?=def |class |\Z)',
        r'def ogc_wms_direct_getmap.*?\n(?:.*?\n)*?(?=def |class |\Z)',
    ]
    
    # Supprimer les fonctions une par une
    for pattern in wms_functions:
        content = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)
    
    # Supprimer les commentaires WMS restants
    content = re.sub(r'#.*?WMS.*?\n', '', content, flags=re.IGNORECASE)
    
    return content

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python remove-wms-functions.py <input_file> [output_file]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file + '.cleaned'
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    cleaned = remove_wms_functions(content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(cleaned)
    
    print(f"Fichier nettoyé: {output_file}")
    print(f"   Lignes originales: {len(content.splitlines())}")
    print(f"   Lignes nettoyées: {len(cleaned.splitlines())}")




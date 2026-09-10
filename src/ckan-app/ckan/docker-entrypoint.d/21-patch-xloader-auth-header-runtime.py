#!/usr/bin/env python3
"""
Script informatif : le patch runtime n'est plus nécessaire

La route download autorise maintenant les appels internes avec ignore_auth=True
grâce à la modification de get_auth_functions dans ckanext-dataload-router
"""

import os

def apply_runtime_patch():
    """Vérifie si un patch runtime existe et informe qu'il n'est plus nécessaire"""
    patch_file = '/srv/app/xloader_auth_patch.py'
    
    if os.path.exists(patch_file):
        print(" Un patch runtime précédent a été détecté")
        print("   Ce patch n'est plus nécessaire car la route download autorise maintenant")
        print("   les appels internes avec ignore_auth=True (via ckanext-dataload-router)")
        print("   Le fichier peut être supprimé si souhaité")
    else:
        print("Aucun patch runtime détecté")
        print("   Le patch n'est pas nécessaire : la route download autorise les appels internes")
    
    print("Configuration xloader auth header runtime terminée")
    print("   Les téléchargements internes (xloader) sont autorisés via ignore_auth")
    return True

if __name__ == '__main__':
    apply_runtime_patch()

#!/usr/bin/env python3
"""
Patch pour get_harvester pour charger les harvesters depuis les entry points
si PluginImplementations ne les trouve pas
"""
import sys
import os

HARVESTER_QUEUE = "/srv/app/src/ckanext-harvest/ckanext/harvest/queue.py"

if not os.path.exists(HARVESTER_QUEUE):
    print(f"Fichier non trouvé: {HARVESTER_QUEUE}")
    sys.exit(0)

# Vérifier si le patch a déjà été appliqué
with open(HARVESTER_QUEUE, 'r') as f:
    content = f.read()
    if "# Patch: chargement direct des harvesters depuis entry points" in content:
        print("Patch déjà appliqué")
        sys.exit(0)

# Créer un backup (avec sudo si nécessaire)
import shutil
try:
    shutil.copy(HARVESTER_QUEUE, f"{HARVESTER_QUEUE}.backup")
except PermissionError:
    # Si pas de permission, essayer avec sudo
    import subprocess
    subprocess.run(['sudo', 'cp', HARVESTER_QUEUE, f"{HARVESTER_QUEUE}.backup"], check=False)

# Remplacer la fonction get_harvester
old_get_harvester = """def get_harvester(harvest_source_type):
    for harvester in PluginImplementations(IHarvester):
        if harvester.info()['name'] == harvest_source_type:
            return harvester"""

new_get_harvester = """def get_harvester(harvest_source_type):
    # Essayer d'abord avec PluginImplementations
    for harvester in PluginImplementations(IHarvester):
        if harvester.info()['name'] == harvest_source_type:
            return harvester
    
    # Patch: chargement direct des harvesters depuis entry points
    # si PluginImplementations ne les trouve pas (problème de contexte)
    try:
        import pkg_resources
        # Charger tous les entry points ckan.plugins qui sont des harvesters
        for ep in pkg_resources.iter_entry_points('ckan.plugins'):
            try:
                plugin_class = ep.load()
                # Vérifier si c'est un harvester
                if hasattr(plugin_class, 'info') and IHarvester.providedBy(plugin_class):
                    harvester_instance = plugin_class()
                    info = harvester_instance.info()
                    if info.get('name') == harvest_source_type:
                        return harvester_instance
            except Exception:
                continue
        
        # Fallback: charger directement le harvester CKAN si le type est 'ckan'
        if harvest_source_type == 'ckan':
            try:
                from ckanext.harvest.harvesters import CKANHarvester
                return CKANHarvester()
            except ImportError:
                pass
    except Exception as e:
        log.warning(f"Erreur lors du chargement direct des harvesters: {e}")
    
    return None"""

if old_get_harvester in content:
    content = content.replace(old_get_harvester, new_get_harvester)
    try:
        with open(HARVESTER_QUEUE, 'w') as f:
            f.write(content)
        print("Patch get_harvester appliqué avec succès")
    except PermissionError:
        # Si pas de permission, utiliser sudo
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        subprocess.run(['sudo', 'mv', tmp_path, HARVESTER_QUEUE], check=True)
        subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', HARVESTER_QUEUE], check=False)
        print("Patch get_harvester appliqué avec succès (via sudo)")
else:
    print("Pattern get_harvester non trouvé, patch non appliqué")


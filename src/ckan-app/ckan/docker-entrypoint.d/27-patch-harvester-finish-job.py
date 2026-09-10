#!/usr/bin/env python3
"""
Patch pour finaliser automatiquement le job de harvest quand tous les objets sont terminés
"""
import sys
import os
import re
import shutil

HARVESTER_QUEUE = "/srv/app/src/ckanext-harvest/ckanext/harvest/queue.py"

if not os.path.exists(HARVESTER_QUEUE):
    print(f"Fichier non trouvé: {HARVESTER_QUEUE}")
    sys.exit(0)

# Vérifier si le patch a déjà été appliqué
with open(HARVESTER_QUEUE, 'r') as f:
    content = f.read()
    if "# Patch: finalisation automatique du job quand tous les objets sont terminés" in content:
        print("Patch déjà appliqué")
        sys.exit(0)

# Créer un backup
try:
    shutil.copy(HARVESTER_QUEUE, f"{HARVESTER_QUEUE}.backup")
except PermissionError:
    print(f"Permission refusée pour créer le backup de {HARVESTER_QUEUE}. Tentative avec sudo...")
    os.system(f"sudo cp {HARVESTER_QUEUE} {HARVESTER_QUEUE}.backup")
    if not os.path.exists(f"{HARVESTER_QUEUE}.backup"):
        print(f"Échec de la création du backup même avec sudo. Abandon du patch.")
        sys.exit(1)

# Trouver fetch_callback et ajouter l'import HarvestObject au début si nécessaire
fetch_pos = content.find('def fetch_callback')
if fetch_pos == -1:
    print("fetch_callback non trouvé")
    sys.exit(1)

# Vérifier si HarvestObject est déjà importé au début de la fonction
# Chercher le premier 'try:' dans la fonction
first_try_pos = content.find('    try:', fetch_pos)
if first_try_pos == -1:
    print("Premier try non trouvé dans fetch_callback")
    sys.exit(1)

# Vérifier si l'import existe déjà avant le premier try
if 'from ckanext.harvest.model import HarvestObject' not in content[fetch_pos:first_try_pos]:
    # Ajouter l'import juste avant le premier try
    import_line = '    from ckanext.harvest.model import HarvestObject\n'
    content = content[:first_try_pos] + import_line + content[first_try_pos:]
    print("Import HarvestObject ajouté au début de fetch_callback")

# Trouver la fin de fetch_callback (avant model.Session.remove() et channel.basic_ack)
# On cherche le pattern exact avec les bons espaces
old_code = '''    for harvester in PluginImplementations(IHarvester):
        if harvester.info()['name'] == obj.source.type:
            fetch_and_import_stages(harvester, obj)

    model.Session.remove()
    channel.basic_ack(method.delivery_tag)'''

new_code = '''    for harvester in PluginImplementations(IHarvester):
        if harvester.info()['name'] == obj.source.type:
            fetch_and_import_stages(harvester, obj)

    # Patch: finalisation automatique du job quand tous les objets sont terminés
    # Vérifier si tous les objets du job sont terminés (COMPLETE ou ERROR)
    # Note: HarvestObject doit être importé au début de la fonction, pas ici
    # IMPORTANT: Ne finaliser que si gather_finished est défini (gather terminé)
    # et qu'il n'y a plus d'objets en attente (ni current=true ni en queue)
    try:
        job = HarvestJob.get(obj.harvest_job_id)
        # Ne vérifier la finalisation que si le gather est terminé
        if job and job.gather_finished and job.status == 'Running' and not job.finished:
            # Compter TOUS les objets qui sont encore en cours de traitement
            # (pas seulement current=true, car les objets peuvent être retraités)
            pending_objects = model.Session.query(HarvestObject).filter_by(
                harvest_job_id=obj.harvest_job_id
            ).filter(
                HarvestObject.state.in_(['GATHER', 'FETCH', 'IMPORT', 'WAITING'])
            ).count()
            
            # Ne finaliser que si vraiment aucun objet n'est en attente
            # (on ne compte pas les ERROR car ils peuvent être retraités)
            if pending_objects == 0:
                # Vérifier une dernière fois après un court délai pour éviter les race conditions
                # Cela permet de s'assurer que tous les objets en queue ont été traités
                import time
                time.sleep(1)  # Attendre 1 seconde
                
                # Re-vérifier après le délai
                final_check = model.Session.query(HarvestObject).filter_by(
                    harvest_job_id=obj.harvest_job_id
                ).filter(
                    HarvestObject.state.in_(['GATHER', 'FETCH', 'IMPORT', 'WAITING'])
                ).count()
                
                if final_check == 0:
                    # Tous les objets sont vraiment terminés, finaliser le job
                    job.status = 'Finished'
                    import datetime
                    job.finished = datetime.datetime.utcnow()
                    job.save()
                    log.info('Job {0} marked as Finished - all objects processed (pending: {1})'.format(job.id, pending_objects))
    except Exception as e:
        # Ne pas bloquer le traitement si la vérification échoue
        log.warning('Error checking job completion status: {0}'.format(str(e)))

    model.Session.remove()
    channel.basic_ack(method.delivery_tag)'''

if old_code in content:
    new_content = content.replace(old_code, new_code)
    try:
        with open(HARVESTER_QUEUE, 'w') as f:
            f.write(new_content)
        print("Patch de finalisation automatique appliqué avec succès")
    except PermissionError:
        print(f"Permission refusée pour écrire dans {HARVESTER_QUEUE}. Tentative avec sudo...")
        # Utiliser une approche différente avec sudo
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        os.system(f"sudo mv {tmp_path} {HARVESTER_QUEUE}")
        # Re-vérifier si le patch a été appliqué
        with open(HARVESTER_QUEUE, 'r') as f_check:
            if "# Patch: finalisation automatique du job quand tous les objets sont terminés" in f_check.read():
                print("Patch de finalisation automatique appliqué avec succès (via sudo)")
            else:
                print("Échec de l'application du patch même avec sudo.")
                sys.exit(1)
else:
    print("Code non trouvé dans fetch_callback, patch non appliqué")
    print("Le code peut avoir changé. Vérifiez manuellement.")
    # Afficher un extrait pour debug
    fetch_pos = content.find('def fetch_callback')
    if fetch_pos != -1:
        print("\nExtrait autour de fetch_callback:")
        print(content[fetch_pos:fetch_pos+2000][-500:])
    sys.exit(1)


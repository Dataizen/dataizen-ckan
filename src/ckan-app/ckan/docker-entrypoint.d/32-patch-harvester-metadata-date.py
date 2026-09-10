#!/usr/bin/env python3
"""
Patch pour corriger l'erreur TypeError lors de la comparaison de metadata_modified_date
"""
import sys
import os
import re
import shutil

SPATIAL_BASE = "/srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py"

if not os.path.exists(SPATIAL_BASE):
    print(f"Fichier non trouvé: {SPATIAL_BASE}")
    sys.exit(0)

# Vérifier si le patch a déjà été appliqué et si l'indentation est correcte
with open(SPATIAL_BASE, 'r') as f:
    content = f.read()
    if "# Patch: vérification de None pour metadata_modified_date" in content:
        # Vérifier si l'indentation est correcte (la ligne doit être indentée après elif)
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if "# Patch: vérification de None pour metadata_modified_date" in line:
                # Vérifier la ligne suivante (l'if)
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    # Si la ligne suivante commence au début (pas d'indentation), c'est une erreur
                    if next_line.strip().startswith('if not self.force_import') and not next_line.startswith('        '):
                        print("Patch détecté mais avec mauvaise indentation, correction...")
                        # Le patch sera réappliqué avec la bonne indentation
                        break
                else:
                    print("Patch déjà appliqué")
                    sys.exit(0)
        else:
            print("Patch déjà appliqué")
            sys.exit(0)

# Créer un backup
try:
    shutil.copy(SPATIAL_BASE, f"{SPATIAL_BASE}.backup")
except PermissionError:
    print(f"Permission refusée pour créer le backup de {SPATIAL_BASE}. Tentative avec sudo...")
    os.system(f"sudo cp {SPATIAL_BASE} {SPATIAL_BASE}.backup")
    if not os.path.exists(f"{SPATIAL_BASE}.backup"):
        print(f"Échec de la création du backup même avec sudo. Abandon du patch.")
        sys.exit(1)

# Chercher d'abord si le patch a été mal appliqué (sans indentation correcte)
# Le patch mal appliqué a le commentaire et l'if sans indentation au début de la ligne
lines = content.split('\n')
fixed_indentation = False
for i, line in enumerate(lines):
    if "# Patch: vérification de None pour metadata_modified_date" in line and not line.startswith(' '):
        # Chercher la ligne précédente (elif) pour obtenir l'indentation
        if i > 0:
            prev_line = lines[i-1]
            if prev_line.strip().startswith('elif'):
                # L'indentation doit être la même que le elif
                indent_match = re.match(r'^(\s+)', prev_line)
                if indent_match:
                    correct_indent = indent_match.group(1)
                    # Corriger les lignes suivantes
                    if i + 1 < len(lines):
                        # Corriger la ligne if
                        if lines[i+1].strip().startswith('if not self.force_import'):
                            print("Patch détecté avec mauvaise indentation, correction...")
                            lines[i] = correct_indent + "# Patch: vérification de None pour metadata_modified_date"
                            lines[i+1] = correct_indent + lines[i+1].strip()
                            content = '\n'.join(lines)
                            fixed_indentation = True
                            print(f"Indentation corrigée (indentation: {len(correct_indent)} espaces)")
                            # Sauvegarder la correction
                            try:
                                with open(SPATIAL_BASE, 'w') as f:
                                    f.write(content)
                            except PermissionError:
                                import tempfile
                                import subprocess
                                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as tmp_file:
                                    tmp_file.write(content)
                                    tmp_path = tmp_file.name
                                subprocess.run(['sudo', 'mv', tmp_path, SPATIAL_BASE], check=True)
                                subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', SPATIAL_BASE], check=False)
                            break

if fixed_indentation:
    # Le fichier a été corrigé, on peut sortir
    sys.exit(0)

# Chercher le pattern original non patché
# Capturer l'indentation au début de la ligne pour la préserver
old_pattern = r'^(\s+)(if not self\.force_import and previous_object and harvest_object\.metadata_modified_date <= previous_object\.metadata_modified_date:)'

new_pattern = r'''\1# Patch: vérification de None pour metadata_modified_date
\1if not self.force_import and previous_object and harvest_object.metadata_modified_date is not None and previous_object.metadata_modified_date is not None and harvest_object.metadata_modified_date <= previous_object.metadata_modified_date:'''

if re.search(old_pattern, content, re.MULTILINE):
    content = re.sub(old_pattern, new_pattern, content, flags=re.MULTILINE)
    try:
        with open(SPATIAL_BASE, 'w') as f:
            f.write(content)
        print("Patch metadata_modified_date appliqué avec succès")
    except PermissionError:
        print(f"Permission refusée pour écrire dans {SPATIAL_BASE}. Tentative avec sudo...")
        import tempfile
        import subprocess
        # Créer le fichier tmp dans un répertoire accessible
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name
        try:
            subprocess.run(['sudo', 'mv', tmp_path, SPATIAL_BASE], check=True)
            subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', SPATIAL_BASE], check=False)
        except subprocess.CalledProcessError:
            # Nettoyer le fichier tmp en cas d'erreur
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        # Re-vérifier si le patch a été appliqué
        with open(SPATIAL_BASE, 'r') as f_check:
            if "# Patch: vérification de None pour metadata_modified_date" in f_check.read():
                print("Patch metadata_modified_date appliqué avec succès (via sudo)")
            else:
                print("Échec de l'application du patch même avec sudo.")
                sys.exit(1)
else:
    print("Pattern metadata_modified_date non trouvé, patch non appliqué")
    print("Le fichier peut avoir une structure différente")
    sys.exit(0)



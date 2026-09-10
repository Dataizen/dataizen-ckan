#!/usr/bin/env python3
"""
Patch pour corriger l'erreur de compatibilité Python 3.12 dans ckan_pycsw.py
Le problème: ConfigParser.get() a changé de signature en Python 3.12
"""
import sys
import os
import re
import shutil
import subprocess

# Le script ckan_pycsw.py est dans ckanext-spatial
PYCSW_SCRIPT = "/srv/app/src/ckanext-spatial/bin/ckan_pycsw.py"

if not os.path.exists(PYCSW_SCRIPT):
    print(f"Script non trouvé: {PYCSW_SCRIPT}")
    sys.exit(0)

# Vérifier si le patch a déjà été appliqué
with open(PYCSW_SCRIPT, 'r') as f:
    content = f.read()
    # Vérifier si le patch est déjà appliqué
    if 'fallback=' in content and 'pycsw_config.get(' in content:
        # Vérifier que c'est bien le bon pattern
        if re.search(r'\.get\([^)]+,\s*fallback=', content):
            print("Patch pycsw ConfigParser déjà appliqué")
            sys.exit(0)

# Créer un backup
try:
    shutil.copy(PYCSW_SCRIPT, f"{PYCSW_SCRIPT}.backup")
except PermissionError:
    print(f"Permission refusée pour créer le backup de {PYCSW_SCRIPT}. Tentative avec sudo...")
    os.system(f"sudo cp {PYCSW_SCRIPT} {PYCSW_SCRIPT}.backup")
    if not os.path.exists(f"{PYCSW_SCRIPT}.backup"):
        print(f"Échec de la création du backup même avec sudo. Abandon du patch.")
        sys.exit(1)

# Pattern à rechercher: pycsw_config.get("repository", "table", "records")
# En Python 3.12, il faut utiliser: pycsw_config.get("repository", "table", fallback="records")
old_pattern = r'pycsw_config\.get\((["\'])(repository)\1,\s*(["\'])(table)\3,\s*(["\'])(records)\5\)'
new_pattern = r'pycsw_config.get(\1\2\1, \3\4\3, fallback=\5\6\5)'

# Appliquer le patch
pattern_found = False
if re.search(old_pattern, content):
    content = re.sub(old_pattern, new_pattern, content)
    pattern_found = True
    print("Pattern 1 trouvé et appliqué")
else:
    # Essayer un pattern plus flexible
    old_pattern2 = r'pycsw_config\.get\((["\'])(repository)\1,\s*(["\'])(table)\3,\s*(["\'])(\w+)\5\)'
    if re.search(old_pattern2, content):
        def replace_func(match):
            quote1 = match.group(1)
            quote2 = match.group(3)
            quote3 = match.group(5)
            default_value = match.group(6)
            return f'pycsw_config.get({quote1}repository{quote1}, {quote2}table{quote2}, fallback={quote3}{default_value}{quote3})'
        content = re.sub(old_pattern2, replace_func, content)
        pattern_found = True
        print("Pattern 2 trouvé et appliqué")
    else:
        # Essayer un pattern encore plus flexible (sans guillemets spécifiques)
        old_pattern3 = r'(\w+)\.get\((["\'])(repository)\2,\s*(["\'])(table)\4,\s*(["\'])(\w+)\6\)'
        if re.search(old_pattern3, content):
            def replace_func(match):
                config_var = match.group(1)
                quote1 = match.group(2)
                quote2 = match.group(4)
                quote3 = match.group(6)
                default_value = match.group(7)
                return f'{config_var}.get({quote1}repository{quote1}, {quote2}table{quote2}, fallback={quote3}{default_value}{quote3})'
            content = re.sub(old_pattern3, replace_func, content)
            pattern_found = True
            print("Pattern 3 trouvé et appliqué")

# Corriger aussi SafeConfigParser -> ConfigParser
if 'SafeConfigParser' in content:
    content = content.replace('SafeConfigParser', 'ConfigParser')
    print("SafeConfigParser remplacé par ConfigParser")

if pattern_found or 'SafeConfigParser' in content:
    # Sauvegarder le fichier modifié dans /tmp pour éviter les problèmes de permissions
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(prefix='ckan_pycsw.py.', suffix='.tmp', dir='/tmp')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            f.write(content)
        # Utiliser sudo pour déplacer et changer le propriétaire
        subprocess.run(['sudo', 'mv', tmp_path, PYCSW_SCRIPT], check=True)
        subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', PYCSW_SCRIPT], check=False)
        # Vérifier que le patch a été appliqué
        with open(PYCSW_SCRIPT, 'r') as f_check:
            check_content = f_check.read()
            if 'fallback=' in check_content or 'ConfigParser' in check_content:
                print("Patch pycsw ConfigParser appliqué avec succès")
            else:
                print("Échec de l'application du patch.")
                sys.exit(1)
    except (PermissionError, subprocess.CalledProcessError) as e:
        print(f"Erreur lors de l'application du patch: {e}")
        # Essayer de nettoyer le fichier temporaire
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        # Ne pas faire sys.exit(1) pour permettre au conteneur de démarrer même si le patch échoue
        print("Continuation malgré l'échec du patch (sera réessayé au prochain démarrage)")
else:
    print("Pattern pycsw_config.get() non trouvé, patch non appliqué")
    print("Le fichier peut avoir une structure différente")
    # Afficher quelques lignes autour de la ligne 66 pour aider au diagnostic
    try:
        with open(PYCSW_SCRIPT, 'r') as f:
            lines = f.readlines()
            if len(lines) >= 66:
                print("Ligne 66 (zone de l'erreur):")
                start = max(0, 60)
                end = min(len(lines), 72)
                for i in range(start, end):
                    marker = ">>>" if i == 65 else "   "
                    print(f"{marker} {i+1}: {lines[i].rstrip()}")
    except Exception as e:
        print(f"Erreur lors du diagnostic: {e}")
    print("\nLe patch pycsw ne peut pas être appliqué automatiquement.")
    print("Les erreurs de synchronisation CSW continueront d'apparaître dans les logs.")
    sys.exit(0)








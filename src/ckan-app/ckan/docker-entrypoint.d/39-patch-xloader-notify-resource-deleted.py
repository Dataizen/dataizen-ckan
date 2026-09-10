#!/usr/bin/env python3
"""
Patch xloader: dans notify(), ignorer NotFound quand on appelle resource_show
(ressource en cours de suppression ou déjà détachée du package).
Évite l'ERROR "Resource ... exists but it is not found in the package" lors de la suppression d'une ressource.
"""
import os
import re
import sys

XLOADER_PLUGIN_CANDIDATES = [
    "/srv/app/src/ckanext-xloader/ckanext/xloader/plugin.py",
    "/usr/local/lib/python3.10/site-packages/ckanext/xloader/plugin.py",
    "/usr/local/lib/python3.11/site-packages/ckanext/xloader/plugin.py",
    "/usr/local/lib/python3.13/site-packages/ckanext/xloader/plugin.py",
]

MARKER = "# Patch: ignore NotFound in notify when resource was deleted"

def main():
    path = None
    for p in XLOADER_PLUGIN_CANDIDATES:
        if os.path.exists(p):
            path = p
            break
    if not path:
        print("ckanext-xloader plugin.py non trouvé (patch notify ignoré)")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Patch xloader notify (resource deleted) déjà appliqué")
        return 0

    # Pattern: resource_dict = toolkit.get_action("resource_show")( ... ); on peut avoir plusieurs lignes jusqu'à "),\n )" ou ")\n"
    # On insère "try:" juste avant "resource_dict = toolkit.get_action" et après la ligne de fermeture ")," on ajoute except.
    pattern = re.compile(
        r'(\s+)(context\s*=\s*\{\s*["\']ignore_auth["\']\s*:\s*True,?\s*\}\s*\n'
        r'\s+resource_dict\s*=\s*toolkit\.get_action\s*\(\s*["\']resource_show["\']\s*\)\s*\(\s*\n'
        r'\s+context\s*,\s*\n'
        r'\s+\{\s*\n'
        r'\s+["\']id["\']\s*:\s*entity\.id\s*,?\s*\n'
        r'\s+\}\s*,?\s*\n'
        r'\s+\)\s*)',
        re.MULTILINE | re.DOTALL
    )
    match = pattern.search(content)
    if not match:
        # Version plus souple
        pattern2 = re.compile(
            r'(\s*)(resource_dict\s*=\s*toolkit\.get_action\s*\(\s*["\']resource_show["\']\s*\)\s*\(\s*\n'
            r'(?:\s*context\s*,?\s*\n)?'
            r'\s*\{\s*["\']id["\']\s*:\s*entity\.id\s*\}.*?\)\s*)',
            re.MULTILINE | re.DOTALL
        )
        match = pattern2.search(content)

    if not match:
        print("Bloc resource_show non trouvé dans xloader plugin (version différente?)")
        return 0

    indent = match.group(1) or " "
    block = match.group(2 if match.lastindex >= 2 else 1)
    # Remplacer par version avec try/except (NotFound = ressource supprimée)
    # Capturer toolkit.ObjectNotFound ET ckan.logic.NotFound (levée par resource_show)
    except_block = (
        indent + "except Exception as _e:\n"
        + indent + "    if _e.__class__.__name__ == 'NotFound' or (getattr(toolkit, 'ObjectNotFound', None) and isinstance(_e, toolkit.ObjectNotFound)):\n"
        + indent + "        return  # " + MARKER + "\n"
        + indent + "    raise\n"
    )
    new_block = indent + "try:\n" + block + "\n" + except_block
    content = content.replace(match.group(0), new_block, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patch xloader notify (resource deleted) appliqué")
    except PermissionError:
        print("Permission denied: impossible d'écrire dans", path)
        print("   Le patch xloader (suppression ressource) ne sera pas actif.")
        print("   Pour l'activer: appliquer le patch au build ou monter le volume en écriture.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

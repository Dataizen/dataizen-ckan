#!/usr/bin/env python3
"""
Patch CKAN API pour accepter les paramètres en query string (POST avec ?id=xxx).
Compatible Drupal et autres clients qui envoient POST avec params dans l'URL.
Le body JSON garde la priorité ; les params de l'URL complètent les champs manquants.
"""
import sys
import os
import re
import shutil

# Trouver api.py : env override, import ou chemins connus
API_FILE = os.environ.get("CKAN_API_PATCH_FILE")
if not API_FILE:
    try:
        import ckan.views.api as api_module
        API_FILE = api_module.__file__
    except Exception:
        pass
if not API_FILE:
    import importlib.util
    spec = importlib.util.find_spec("ckan.views.api")
    API_FILE = spec.origin if spec and spec.origin else None
if not API_FILE or not os.path.exists(API_FILE):
    for path in ["/srv/app/src/ckan/ckan/views/api.py"]:
        if os.path.exists(path):
            API_FILE = path
            break
if not API_FILE:
    print("Module ckan.views.api non trouvé")
    sys.exit(0)

with open(API_FILE, 'r') as f:
    content = f.read()

if "# Patch: merge query string params for compatibility" in content:
    print("Patch API déjà appliqué")
    sys.exit(0)

# CKAN 2.11 utilise 4 espaces (base) + 4 pour le corps du for
# Essayer plusieurs patterns : (base_indent, body_indent)
def do_patch(content):
    variants = [
        ("    ", "        "),  # CKAN 2.11 standard : 4 + 4 pour le corps du for
        (" ", " "),            # 1 espace partout (anciennes versions)
        ("    ", "    "),      # 4 espaces partout
    ]
    for base, body in variants:
        old = (
            f"{base}for field_name, file_ in request.files.items():\n"
            f"{body}request_data[field_name] = file_\n"
            f"{base}log.debug(u'Request data extracted: %r', request_data)\n"
            f"\n"
            f"{base}return request_data"
        )
        if old in content:
            inner = base + "    " if len(base) < 4 else base + "    "
            new = (
                f"{base}for field_name, file_ in request.files.items():\n"
                f"{body}request_data[field_name] = file_\n"
                f"\n"
                f"{base}# Patch: merge query string params for compatibility (e.g. Drupal POST with ?id=)\n"
                f"{base}# Body/form takes precedence; params from request.args fill in missing keys\n"
                f"{base}if request.args:\n"
                f"{inner}args_mixed = mixed(request.args)\n"
                f"{inner}for key, value in args_mixed.items():\n"
                f"{inner}    if key not in request_data or request_data.get(key) in (None, '', [], {{}}):\n"
                f"{inner}        request_data[key] = value\n"
                f"\n"
                f"{base}log.debug(u'Request data extracted: %r', request_data)\n"
                f"\n"
                f"{base}return request_data"
            )
            return content.replace(old, new)
    return None

new_content = do_patch(content)
if new_content is None:
    print("Pattern non trouvé dans ckan/views/api.py")
    sys.exit(1)

# Backup
try:
    shutil.copy(API_FILE, f"{API_FILE}.backup")
except PermissionError:
    os.system(f"sudo cp {API_FILE} {API_FILE}.backup")

try:
    with open(API_FILE, 'w') as f:
        f.write(new_content)
    print("Patch API query params appliqué avec succès")
except PermissionError:
    import tempfile
    import subprocess
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as tmp:
        tmp.write(new_content)
        tmp_path = tmp.name
    subprocess.run(['sudo', 'mv', tmp_path, API_FILE], check=True)
    subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', API_FILE], check=False)
    print("Patch API query params appliqué avec succès (via sudo)")

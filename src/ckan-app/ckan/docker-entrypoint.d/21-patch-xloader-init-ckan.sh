#!/bin/bash
set -euo pipefail

echo "Patch xloader_data_into_datastore: bind SQLAlchemy in job context (no make_app)"

XLOADER_JOBS_FILE="/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"

if [ ! -f "$XLOADER_JOBS_FILE" ]; then
  echo "Not found: $XLOADER_JOBS_FILE (skip)"
  exit 0
fi

python3 - <<'PY'
import re, sys, tempfile, os

path = "/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

marker = "# Patched by databfc: ensure CKAN SQLAlchemy is bound for RQ jobs (no make_app)"
if marker in content:
    print("Patch already present")
    sys.exit(0)

m = re.search(r'^(def\s+xloader_data_into_datastore\s*\([^\)]*\)\s*:\s*)\n', content, flags=re.M)
if not m:
    print("Function xloader_data_into_datastore not found")
    sys.exit(1)

insert = f"""{marker}
    import os
    import logging
    from configparser import ConfigParser
    from sqlalchemy import create_engine
    log = logging.getLogger(__name__)

    def _ensure_sqlalchemy_bound():
        from ckan import model
        if model.Session.bind is not None:
            return

        ckan_ini_path = os.environ.get("CKAN_INI", "/srv/app/ckan.ini")

        cp = ConfigParser()
        if not cp.read(ckan_ini_path):
            raise RuntimeError("Cannot read CKAN ini file: " + ckan_ini_path)
        if not cp.has_section("app:main"):
            raise RuntimeError("Missing [app:main] section in " + ckan_ini_path)

        sqlalchemy_url = cp.get("app:main", "sqlalchemy.url", fallback=None)
        if not sqlalchemy_url:
            raise RuntimeError("sqlalchemy.url not found in [app:main]")

        log.warning("[xloader] Binding CKAN model.Session to %s (job context)", sqlalchemy_url)

        engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
        model.meta.engine = engine
        model.Session.bind = engine
        if hasattr(model.meta, "Session"):
            model.meta.Session.bind = engine

        if model.Session.bind is None:
            raise RuntimeError("Bind failed: model.Session.bind is still None")

    _ensure_sqlalchemy_bound()

"""

sig = m.group(1) + "\n"
new_content = content.replace(sig, sig + insert, 1)

fd, tmp = tempfile.mkstemp(prefix="jobs.py.", suffix=".tmp")
os.close(fd)
with open(tmp, "w", encoding="utf-8") as f:
    f.write(new_content)

# atomic-ish replace with sudo
import subprocess
result = subprocess.run(['sudo', 'cp', tmp, path], 
                      capture_output=True, text=True, timeout=10)
os.unlink(tmp)

if result.returncode == 0:
    print("Patch applied")
else:
    print(f"Erreur sudo: {result.stderr[:200]}")
    sys.exit(1)
PY

# Important: permissions readable by runtime user (euid 503)
# Utiliser sudo si nécessaire
chmod 644 "$XLOADER_JOBS_FILE" 2>/dev/null || sudo chmod 644 "$XLOADER_JOBS_FILE" 2>/dev/null || echo " Impossible de chmod jobs.py"
chmod 755 "$(dirname "$XLOADER_JOBS_FILE")" 2>/dev/null || sudo chmod 755 "$(dirname "$XLOADER_JOBS_FILE")" 2>/dev/null || echo " Impossible de chmod répertoire parent"

echo "Patch done: $(ls -la "$XLOADER_JOBS_FILE")"
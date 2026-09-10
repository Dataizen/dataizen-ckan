#!/bin/bash
# /docker-entrypoint.d/22-patch-ckan-cli-jobs-prefix.sh
# Idempotent, non-bloquant.
# Patch CKAN CLI jobs worker to:
# - pass explicit Redis connection
# - prefix queue names (rq keys) using ckan.site_id

set +e
echo "[dataizen] Patching CKAN CLI jobs worker: prefix queue names + explicit Redis connection..."

CLI_FILE="/srv/app/src/ckan/ckan/cli/jobs.py"
if [ ! -f "$CLI_FILE" ]; then
  echo " $CLI_FILE not found, skip"
  exit 0
fi

timeout 12 python3 - <<'PY' 2>&1 || echo " Patch cli/jobs.py (prefix) non appliqué (timeout/erreur), continuons..."
import os, re, sys, tempfile, shutil, subprocess

FILE = "/srv/app/src/ckan/ckan/cli/jobs.py"
MARKER = "# Patched by databfc: cli-worker-prefix-queues + explicit-redis v1"

def py_compile_or_fail(path: str):
    r = subprocess.run([sys.executable, "-m", "py_compile", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("py_compile failed:\n" + (r.stderr or r.stdout or ""))

def write_atomic(dst: str, content: str):
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(prefix="cli_jobs_patch_", suffix=".py", dir=d)
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        py_compile_or_fail(tmp)
        bak = dst + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(dst, bak)
        shutil.move(tmp, dst)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass

src = open(FILE, "r", encoding="utf-8").read()
if MARKER in src:
    print("Patch déjà appliqué")
    raise SystemExit(0)

# Replace the whole patched block if present (your v1 marker),
# otherwise patch the worker call line.
# We target from "# Patched by databfc: cli-worker-pass-explicit-redis-connection v1" up to the Worker call block end.
block_pat = r"(?ms)^[ \t]*# Patched by databfc: cli-worker-pass-explicit-redis-connection v1.*?^[ \t]*bg_jobs\.Worker\(queues\)\.work\(burst=burst\)\s*$"
m = re.search(block_pat, src)
if not m:
    # fallback: patch the simple call line
    call_pat = r"(?m)^(?P<indent>[ \t]*)bg_jobs\.Worker\(\s*queues\s*\)\.work\(\s*burst\s*=\s*burst\s*\)\s*$"
    m2 = re.search(call_pat, src)
    if not m2:
        print(" Target not found, skip")
        raise SystemExit(0)
    indent = m2.group("indent")
    start, end = m2.start(), m2.end()
else:
    # detect indentation from the start of the block
    line_start = src.rfind("\n", 0, m.start()) + 1
    indent = re.match(r"[ \t]*", src[line_start:m.start()+1]).group(0)
    start, end = m.start(), m.end()

replacement = (
    f"{indent}{MARKER}\n"
    f"{indent}# Force explicit Redis connection for RQ Worker\n"
    f"{indent}try:\n"
    f"{indent}    conn = bg_jobs._connect()  # type: ignore[attr-defined]\n"
    f"{indent}except Exception:\n"
    f"{indent}    conn = None\n"
    f"{indent}if conn is None:\n"
    f"{indent}    try:\n"
    f"{indent}        from ckan.lib.redis import connect_to_redis\n"
    f"{indent}        from rq.connections import push_connection\n"
    f"{indent}        conn = connect_to_redis()\n"
    f"{indent}        push_connection(conn)\n"
    f"{indent}    except Exception:\n"
    f"{indent}        conn = None\n"
    f"\n"
    f"{indent}# IMPORTANT: CKAN prefixes RQ queue names with ckan:<site_id>:\n"
    f"{indent}# CLI args are unprefixed ('default'), so we must prefix them for RQ keys.\n"
    f"{indent}qnames = list(queues) if queues else [bg_jobs.DEFAULT_QUEUE_NAME]\n"
    f"{indent}qnames = [bg_jobs.add_queue_name_prefix(q) for q in qnames]\n"
    f"\n"
    f"{indent}if conn is not None:\n"
    f"{indent}    bg_jobs.Worker(qnames, connection=conn).work(burst=burst)\n"
    f"{indent}else:\n"
    f"{indent}    bg_jobs.Worker(qnames).work(burst=burst)\n"
)

out = src[:start] + replacement + src[end:]
write_atomic(FILE, out)
print("Patch appliqué :", FILE)
PY

echo "[dataizen] Patch terminé"
exit 0
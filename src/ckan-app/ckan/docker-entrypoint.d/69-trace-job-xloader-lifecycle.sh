#!/bin/bash
# /docker-entrypoint.d/20-patch-rq.sh
# Idempotent, non-bloquant.
# Patch CKAN Worker class (ckan/lib/jobs.py):
# - Accept queue NAMES (strings) and convert to rq.Queue with a real Redis connection
# - Force self.connection if None (fix rq.Worker connection_pool crash)
# - Disable RQ intermediate move (avoid BLMOVE -> intermediate) via BLPOP dequeue
# - Trace execute_job
# - Anti-suicide loop: on StopRequested, re-register_birth + subscribe and continue

set +e
echo "[dataizen] Patching CKAN RQ worker (queue strings -> rq.Queue + force connection + disable intermediate + trace + StopRequested recovery)..."

JOBS_FILE="/srv/app/src/ckan/ckan/lib/jobs.py"
if [ ! -f "$JOBS_FILE" ]; then
  echo " $JOBS_FILE not found, skip"
  exit 0
fi

timeout 18 python3 - <<'PY' 2>&1 || echo " Patch jobs.py non appliqué (timeout/erreur), continuons..."
import os, re, sys, tempfile, shutil, subprocess, textwrap

FILE = "/srv/app/src/ckan/ckan/lib/jobs.py"
MARKER = "# Patched by databfc: rq-queue-strings+force-conn+no-intermediate+trace+stoprequested v4"

def py_compile_or_fail(path: str):
    r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("py_compile failed:\n" + (r.stderr or r.stdout or ""))

def write_atomic(dst: str, content: str):
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(prefix="jobs_patch_", suffix=".py", dir=d)
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

m_class = re.search(r"^class\s+Worker\s*\(.*?\)\s*:\s*$", src, flags=re.M)
if not m_class:
    print(" class Worker(...) not found, skip")
    raise SystemExit(0)

start = m_class.end()
m_first_def = re.search(r"^\s{4}def\s+\w+\s*\(", src[start:], flags=re.M)
if not m_first_def:
    print(" No methods in Worker class, skip")
    raise SystemExit(0)

insert_pos = start + m_first_def.start()

patch = textwrap.dedent(f"""
    {MARKER}

    def _rqtrace(self, msg: str, *a):
        import os as _os
        if _os.environ.get("CKAN_RQ_TRACE_EXECUTE_JOB") != "1" and _os.environ.get("CKAN_RQ_TRACE_TEARDOWN") != "1":
            return
        try:
            log.error("[RQ-TRACE] " + msg, *a)
        except Exception:
            pass

    def _redis_from_ini(self):
        \"\"\"Last-resort Redis connection builder without CKAN config init.\"\"\"
        import os as _os
        try:
            import redis as _redis
        except Exception:
            return None

        ini = _os.environ.get("CKAN_INI") or _os.environ.get("CKAN_CONFIG") or "/srv/app/ckan.ini"
        try:
            import configparser as _cp
            cfg = _cp.ConfigParser()
            cfg.read(ini)
            url = None
            if cfg.has_section("app:main"):
                url = cfg.get("app:main", "ckan.redis.url", fallback=None)
            if not url:
                url = _os.environ.get("CKAN_REDIS_URL")
            if not url:
                return None
            return _redis.from_url(url)
        except Exception:
            return None

    def __init__(self, queues, *args, **kwargs):
        \"\"\"Accept queue names (strings) OR rq.Queue objects, always ensuring a real Redis connection.

        Fixes crashes like:
          AttributeError: 'NoneType' object has no attribute 'connection_pool'
        when rq.Worker receives queue names but no current connection.
        \"\"\"
        import os as _os
        import rq as _rq

        # Normalize queues input
        if queues is None:
            queues = []
        if not isinstance(queues, (list, tuple)):
            queues = [queues]

        # Try normal CKAN connection first, fallback to ini parsing
        conn = kwargs.get("connection")
        if conn is None:
            try:
                conn = _connect()  # CKAN jobs.py helper (uses connect_to_redis + push_connection)
            except Exception:
                conn = None
        if conn is None:
            conn = self._redis_from_ini()
            # If we built a connection ourselves, still try to register it as "current" in rq
            try:
                from rq.connections import push_connection as _push
                _push(conn)
            except Exception:
                pass

        # Convert queue names to rq.Queue objects with connection
        fixed = []
        for q in queues:
            if isinstance(q, str):
                # CKAN expects unprefixed names here ("default")
                try:
                    fullname = add_queue_name_prefix(q)
                except Exception:
                    fullname = q
                fixed.append(_rq.Queue(fullname, connection=conn))
            else:
                fixed.append(q)

        # Ensure rq.Worker gets a real connection
        if kwargs.get("connection") is None and conn is not None:
            kwargs["connection"] = conn

        return super(Worker, self).__init__(fixed, *args, **kwargs)

    def dequeue_job(self, timeout=None):
        \"\"\"Disable RQ 'intermediate' move (BLMOVE) by doing a BLPOP on queue keys.
        Enable with CKAN_RQ_DISABLE_INTERMEDIATE=1 (default).
        \"\"\"
        import os as _os
        if _os.environ.get("CKAN_RQ_DISABLE_INTERMEDIATE", "1") != "1":
            return super(Worker, self).dequeue_job(timeout=timeout)

        try:
            from rq.job import Job as _Job
        except Exception:
            return None

        conn = getattr(self, "connection", None)
        if conn is None:
            conn = self._redis_from_ini()
            try:
                self.connection = conn
            except Exception:
                pass
        if conn is None:
            return None

        try:
            qkeys = [qq.key for qq in getattr(self, "queues", []) if getattr(qq, "key", None)]
        except Exception:
            qkeys = []

        if not qkeys:
            return None

        try:
            res = conn.blpop(qkeys, timeout=timeout)
        except Exception:
            return None

        if not res:
            return None

        _key, job_id = res
        try:
            if isinstance(job_id, bytes):
                job_id = job_id.decode("utf-8")
        except Exception:
            pass

        try:
            return _Job.fetch(job_id, connection=conn)
        except Exception:
            return None

    def execute_job(self, job, queue):
        import traceback as _traceback
        self._rqtrace("execute_job enter job_id=%s origin=%s desc=%s",
                      getattr(job, "id", None), getattr(job, "origin", None), getattr(job, "description", None))
        try:
            return super(Worker, self).execute_job(job, queue)
        except Exception as e:
            self._rqtrace("execute_job exception job_id=%s err=%r\\n%s",
                          getattr(job, "id", None), e, _traceback.format_exc())
            raise
        finally:
            self._rqtrace("execute_job exit job_id=%s", getattr(job, "id", None))

    def work(self, *args, **kwargs):
        import os as _os
        import time as _time
        from rq.exceptions import StopRequested as _StopRequested

        if _os.environ.get("CKAN_RQ_ANTI_SUICIDE", "0") != "1":
            return super(Worker, self).work(*args, **kwargs)

        while True:
            try:
                return super(Worker, self).work(*args, **kwargs)
            except _StopRequested:
                try:
                    self._rqtrace("StopRequested caught; re-registering birth + subscribe then retry in 1s")
                    try:
                        self.register_birth()
                    except Exception:
                        pass
                    try:
                        self.subscribe()
                    except Exception:
                        pass
                except Exception:
                    pass
                _time.sleep(1)
                continue
""").rstrip("\n").replace("\t", "    ")

out = src[:insert_pos] + patch + "\n\n" + src[insert_pos:]
write_atomic(FILE, out)
print("Patch appliqué :", FILE)
PY

echo "[dataizen] Patch terminé"
exit 0
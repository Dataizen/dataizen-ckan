# -*- coding: utf-8 -*-
"""Job RQ : notifie le service dtz-rag qu'un jeu doit être ré-indexé.
Exécuté par le worker « rag » (enqueue_job). stdlib uniquement."""
import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)

PENDING_KEY = "ckanext-rag:pending:{}"


def _clear_debounce(name):
    """Libère le verrou de débounce dès le démarrage du job : un changement qui
    survient pendant/après la réindexation pourra re-programmer un nouveau job."""
    try:
        from ckan.lib.redis import connect_to_redis
        connect_to_redis().delete(PENDING_KEY.format(name))
    except Exception:
        pass


def reindex_dataset_job(name):
    _clear_debounce(name)
    url = os.getenv("RAG_INTERNAL_URL", "http://dtz-rag:8000")
    token = os.getenv("RAG_WEBHOOK_TOKEN", "")
    req = urllib.request.Request(
        f"{url}/reindex/dataset/{name}", method="POST",
        headers={"X-Dtz-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            log.info("[rag] réindexation %s : %s", name, r.read().decode()[:200])
    except Exception as e:
        log.warning("[rag] réindexation %s échec : %s", name, e)

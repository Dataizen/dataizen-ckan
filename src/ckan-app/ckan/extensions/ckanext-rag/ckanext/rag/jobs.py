# -*- coding: utf-8 -*-
"""Job RQ : notifie le service dtz-rag qu'un jeu doit être ré-indexé.
Exécuté par le worker CKAN existant (enqueue_job). stdlib uniquement."""
import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)


def reindex_dataset_job(name):
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

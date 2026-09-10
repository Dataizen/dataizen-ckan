#!/usr/bin/env python3
"""
Script pour supprimer tous les utilisateurs CKAN sauf ckan_admin et ad_arnia.

Utilise l'API CKAN (user_list, user_delete). Nécessite un token admin.

Usage:
    # Avec variables d'environnement
    CKAN_URL=https://ckan2.data.example.org CKAN_API_KEY=xxx python3 scripts/delete-ckan-users-except.py

    # Ou en arguments
    python3 scripts/delete-ckan-users-except.py --ckan-url https://ckan2.data.example.org --api-key YOUR_ADMIN_TOKEN

    # Mode simulation (affiche ce qui serait supprimé sans rien faire)
    python3 scripts/delete-ckan-users-except.py --dry-run
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

# Utilisateurs à conserver (noms exacts)
KEEP_USERS = {"ckan_admin", "ad_arnia"}


def _log(msg: str) -> None:
    """Affiche un message avec flush pour voir la progression en temps réel."""
    print(msg, flush=True)


def call_api(
    ckan_url: str,
    api_key: str,
    action: str,
    data: Dict[str, Any] | None = None,
    method: str = "POST",
) -> Dict[str, Any] | None:
    """Appelle une action de l'API CKAN."""
    base = ckan_url.rstrip("/")
    # Essayer /api/3/action/ (CKAN récent) puis /api/action/ si besoin
    url = f"{base}/api/3/action/{action}"

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    body = json.dumps(data or {}).encode("utf-8") if data else None

    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        try:
            err_data = json.loads(err_body)
            _log(f"Erreur HTTP {e.code}: {err_data.get('error', err_body)}")
        except json.JSONDecodeError:
            _log(f"Erreur HTTP {e.code}: {err_body}")
        return None
    except Exception as e:
        _log(f"Erreur: {e}")
        return None


def get_user_list(ckan_url: str, api_key: str) -> List[Dict[str, Any]]:
    """Récupère la liste complète des utilisateurs.
    L'API user_list de CKAN retourne tous les utilisateurs en une fois
    (pas de pagination offset/limit supportée).
    """
    _log("   → Appel user_list (all_fields=True)...")
    result = call_api(
        ckan_url,
        api_key,
        "user_list",
        data={"all_fields": True},
    )
    _log("   → Réponse reçue")
    if not result or not result.get("success"):
        if not result:
            _log("Échec de l'appel user_list (pas de réponse)")
        else:
            _log(f"Échec user_list: {result.get('error', 'Unknown')}")
        return []

    batch = result.get("result", [])
    if not isinstance(batch, list):
        _log(f"user_list a retourné un type inattendu: {type(batch)}")
        return []

    _log(f"   → {len(batch)} utilisateurs récupérés")
    return batch


def delete_user(
    ckan_url: str, api_key: str, user_id: str, dry_run: bool = False
) -> bool:
    """Supprime un utilisateur via l'API user_delete."""
    if dry_run:
        return True

    result = call_api(ckan_url, api_key, "user_delete", data={"id": user_id})
    if result and result.get("success"):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Supprime tous les utilisateurs CKAN sauf ckan_admin et ad_arnia"
    )
    parser.add_argument(
        "--ckan-url",
        default=os.getenv("CKAN_URL", "https://ckan2.data.example.org"),
        help="URL de l'instance CKAN",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("CKAN_API_KEY", ""),
        help="Clé API admin (ou CKAN_API_KEY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mode simulation : afficher les utilisateurs sans les supprimer",
    )
    args = parser.parse_args()

    if not args.api_key:
        _log("Clé API requise. Utilisez --api-key ou la variable CKAN_API_KEY.")
        sys.exit(1)

    ckan_url = args.ckan_url.rstrip("/")
    _log(f"CKAN: {ckan_url}")
    _log(f"Utilisateurs conservés: {', '.join(sorted(KEEP_USERS))}")
    if args.dry_run:
        _log(" Mode DRY-RUN (aucune suppression réelle)")
    _log("")

    _log("Récupération de la liste des utilisateurs...")
    users = get_user_list(ckan_url, args.api_key)
    if not users:
        _log("Aucun utilisateur récupéré ou erreur API.")
        sys.exit(1)

    to_delete = []
    to_keep = []
    for u in users:
        name = (u.get("name") or u.get("id") or "").strip()
        if not name:
            continue
        # Exclure aussi l'utilisateur système "default"
        if name in KEEP_USERS or name == "default":
            to_keep.append(name)
        else:
            to_delete.append({"id": u.get("id", name), "name": name})

    _log(f"   Total: {len(users)} utilisateurs")
    _log(f"   Conservés: {len(to_keep)} ({', '.join(sorted(to_keep))})")
    _log(f"   À supprimer: {len(to_delete)}")
    _log("")

    if not to_delete:
        _log("Aucun utilisateur à supprimer.")
        return 0

    _log(" Suppression des utilisateurs...")
    deleted = 0
    failed = 0
    for i, u in enumerate(to_delete, 1):
        name = u["name"]
        uid = u["id"]
        if delete_user(ckan_url, args.api_key, uid, dry_run=args.dry_run):
            deleted += 1
            _log(f"   [{i}/{len(to_delete)}] {name}")
        else:
            failed += 1
            _log(f"   [{i}/{len(to_delete)}] Échec: {name}")

        if not args.dry_run:
            time.sleep(0.2)

    _log("")
    if args.dry_run:
        _log(f" DRY-RUN: {len(to_delete)} utilisateurs seraient supprimés.")
    else:
        _log(f"Terminé: {deleted} supprimés, {failed} échecs.")
        if failed:
            sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

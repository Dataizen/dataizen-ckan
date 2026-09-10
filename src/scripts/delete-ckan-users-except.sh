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


def call_api(
    ckan_url: str,
    api_key: str,
    action: str,
    data: Dict[str, Any] | None = None,
    method: str = "POST",
) -> Dict[str, Any] | None:
    """Appelle une action de l'API CKAN."""
    url = f"{ckan_url.rstrip('/')}/api/3/action/{action}"
    # Fallback pour les versions CKAN avec /api/action/
    if "api/3/" not in url:
        url = f"{ckan_url.rstrip('/')}/api/action/{action}"

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
            print(f"Erreur HTTP {e.code}: {err_data.get('error', err_body)}")
        except json.JSONDecodeError:
            print(f"Erreur HTTP {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"Erreur: {e}")
        return None


def get_user_list(ckan_url: str, api_key: str) -> List[Dict[str, Any]]:
    """Récupère la liste complète des utilisateurs."""
    users = []
    offset = 0
    limit = 1000

    while True:
        result = call_api(
            ckan_url,
            api_key,
            "user_list",
            data={"offset": offset, "limit": limit, "all_fields": True},
        )
        if not result or not result.get("success"):
            if not result:
                print("Échec de l'appel user_list (pas de réponse)")
            else:
                print(f"Échec user_list: {result.get('error', 'Unknown')}")
            break

        batch = result.get("result", [])
        if not isinstance(batch, list):
            print(f"user_list a retourné un type inattendu: {type(batch)}")
            break

        users.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.2)  # Éviter de surcharger l'API

    return users


def delete_user(
    ckan_url: str, api_key: str, user_id: str, dry_run: bool = False
) -> bool:
    """Supprime un utilisateur via l'API user_delete."""
    if dry_run:
        print(f"   [DRY-RUN] Suppression: {user_id}")
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
        print("Clé API requise. Utilisez --api-key ou la variable CKAN_API_KEY.")
        sys.exit(1)

    ckan_url = args.ckan_url.rstrip("/")
    print(f"CKAN: {ckan_url}")
    print(f"Utilisateurs conservés: {', '.join(sorted(KEEP_USERS))}")
    if args.dry_run:
        print(" Mode DRY-RUN (aucune suppression réelle)")
    print()

    print("Récupération de la liste des utilisateurs...")
    users = get_user_list(ckan_url, args.api_key)
    if not users:
        print("Aucun utilisateur récupéré ou erreur API.")
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

    print(f"   Total: {len(users)} utilisateurs")
    print(f"   Conservés: {len(to_keep)} ({', '.join(sorted(to_keep))})")
    print(f"   À supprimer: {len(to_delete)}")
    print()

    if not to_delete:
        print("Aucun utilisateur à supprimer.")
        return 0

    print(" Suppression des utilisateurs...")
    deleted = 0
    failed = 0
    for u in to_delete:
        name = u["name"]
        uid = u["id"]
        if delete_user(ckan_url, args.api_key, uid, dry_run=args.dry_run):
            deleted += 1
            print(f"   {name}")
        else:
            failed += 1
            print(f"   Échec: {name}")

        if not args.dry_run:
            time.sleep(0.2)

    print()
    if args.dry_run:
        print(f" DRY-RUN: {len(to_delete)} utilisateurs seraient supprimés.")
    else:
        print(f"Terminé: {deleted} supprimés, {failed} échecs.")
        if failed:
            sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

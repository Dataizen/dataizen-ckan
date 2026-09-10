#!/bin/sh
set -eu

STAMP="/tmp/csw_patch_applied.stamp"

if [ -f "$STAMP" ]; then
  echo "[CSW PATCH] Déjà exécuté dans ce conteneur, skip"
  exit 0
fi

echo "[CSW PATCH] Exécution patcher"
python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py

touch "$STAMP"
echo "[CSW PATCH] Terminé"

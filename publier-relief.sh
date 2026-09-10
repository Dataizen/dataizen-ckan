#!/usr/bin/env bash
# Publie un gros PMTiles de relief (terrain-RGB) dans CKAN par DÉPÔT DIRECT dans le
# stockage (sans upload HTTP : contourne client_max_body_size / ckan.max_resource_size),
# servi ensuite en HTTP Range. Réutilisable pour toutes les régions.
#
# Le fichier .pmtiles doit déjà être présent SUR LE SERVEUR dtz-core (généré par
# l'outil terrain, cf. dtz/tools/terrain). Le script crée le jeu + la ressource,
# déplace le fichier dans le stockage CKAN (mv instantané si même volume /data),
# puis marque la ressource comme upload.
#
# Usage :
#   CKAN_TOKEN=<sysadmin> ./publier-relief.sh <slug-region> "<titre>" <chemin-pmtiles-sur-serveur> [org]
# Exemple :
#   CKAN_TOKEN=xxx ./publier-relief.sh relief-terrain-idf "Relief Île-de-France (PMTiles terrain-RGB)" \
#       /data/build/terrain-idf/idf.pmtiles referentiels
set -euo pipefail

SLUG="${1:?slug requis}"; TITRE="${2:?titre requis}"; SRC_SERVEUR="${3:?chemin pmtiles serveur requis}"
ORG="${4:-referentiels}"
CK="${CKAN_URL:-https://data.core.dataizen.eu}"
TOK="${CKAN_TOKEN:?CKAN_TOKEN (jeton sysadmin) requis}"
SSH="${DTZ_SSH:-ssh dtz-core}"

api() { curl -s -H "Authorization: $TOK" -H "Content-Type: application/json" "$@"; }

echo "1) création du jeu $SLUG (org $ORG)"
PKG=$(api -X POST -d "{\"name\":\"$SLUG\",\"title\":\"$TITRE\",\"owner_org\":\"$ORG\",\"private\":false,
  \"license_id\":\"lov2\",\"notes\":\"Modèle numérique de terrain encodé terrain-RGB, tuilé en PMTiles, servi en HTTP Range. Source de relief 3D (champ terrain_url des cartes).\"}" \
  "$CK/api/3/action/package_create" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['id'] if d.get('success') else 'ERR:'+str(d.get('error')))")
case "$PKG" in ERR:*) echo "  $PKG"; exit 1;; esac
echo "   pkg=$PKG"

echo "2) création de la ressource (métadonnées)"
RID=$(api -X POST -d "{\"package_id\":\"$PKG\",\"name\":\"$(basename "$SRC_SERVEUR")\",\"url\":\"$(basename "$SRC_SERVEUR")\",\"format\":\"PMTiles\",\"mimetype\":\"application/vnd.pmtiles\"}" \
  "$CK/api/3/action/resource_create" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['id'] if d.get('success') else 'ERR:'+str(d.get('error')))")
case "$RID" in ERR:*) echo "  $RID"; exit 1;; esac
echo "   rid=$RID"

DST="/data/svc/ckan/resources/${RID:0:3}/${RID:3:3}/${RID:6}"
echo "3) déplacement du fichier dans le stockage CKAN : $DST"
# aligne le propriétaire sur une ressource CKAN existante (uid/gid du conteneur CKAN)
$SSH "set -e
  REF=\$(sudo find /data/svc/ckan/resources -maxdepth 3 -type f 2>/dev/null | head -1)
  OWNER=\$(sudo stat -c '%u:%g' \"\$REF\" 2>/dev/null || echo '')
  sudo mkdir -p /data/svc/ckan/resources/${RID:0:3}/${RID:3:3}
  sudo mv \"$SRC_SERVEUR\" \"$DST\"
  [ -n \"\$OWNER\" ] && sudo chown \"\$OWNER\" \"$DST\"
  sudo ls -lh \"$DST\""

SIZE=$($SSH "sudo stat -c %s \"$DST\"")
echo "4) marque la ressource comme upload (taille $SIZE)"
api -X POST -d "{\"id\":\"$RID\",\"url_type\":\"upload\",\"url\":\"$(basename "$SRC_SERVEUR")\",\"size\":$SIZE,\"format\":\"PMTiles\",\"mimetype\":\"application/vnd.pmtiles\"}" \
  "$CK/api/3/action/resource_patch" >/dev/null

DL="$CK/dataset/$PKG/resource/$RID/download/$(basename "$SRC_SERVEUR")"
echo "5) vérification (Range + CORS)"
curl -s -D - -o /dev/null -H "Origin: https://x.core.dataizen.eu" -r 0-1023 "$DL" | grep -iE "HTTP/|accept-ranges|content-range|access-control-allow-origin" | sed 's/^/   /'
echo
echo "OK. terrain_url à coller dans une carte :"
echo "   $DL"

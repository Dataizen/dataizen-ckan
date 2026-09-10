"""
OGC Synchronization Module (hardened)
- Waits for CKAN readiness (API + Solr)
- (Optionally) triggers a reindex
- Generates/merges pygeoapi collections
- Writes YAML atomically with backup + lock
- Restarts pygeoapi process if configured
"""

import logging
import os
import json
import time
import fcntl
import tempfile
import shutil
import subprocess
from contextlib import contextmanager

import requests
from ckan.plugins import toolkit

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

log = logging.getLogger(__name__)


def _env(name, default=None, cast=str):
    v = os.getenv(name, default)
    return cast(v) if v is not None and cast is not None else v


def _restart_pygeoapi_via_kubernetes():
    """Redémarre pygeoapi via Kubernetes : API K8s (depuis un pod) ou kubectl."""
    namespace = os.getenv("KUBERNETES_NAMESPACE", "ckan-bpm")
    deployment_name = os.getenv("PYGEOAPI_DEPLOYMENT_NAME", "pygeoapi")
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    api_host = os.getenv("KUBERNETES_SERVICE_HOST")
    api_port = os.getenv("KUBERNETES_SERVICE_PORT")

    # Méthode 1: API Kubernetes (fonctionne depuis un pod sans kubectl)
    if api_host and api_port and os.path.isfile(token_path) and os.path.isfile(ca_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                token = f.read().strip()
            url = f"https://{api_host}:{api_port}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/strategic-merge-patch+json",
            }
            data = {"spec": {"template": {"metadata": {"annotations": {"ckan/restarted": f"restarted-at-{int(time.time())}"}}}}}
            resp = requests.patch(url, json=data, headers=headers, verify=ca_path, timeout=15)
            if resp.status_code == 200:
                log.info("Redémarrage pygeoapi déclenché via API Kubernetes")
                return
        except Exception as e:
            log.debug("API Kubernetes non disponible: %s", e)

    # Méthode 2: kubectl delete pod
    pod_name = os.getenv("PYGEOAPI_POD_NAME", "pygeoapi")
    try:
        kube_cmd = f"kubectl delete pod {pod_name} -n {namespace} --ignore-not-found=true"
        log.info("Redémarrage pygeoapi via Kubernetes (delete pod %s -n %s)...", pod_name, namespace)
        result = subprocess.run(kube_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            log.info("Pod pygeoapi supprimé, Kubernetes recréera le pod.")
        else:
            log.debug("kubectl non disponible ou échec: %s", (result.stderr or result.stdout or "")[:200])
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        log.debug("Fallback kubectl non disponible: %s", e)


class OGCSynchronizer:
    """
    Synchronizes CKAN datasets with pygeoapi collections
    """

    def __init__(
        self,
        pygeoapi_url=None,
        ckan_url=None,
        ckan_api_key=None,
        pygeoapi_config_path=None,
        pygeoapi_restart_cmd=None,
        require_ckan_ready=True,
        trigger_reindex_if_needed=False,
        max_wait_s=300,
        request_timeout_s=10,
        lock_path="/tmp/pygeoapi_config.lock",
    ):
        # Config from ENV with sane defaults
        self.pygeoapi_url = pygeoapi_url or _env("PYGEOAPI_URL", "http://localhost:5001")
        self.ckan_url = ckan_url or _env("CKAN_URL", "http://localhost:5000")
        self.ckan_api_key = ckan_api_key or _env("CKAN_API_KEY")
        self.pygeoapi_config_path = pygeoapi_config_path or _env(
            "PYGEOAPI_CONFIG_PATH", "/srv/app/pygeoapi/local.config.yml"
        )
        # e.g. "supervisorctl restart pygeoapi" OR "pkill -HUP -f 'gunicorn.*pygeoapi'"
        self.pygeoapi_restart_cmd = pygeoapi_restart_cmd or _env("PYGEOAPI_RESTART_CMD")
        self.require_ckan_ready = require_ckan_ready
        self.trigger_reindex_if_needed = trigger_reindex_if_needed
        self.max_wait_s = max_wait_s
        self.request_timeout_s = request_timeout_s
        self.lock_path = lock_path

        if yaml is None:
            raise RuntimeError("PyYAML is required")

        if not self.ckan_api_key:
            log.warning("No CKAN_API_KEY provided; only public datasets will be visible via direct API calls.")
        
        # Cache pour optimiser les appels API répétés
        self._datastore_cache: dict = {}  # Cache pour datastore_search
        self._geospatial_cache: dict = {}  # Cache pour _has_geospatial_columns
        
    # ---------- Public orchestration ----------

    def sync_all_datasets(self, progress_callback=None):
        """
        Full sync:
        - Wait CKAN ready (and Solr responsive)
        - (Optional) trigger reindex
        - Create/update all geospatial collections
        - Atomic write + optional pygeoapi hot reload/restart

        progress_callback: optional callable(current, total, name) appelé avant chaque dataset.
        """
        if self.require_ckan_ready:
            self._wait_for_ckan_ready()

        if self.trigger_reindex_if_needed:
            self._ensure_search_index_ok_or_rebuild()

        packages = self._get_packages_with_fallback()
        total = len(packages)
        log.debug(f"{total} packages détectés")

        changed_any = False
        current = 0
        for pkg in packages:
            try:
                if self._is_geospatial_dataset(pkg):
                    current += 1
                    if progress_callback:
                        progress_callback(current, total, pkg.get("name", "?"))
                    log.info(f"[{current}/{total}] Synchronisation {pkg.get('name','?')}...")
                    changed = self.sync_dataset(pkg["name"], _pkg=pkg)
                    changed_any = changed_any or changed
            except Exception as e:
                log.error(f"Erreur synchronisation dataset {pkg.get('name','?')}: {e}")

        if changed_any and self.pygeoapi_restart_cmd:
            self._restart_pygeoapi()

        log.info("Synchronisation terminée.")
    
    def sync_dataset(self, package_id, _pkg=None):
        """
        Synchronize a specific dataset. Returns True if the config changed.
        """
        package = _pkg or self._get_package_details(package_id)
        if not package or not self._is_geospatial_dataset(package):
            log.debug(f"Dataset {package_id} n'est pas géospatial: ignoré")
            return False

        collection_id, collection_cfg = self._generate_collection_config(package)
        changed = self._upsert_collection_in_config(collection_id, collection_cfg)
        if changed:
            log.info(f"Dataset {package_id} synchronisé (config mise à jour)")
            # Redémarrer pygeoapi pour charger la nouvelle configuration
            if self.pygeoapi_restart_cmd:
                self._restart_pygeoapi()
        else:
            log.info(f"Dataset {package_id}: aucune modification (déjà à jour)")
        return changed
    
    def remove_dataset(self, package_id):
        changed = self._remove_collection_from_config(package_id)
        if changed and self.pygeoapi_restart_cmd:
            self._restart_pygeoapi()
        return changed
    
    # ---------- Readiness & Index health ----------

    def _wait_for_ckan_ready(self):
        """
        Poll CKAN + Solr readiness without blind sleep.
        Conditions:
          - /api/3/action/status_show OK
          - /api/3/action/package_search rows=0 returns 200 (Solr responsive)
        """
        start = time.time()
        backoff = 1.5
        delay = 1.0

        def _ok_status():
            try:
                r = requests.get(
                    f"{self.ckan_url}/api/3/action/status_show",
                    headers=self._auth_hdr(),
                    timeout=self.request_timeout_s,
                )
                return r.status_code == 200 and r.json().get("success") is True
            except Exception:
                return False

        def _ok_search():
            try:
                r = requests.get(
                    f"{self.ckan_url}/api/3/action/package_search",
                    params={"q": "*:*", "rows": 0},
                    headers=self._auth_hdr(),
                    timeout=self.request_timeout_s,
                )
                return r.status_code == 200 and r.json().get("success") is True
            except Exception:
                return False

        while True:
            status_ok = _ok_status()
            search_ok = _ok_search()
            if status_ok and search_ok:
                log.info("CKAN prêt (API + Solr).")
                return
            if time.time() - start > self.max_wait_s:
                raise TimeoutError("CKAN n'est pas prêt dans le délai imparti.")
            log.debug(f"CKAN pas encore prêt (status={status_ok}, search={search_ok})… retry dans {delay:.1f}s")
            time.sleep(delay)
            delay = min(delay * backoff, 8.0)

    def _ensure_search_index_ok_or_rebuild(self):
        """
        Vérifie la santé de Solr via package_search; sinon déclenche un rebuild.
        """
        try:
            r = requests.get(
                f"{self.ckan_url}/api/3/action/package_search",
                params={"q": "*:*", "rows": 0},
                headers=self._auth_hdr(),
                timeout=self.request_timeout_s,
            )
            if r.status_code == 200 and r.json().get("success"):
                log.info("Index de recherche OK.")
                return
        except Exception:
            pass

        log.warning("Problème d'index de recherche détecté — tentative de rebuild…")
        # Appel CLI CKAN: nécessite que ce code s'exécute dans le conteneur CKAN
        ini = _env("CKAN_INI", "/srv/app/ckan.ini")
        cmd = ["ckan", "-c", ini, "search-index", "rebuild", "-r"]
        try:
            subprocess.check_call(cmd)
            log.info("Rebuild Solr terminé.")
        except Exception as e:
            log.error(f"Échec du rebuild de l'index CKAN: {e}")

    # ---------- CKAN access ----------

    def _auth_hdr(self):
        return {"Authorization": self.ckan_api_key} if self.ckan_api_key else {}

    def _get_packages_with_fallback(self):
        """
        Retourne la liste des packages avec détails complets (via API).
        Optimisé pour utiliser package_search au lieu de package_list + package_show pour chaque package.
        """
        try:
            packages = []
            start = 0
            rows = 100
            while True:
                search = requests.get(
                    f"{self.ckan_url}/api/3/action/package_search",
                    params={
                        "q": "*:*",
                        "rows": rows,
                        "start": start,
                        "include_private": True,
                    },
                    headers=self._auth_hdr(),
                    timeout=self.request_timeout_s * 2,
                )
                search.raise_for_status()
                result = search.json()
                if not result.get("success"):
                    break
                data = result.get("result", {})
                page = data.get("results", [])
                count = data.get("count", 0)
                packages.extend(page)
                log.info(f"Pagination pygeoapi: {len(packages)}/{count} packages")
                if start + rows >= count or len(page) == 0:
                    break
                start += rows
            if packages:
                log.debug(f"Récupération de {len(packages)} packages via package_search (sans limite)")
                return packages
        except Exception as e:
            log.warning(f"package_search échoué, utilisation du fallback: {e}")
        
        # Fallback: méthode originale (package_list + package_show pour chaque)
        try:
            ls = requests.get(
                f"{self.ckan_url}/api/3/action/package_list",
                headers=self._auth_hdr(),
                timeout=self.request_timeout_s,
            )
            ls.raise_for_status()
            names = ls.json().get("result", [])
        except Exception as e:
            log.error(f"Erreur package_list: {e}")
            names = []

        full = []
        for name in names:
            try:
                pkg = self._get_package_details(name)
                if pkg:
                    full.append(pkg)
            except Exception as e:
                log.warning(f"Erreur récupération détails package {name}: {e}")
        return full
    
    def _get_package_details(self, package_id):
        """
        D'abord via action interne, sinon via API HTTP.
        """
        try:
            return toolkit.get_action("package_show")({}, {"id": package_id})
        except Exception:
            pass

        try:
            r = requests.get(
                f"{self.ckan_url}/api/3/action/package_show",
                params={"id": package_id},
                headers=self._auth_hdr(),
                timeout=self.request_timeout_s,
            )
            if r.status_code == 200 and r.json().get("success"):
                return r.json()["result"]
        except Exception as e:
            log.error(f"Erreur package_show {package_id}: {e}")
        return None
    
    # ---------- Dataset heuristics ----------

    def _is_geospatial_dataset(self, package):
        resources = package.get("resources") or []
        for r in resources:
            fmt = (r.get("format") or "").lower().strip()
            if self._is_geospatial_format(fmt):
                return True
            if fmt == "csv" and r.get("datastore_active") and self._has_geospatial_columns(r):
                return True
        return False
    
    def _is_geospatial_format(self, fmt):
        # Formats vecteur/rasters (éviter d'ajouter WMS/WFS ici car ce sont des services)
        geospatial = {
            "geojson", "shp", "gml", "gpx",
            "kml", "kmz",
            "geotiff", "tiff", "tif",
            "netcdf", "hdf5",
            "fgdb", "gpkg"
        }
        return fmt in geospatial
    
    def _has_geospatial_columns(self, resource):
        """
        Vérifie si une ressource a des colonnes géospatiales.
        Utilise un cache pour éviter les appels API répétés.
        """
        rid = resource.get("id")
        if not rid:
            return False
        
        # Vérifier le cache d'abord
        if rid in self._geospatial_cache:
            return self._geospatial_cache[rid]
        
        try:
            # Vérifier le cache datastore_search
            if rid in self._datastore_cache:
                fields = self._datastore_cache[rid].get("fields", [])
            else:
                r = requests.get(
                    f"{self.ckan_url}/api/3/action/datastore_search",
                    params={"resource_id": rid, "limit": 0},
                    headers=self._auth_hdr(),
                    timeout=self.request_timeout_s,
                )
                if r.status_code == 200 and r.json().get("success"):
                    result = r.json()["result"]
                    fields = result.get("fields", [])
                    # Mettre en cache le résultat complet
                    self._datastore_cache[rid] = result
                else:
                    self._geospatial_cache[rid] = False
                    return False
            
            # Vérifier les patterns géospatiaux
            patterns = (
                "lat", "lon", "lng", "long",
                "x", "y", "coord",
                "geometry", "geom", "wkt", "geojson", "st_asgeojson"
            )
            has_geo = False
            for f in fields:
                fid = (f.get("id") or "").lower()
                if any(p in fid for p in patterns):
                    has_geo = True
                    break
            
            # Mettre en cache le résultat
            self._geospatial_cache[rid] = has_geo
            return has_geo
        except Exception as e:
            log.warning(f"Erreur inspection colonnes géo ({resource.get('name','?')}): {e}")
            self._geospatial_cache[rid] = False
            return False
    
    # ---------- Config generation + atomic write ----------

    def _compute_bbox(self, resource):
        """Bbox réelle [minlon, minlat, maxlon, maxlat] depuis les colonnes
        lat/lon du datastore (2000 premiers enregistrements). None si impossible."""
        rid = resource.get("id")
        if not rid:
            return None
        try:
            r = requests.get(
                f"{self.ckan_url}/api/3/action/datastore_search",
                params={"resource_id": rid, "limit": 2000},
                headers=self._auth_hdr(), timeout=self.request_timeout_s,
            )
            if r.status_code != 200 or not r.json().get("success"):
                return None
            result = r.json()["result"]
            fields = [f.get("id", "") for f in result.get("fields", [])]
            lat_col = next((f for f in fields if f.lower() in ("latitude", "lat", "y_lat")), None)
            lon_col = next((f for f in fields if f.lower() in ("longitude", "lon", "lng", "x_lon")), None)
            if not lat_col or not lon_col:
                return None
            lats, lons = [], []
            for rec in result.get("records", []):
                try:
                    la, lo = float(rec.get(lat_col)), float(rec.get(lon_col))
                except (TypeError, ValueError):
                    continue
                if -90 <= la <= 90 and -180 <= lo <= 180:
                    lats.append(la)
                    lons.append(lo)
            if not lats:
                return None
            marge = 0.01
            return [min(lons) - marge, min(lats) - marge, max(lons) + marge, max(lats) + marge]
        except Exception as e:
            log.debug(f"bbox non calculée pour {rid}: {e}")
            return None

    def _generate_collection_config(self, package):
        collection_id = package["name"]
        resources = package.get("resources") or []

        geores = []
        for r in resources:
            fmt = (r.get("format") or "").lower().strip()
            if self._is_geospatial_format(fmt):
                geores.append(r)
            elif fmt == "csv" and r.get("datastore_active") and self._has_geospatial_columns(r):
                geores.append(r)

        if not geores:
            raise ValueError(f"Dataset {collection_id} n'a pas de ressources géospatiales")

        # Prend la première ressource géo (ou améliore avec une préférence)
        resource = geores[0]

        cfg = {
            "type": "collection",
            "title": package.get("title", collection_id),
            "description": package.get("notes", f"Dataset {collection_id} from CKAN"),
            "keywords": [t["name"] for t in (package.get("tags") or [])],
            "limits": {"default": 1000, "max": 50000},
            "links": [{
                "type": "text/html",
                "rel": "canonical",
                "title": "CKAN Dataset",
                "href": f"{self.ckan_url}/dataset/{collection_id}",
                "hreflang": "en",
            }],
            # CRITICAL: CRS84 is ALWAYS required for pygeoapi /map endpoint
            # Without CRS84, the /map endpoint will be rejected by pygeoapi
            "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
            "extents": {
                # bbox réelle si calculable depuis les données (sinon monde)
                "spatial": {"bbox": [self._compute_bbox(resource) or [-180, -90, 180, 90]]},
                "temporal": {"interval": [[None, None]]},
            },
            "storageCrs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
            "providers": [{
                "type": "feature",
                "name": "ckan_provider.provider.CKANProvider",
                "data": f"ckan-{collection_id}",
                "options": {
                    "ckan_url": self.ckan_url,
                    "api_key": self.ckan_api_key,
                    "dataset_id": package["id"],
                    "resource_id": resource.get("id"),
                    "limits": {"default": 1000, "max": 50000},
                    "max_record_count": 50000,
                },
            }],
        }

        return collection_id, cfg
    
    @contextmanager
    def _config_lock(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        with open(self.lock_path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _read_config(self):
        """
        Read pygeoapi config, using base config if target doesn't exist yet.
        """
        # If target config exists, use it
        if os.path.exists(self.pygeoapi_config_path):
            try:
                # Lire le fichier en mode binaire d'abord pour vérifier les caractères nuls
                with open(self.pygeoapi_config_path, "rb") as f:
                    content = f.read()
                    # Vérifier et nettoyer les caractères nuls
                    if b'\x00' in content:
                        log.warning(f"Fichier {self.pygeoapi_config_path} contient des caractères nuls, nettoyage...")
                        content = content.replace(b'\x00', b'')
                        # Réécrire le fichier nettoyé
                        with open(self.pygeoapi_config_path, "wb") as fw:
                            fw.write(content)
                        log.info(f"Fichier nettoyé")
                
                # Lire en mode texte
                with open(self.pygeoapi_config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (yaml.YAMLError, UnicodeDecodeError) as e:
                log.error(f"Erreur lecture config YAML: {e}")
                log.debug(f"Tentative de restauration depuis backup...")
                # Essayer de trouver un backup récent
                config_dir = os.path.dirname(self.pygeoapi_config_path)
                backups = sorted([f for f in os.listdir(config_dir) if f.endswith('.bak')], reverse=True)
                if backups:
                    backup_path = os.path.join(config_dir, backups[0])
                    log.info(f"Restauration depuis: {backup_path}")
                    shutil.copy2(backup_path, self.pygeoapi_config_path)
                    with open(self.pygeoapi_config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                else:
                    data = {}
        else:
            # Try to load base config
            config_dir = os.path.dirname(self.pygeoapi_config_path)
            base_config_candidates = [
                os.path.join(config_dir, 'local.config.base.yml'),  # In pygeoapi_storage (volume)
                '/srv/app/pygeoapi/local.config.base.yml',  # In Docker image
                '/srv/app/ckanext-ogc/ckanext/ogc/pygeoapi/local.config.base.yml',
                '/srv/app/ckanext-ogc/pygeoapi/local.config.base.yml',
            ]
            
            data = {}
            for candidate in base_config_candidates:
                if os.path.exists(candidate):
                    log.info(f"Loading base config from: {candidate}")
                    with open(candidate, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    break
            
            # If no base config found, return empty dict (will be created with minimal structure)
            if not data:
                log.warning("No base config found, will create minimal structure")
                data = {}
        
        if not isinstance(data, dict):
            raise ValueError("Config pygeoapi invalide (racine non-dict).")
        
        # --- UPDATE SERVER URL FROM ENV ---
        # Replace server.url with public URL from environment variable
        # Use same logic as helpers.get_pygeoapi_url() to ensure consistency
        pygeoapi_public_url = os.getenv('PYGEOAPI_PUBLIC_URL') or os.getenv('PYGEOAPI_URL') or 'http://localhost:5001'
        
        # If URL contains localhost, try to replace with public domain
        if 'localhost' in pygeoapi_public_url:
            # Try to get public URL from PYGEOAPI_PUBLIC_URL
            public_url = os.getenv('PYGEOAPI_PUBLIC_URL')
            if public_url:
                pygeoapi_public_url = public_url
            else:
                # Build URL from CKAN_SITE_URL if available
                ckan_url = os.getenv('CKAN_SITE_URL') or os.getenv('CKAN_URL')
                if ckan_url and 'localhost' not in ckan_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(ckan_url)
                    domain = parsed.netloc
                    # Build pygeoapi URL with same domain
                    pygeoapi_public_url = f"{parsed.scheme}://ogc.{domain.split('.', 1)[-1] if '.' in domain else domain}"
        
        # Remove trailing slash if present
        if pygeoapi_public_url.endswith('/'):
            pygeoapi_public_url = pygeoapi_public_url[:-1]
        
        # Apply same transformation as in helpers.py (ogc. -> ogc.ckan2.)
        if 'ogc.' in pygeoapi_public_url and 'ogc.ckan2.' not in pygeoapi_public_url:
            pygeoapi_public_url = pygeoapi_public_url.replace('ogc.', 'ogc.ckan2.', 1)
        
        if 'server' not in data:
            data['server'] = {}
        data['server']['url'] = pygeoapi_public_url
        log.debug(f"Set pygeoapi server.url to: {pygeoapi_public_url}")
        
        # --- SANITIZE CRS/STORAGE CRS ---
        resources = data.setdefault("resources", {})
        def _fix(coll):
            if not isinstance(coll, dict):
                return coll
            if coll.get("type") == "collection":
                crs = coll.get("crs")
                if not isinstance(crs, list) or not all(isinstance(x, str) and x for x in crs):
                    coll["crs"] = ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"]
                s = coll.get("storageCrs")
                if not isinstance(s, str) or not s:
                    coll["storageCrs"] = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
            return coll
        for k, v in list(resources.items()):
            resources[k] = _fix(v)
        return data

    def _atomic_write_yaml(self, data):
        """
        Écrit le YAML atomiquement avec:
          - backup .bak horodaté
          - écriture vers fichier temporaire + fsync + rename
          - relecture de validation
        """
        cfg_path = self.pygeoapi_config_path
        dirpath = os.path.dirname(cfg_path)
        os.makedirs(dirpath, exist_ok=True)

        # Backup
        if os.path.exists(cfg_path):
            ts = time.strftime("%Y%m%d-%H%M%S")
            backup = f"{cfg_path}.{ts}.bak"
            shutil.copy2(cfg_path, backup)
            log.info(f"Backup config: {backup}")

        # Temp write
        fd, tmp = tempfile.mkstemp(prefix=".pygeoapi.", suffix=".yml", dir=dirpath)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, cfg_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

        # Validation de relecture
        with open(cfg_path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)  # lève si corrompu

    def _upsert_collection_in_config(self, collection_id, collection_cfg):
        """
        Upsert idempotent; retourne True si modifié.
        """
        with self._config_lock():
            config = self._read_config()
            resources = config.setdefault("resources", {})

            prev = resources.get(collection_id)
            if prev == collection_cfg:
                return False

            resources[collection_id] = collection_cfg
            self._atomic_write_yaml(config)
            log.info(f"Collection {collection_id} ajoutée/mise à jour dans pygeoapi")
            return True
    
    def _remove_collection_from_config(self, collection_id):
        with self._config_lock():
            config = self._read_config()
            resources = config.get("resources", {})
            if collection_id in resources:
                del resources[collection_id]
                self._atomic_write_yaml(config)
                log.info(f"Collection {collection_id} supprimée de la config pygeoapi")
                return True
            log.info(f"Collection {collection_id} absente; rien à faire")
            return False

    # ---------- Restart / HUP pygeoapi ----------

    def _restart_pygeoapi(self):
        """Redémarre pygeoapi : PYGEOAPI_RESTART_CMD puis fallback Kubernetes (kubectl delete pod)."""
        cmd = self.pygeoapi_restart_cmd
        if cmd:
            log.info(f"Redémarrage pygeoapi: {cmd}")
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    log.info("pygeoapi redémarré.")
                    return
                # Commande échouée (ex. 127 = docker non trouvé sur cluster K8s)
                stderr = (result.stderr or "")[:200]
                if "not found" in stderr.lower() or "command not found" in stderr.lower():
                    log.warning("Commande de redémarrage non disponible (%s), tentative fallback Kubernetes", stderr.strip() or result.returncode)
                else:
                    log.warning("Échec redémarrage pygeoapi (code %s): %s", result.returncode, stderr)
            except subprocess.TimeoutExpired:
                log.warning("Timeout lors du redémarrage pygeoapi, tentative fallback Kubernetes")
            except FileNotFoundError:
                log.warning("Commande de redémarrage non trouvée, tentative fallback Kubernetes")
            except Exception as e:
                log.warning("Erreur redémarrage pygeoapi (non bloquant): %s", e)

        # Fallback Kubernetes : API K8s (prioritaire, fonctionne depuis un pod) puis kubectl
        _restart_pygeoapi_via_kubernetes()
    


# Singleton
_synchronizer = None

def get_synchronizer():
    global _synchronizer
    if _synchronizer is None:
        _synchronizer = OGCSynchronizer()
    return _synchronizer

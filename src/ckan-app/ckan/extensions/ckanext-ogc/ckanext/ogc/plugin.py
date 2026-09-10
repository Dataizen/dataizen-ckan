"""
CKAN OGC Extension Plugin - Avec pygeoapi intégré

Plugin pour pygeoapi intégré dans Docker CKAN avec synchronisation automatique.
"""
import logging
import threading
import subprocess
import time
import requests
import os
import yaml
import zipfile
import tempfile
import json
from ckan.plugins import implements, SingletonPlugin
from ckan.plugins import IConfigurer, IResourceController, IDatasetForm, IBlueprint, ITemplateHelpers, IActions
from ckan.plugins.interfaces import IPackageController
from ckan.plugins import toolkit
from ckan.logic import get_action
from ckan.plugins.toolkit import add_template_directory, add_public_directory, add_resource
from ckan.plugins import PluginImplementations
from ckan.plugins.interfaces import IPluginObserver
from ckanext.ogc.views import get_blueprint
# wms_blueprint supprimé - WMS sera fourni par MapServer
import ckanext.ogc.helpers as ogc_helpers
from ckanext.ogc.logging_filter import DatastoreSearch404Filter


def _thread_with_session_cleanup(fn):
    """Enveloppe un worker de thread pour TOUJOURS refermer la session SQLAlchemy en fin
    d'exécution. Les threads démon de ce plugin tournent HORS du cycle de requête web : sans
    ce remove(), chaque appel package_show/resource_show (qui émet notamment la requête
    tracking_summary) laisse la connexion « idle in transaction ». Ces transactions fantômes
    détiennent des verrous qui peuvent bloquer une purge de jeu et finissent par épuiser le
    pool (constaté : ~11 connexions fuitées par dépôt géo). model.Session est thread-local, donc
    remove() ne referme que la session du thread courant. Pratique standard CKAN pour tout code
    base de données exécuté hors d'une requête."""
    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                from ckan import model
                model.Session.remove()
            except Exception:
                pass
    return _wrapped


def _geo_set_status(resource_id, status, **fields):
    """Pose l'état du traitement géo sur la ressource (lu par l'indicateur « traitement en
    cours » du portail) : `dtz_geo_status` + éventuels `dtz_geo_<champ>`."""
    try:
        patch = {'id': resource_id, 'dtz_geo_status': status}
        patch.update({('dtz_geo_' + k): ('' if v is None else str(v)) for k, v in fields.items()})
        try:
            site_user = get_action('get_site_user')({'ignore_auth': True}, {}).get('name')
        except Exception:
            site_user = None
        get_action('resource_patch')({'ignore_auth': True, 'user': site_user}, patch)
    except Exception as e:
        log.warning("[geo] maj statut %s -> %s échec : %s", resource_id, status, e)


def dtz_geo_process_job(resource_id, force=False):
    """Job RQ : pour une ressource datastore portant la géo EN COLONNE (Geo Shape/Geo Point/
    WKT/lat-lon), construit la géométrie PostGIS `_geom` (robuste, batché, en base) puis
    génère le mapfile (WMS/WFS MapServer). Une fois `_geom` présente, l'engine mapfile
    existant la reconnaît et sert la couche côté serveur (rendu de l'emprise visible).
    Idempotent : ne refait pas si déjà prêt (sauf force). Tourne dans le worker CKAN."""
    from ckanext.ogc import datastore_geo as dg
    ctx = {'ignore_auth': True}
    try:
        try:
            res = get_action('resource_show')(ctx, {'id': resource_id})
        except Exception as e:
            log.warning("[geo] ressource %s introuvable : %s", resource_id, e)
            return
        if not res.get('datastore_active'):
            return
        st = res.get('dtz_geo_status')
        if not force and st in ('geometrizing', 'mapfile', 'ready', 'none'):
            return  # déjà en cours / fait / rien à faire
        try:
            ds = get_action('datastore_search')(ctx, {'resource_id': resource_id, 'limit': 5})
        except Exception as e:
            log.warning("[geo] datastore_search %s échec : %s", resource_id, e)
            return
        detect = dg.detect_geo(ds.get('fields', []), ds.get('records', []))
        if not detect:
            _geo_set_status(resource_id, 'none')
            return
        srccol = detect.get('col') or ((detect.get('lat_col') or '') + '/' + (detect.get('lon_col') or ''))
        _geo_set_status(resource_id, 'geometrizing', col=srccol, kind=detect['kind'])
        done, total, gtype = dg.geometrize(resource_id, detect)
        # Aperçu bas-zoom (échantillon simplifié) pour les gros jeux LIGNE/POLYGONE : bâti AVANT
        # le mapfile, qui détecte la table `<rid>_ov` et sert deux couches à échelle.
        try:
            dg.build_overview(resource_id, gtype, total)
        except Exception as e:
            log.warning("[geo] aperçu non bâti pour %s : %s", resource_id, e)
        try:
            pkg = get_action('package_show')(ctx, {'id': res.get('package_id')})
            try:
                from ckan.common import config as _cfg
                api_key = _cfg.get('ckanext.ogc.ckan_api_key') or ''
            except Exception:
                api_key = ''
            # Génération SYNCHRONE (on est dans le worker) : évite la course des threads
            # async déclenchés par les patchs de statut. Produit le mapfile + WMS d'org.
            dg.build_mapfile(pkg, api_key)
        except Exception as e:
            log.warning("[geo] génération mapfile pour %s échec : %s", resource_id, e)
        _geo_set_status(resource_id, 'ready', col=srccol, kind=detect['kind'], done=done, total=total, type=gtype)
        log.info("[geo] ressource %s prête (%s/%s géométries, %s)", resource_id, done, total, gtype)
    except Exception as e:
        log.error("[geo] traitement %s échec : %s", resource_id, e, exc_info=True)
        try:
            _geo_set_status(resource_id, 'error')
        except Exception:
            pass
    finally:
        try:
            from ckan import model
            model.Session.remove()
        except Exception:
            pass


# Try to import fiona for Shapefile support
try:
    import fiona
    HAS_FIONA = True
except ImportError:
    HAS_FIONA = False
    log = logging.getLogger(__name__)
    log.warning("fiona not available, Shapefile ZIP support will be limited")

# Import des signaux CKAN au niveau global pour éviter les conditions d'enregistrement trop tardives
try:
    from ckan.lib import signals
    log = logging.getLogger(__name__)
    # log.info("Signaux CKAN importés au niveau global")
except ImportError as e:
    log = logging.getLogger(__name__)
    log.error(f"Erreur import signaux CKAN: {e}")
    signals = None

class OGCPlugin(SingletonPlugin):
    """Plugin OGC avec pygeoapi intégré et synchronisation automatique"""
    
    implements(IConfigurer)
    implements(IResourceController, inherit=True)
    implements(IDatasetForm)
    implements(IPackageController, inherit=True)
    implements(IPluginObserver)
    implements(IBlueprint)
    implements(ITemplateHelpers)
    implements(IActions)
    
    def is_fallback(self):
        return False
    
    def package_types(self):
        return []
    
    def before_dataset_index(self, pkg_dict):
        """
        Retire ogc_resources avant envoi à Solr (multi-valued, non supporté par le schéma).
        Ce champ sert uniquement à l'API package_show, pas à la recherche.
        """
        pkg_dict.pop('ogc_resources', None)
        return pkg_dict
    
    def __init__(self, name=None):
        self.name = name or 'ogc'
        # Config via ENV (fallbacks sûrs)
        self.pygeoapi_process = None  # n'est plus utilisé
        self.pygeoapi_url = os.getenv('PYGEOAPI_URL', 'http://localhost:5001')
        # URL CKAN publique (utilisée dans les URLs renvoyées au client).
        self.ckan_url = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
        # URL CKAN interne, utilisee depuis les sous-process (generate-mapfile,
        # imports datagis, etc.) qui tournent dans le meme container que uwsgi.
        # CKAN_SITE_URL peut etre l URL publique (ex http://localhost:8080 sur
        # l hote en dev), qui n est pas joignable depuis l interieur du container.
        self.ckan_internal_url = os.getenv('CKAN_INTERNAL_URL', 'http://localhost:5000')
        self.config_path = os.getenv('PYGEOAPI_CONFIG_PATH', '/srv/app/pygeoapi/local.config.yml')
        self.ckan_api_key = os.getenv('CKAN_API_KEY')  # optionnel
        self.pycsw_config = os.getenv('PYCSW_CONFIG', '/srv/app/pycsw/default.cfg')
        if not self.ckan_api_key:
            log.warning("CKAN_API_KEY non défini, certaines fonctionnalités OGC peuvent ne pas fonctionner")
        self.autostart = os.getenv('PYGEOAPI_AUTOSTART', '0') == '1'
        self._startup_done = False
        self._startup_lock = threading.Lock()
        # Verrou par ressource pour éviter imports datagis concurrents (duplicate key pg_class)
        self._datagis_import_locks = {}
        self._datagis_import_locks_guard = threading.Lock()
        
        # Configurer le logging sécurisé pour éviter les OSError: write error
        self._setup_safe_logging()
        
        # Appliquer le filtre de logging pour filtrer les 404 de datastore_search
        self._apply_logging_filter()
        
        # log.info("OGC Plugin initialisé - pygeoapi intégré")
        # log.info(f"Plugin name: {self.name}")
        # log.info(f"Plugin class: {self.__class__}")
        # log.info(f"Plugin module: {self.__class__.__module__}")
    
    def _setup_safe_logging(self):
        """
        Configure le logging pour utiliser SafeStreamHandler qui ignore silencieusement
        les OSError lors de l'écriture dans stdout/stderr (utile dans Kubernetes/Rancher)
        """
        try:
            from ckanext.ogc.safe_logging_handler import setup_safe_logging
            setup_safe_logging()
            # Utiliser logging directement car log peut ne pas être défini à ce stade
            logging.getLogger(__name__).debug("SafeStreamHandler configuré pour éviter les OSError: write error")
        except ImportError:
            # Si le module n'existe pas, utiliser le logging standard
            logging.getLogger(__name__).debug("Impossible d'importer SafeStreamHandler, utilisation du logging standard")
        except Exception as e:
            # Utiliser logging directement pour éviter les erreurs circulaires
            logging.getLogger(__name__).debug(f"Erreur lors de la configuration de SafeStreamHandler: {e}")
    
    def _apply_logging_filter(self):
        """Applique le filtre de logging pour filtrer les 404 de datastore_search"""
        try:
            # Utiliser le logger local pour éviter les problèmes d'initialisation
            logger = logging.getLogger(__name__)
            flask_app_logger = logging.getLogger('ckan.config.middleware.flask_app')
            
            # Vérifier si le filtre n'est pas déjà appliqué
            for existing_filter in flask_app_logger.filters:
                if isinstance(existing_filter, DatastoreSearch404Filter):
                    logger.debug("Filtre DatastoreSearch404Filter déjà appliqué")
                    return
            
            # Ajouter le filtre
            flask_app_logger.addFilter(DatastoreSearch404Filter())
            logger.info("Filtre DatastoreSearch404Filter appliqué au logger flask_app")
            logger.info("Les 404 de /api/action/datastore_search seront filtrés, les autres logs INFO conservés")
        except Exception as e:
            # Utiliser logging directement si log n'est pas encore défini
            logging.getLogger(__name__).warning(f"Erreur lors de l'application du filtre de logging: {e}")
    
    def update_config(self, config):
        """Configuration du plugin"""
        # log.info("OGC plugin configuré")
        # log.info(f"Config reçue: {type(config)}")
        # log.info(f"Plugin instance: {self}")
        # log.info(f"Plugin implements IResourceController: {hasattr(self, 'after_resource_create')}")
        # log.info(f"Plugin implements IDatasetForm: {hasattr(self, 'after_create')}")
        
        # Ajouter les templates et ressources
        # IMPORTANT: add_template_directory doit être appelé pour que CKAN trouve nos templates
        add_template_directory(config, 'templates')
        add_public_directory(config, 'public')
        add_resource('fanstatic', 'ogc')
        toolkit.add_template_directory(config, 'templates')
        log.info("Extension OGC: templates directory ajoutée (y compris admin/snippets/data_type.html)")
        
        # Enregistrer les signaux pour les actions API (CKAN >= 2.9)
        self._register_api_signals()
        
        # Programmer la synchronisation (pas de démarrage pygeoapi ici)
        self._schedule_delayed_startup()
    
    def _register_api_signals(self):
        """Enregistrer les signaux pour les actions API (CKAN >= 2.9)"""
        try:
            # Utiliser l'import global des signaux CKAN
            if signals is None:
                log.error("Signaux CKAN non disponibles (import global échoué)")
                return
                
            # Synchronisation automatique via signaux désactivée pour éviter les boucles infinies.
            # La synchro se fait via les hooks IResourceController / IPackageController.
        except Exception as e:
            log.error(f"Erreur enregistrement signaux API: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
    
    def _register_hooks(self):
        """Enregistrer les hooks CKAN"""
        log.info("Enregistrement des hooks OGC...")
        
        # Les hooks sont automatiquement enregistrés par CKAN via les interfaces
        # IResourceController et IDatasetForm. Pas besoin d'enregistrement manuel.
        log.info("Hooks OGC enregistrés automatiquement via les interfaces CKAN")
    
    def _schedule_delayed_startup(self):
        """Synchronisations désactivées au démarrage. Lancement manuel via /admin-tools/sync."""
        log.info("Synchronisations au démarrage désactivées, utiliser /admin-tools/sync")
        self._startup_done = True

    def _get_ckan_sync(self):
        """
        Get CKANSync instance with proper configuration
        Uses the same code as sync-ogc.sh for consistency
        """
        import sys
        import importlib.util
        from pathlib import Path
        
        # Get the path to ckan_sync.py
        pygeoapi_providers_path = Path(__file__).parent.parent.parent / 'pygeoapi-providers' / 'ckan_provider'
        ckan_sync_path = pygeoapi_providers_path / 'ckan_sync.py'
        
        if not ckan_sync_path.exists():
            raise ImportError(f"ckan_sync.py not found at {ckan_sync_path}")
        
        # Load the module directly from file
        spec = importlib.util.spec_from_file_location("ckan_sync", ckan_sync_path)
        ckan_sync_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ckan_sync_module)
        CKANSync = ckan_sync_module.CKANSync
        
        # Get configuration from environment or defaults
        ckan_url = os.getenv('CKAN_URL', 'http://localhost:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        pygeoapi_config_path = os.getenv('PYGEOAPI_CONFIG_PATH', '/srv/app/pygeoapi/local.config.yml')
        # Par défaut, utiliser le script qui détecte automatiquement l'environnement (Docker/Kubernetes)
        pygeoapi_restart_cmd = os.getenv('PYGEOAPI_RESTART_CMD', '/srv/app/restart-pygeoapi.sh')
        
        return CKANSync(
            ckan_url=ckan_url,
            ckan_api_key=ckan_api_key,
            pygeoapi_config_path=pygeoapi_config_path,
            pygeoapi_restart_cmd=pygeoapi_restart_cmd
        )
    
    def sync_datasets(self):
        """Synchroniser manuellement tous les datasets (sans démarrer pygeoapi)"""
        try:
            log.info("Lancement de la synchronisation manuelle...")
            sync = self._get_ckan_sync()
            sync.sync()  # Full sync using CKANSync
            log.info("Synchronisation manuelle terminée")
            return True
            
        except Exception as e:
            log.error(f"Erreur synchronisation manuelle: {e}")
            import traceback
            log.error(traceback.format_exc())
            return False
    
    def _schedule_sync_after_indexing(self):
        """Programmer une synchronisation après l'indexation de CKAN"""
        def sync_after_indexing_worker():
            try:
                # Attendre que l'indexation soit terminée
                log.info("Attente que l'indexation de CKAN soit terminée...")
                self._wait_for_indexing_complete()
                # Synchroniser via CKANSync (code unifié avec démarrage)
                log.info("Synchronisation via CKANSync...")
                sync = self._get_ckan_sync()
                sync.sync()  # Full sync using CKANSync
                log.info("Synchronisation après indexation terminée")
                
            except Exception as e:
                log.error(f"Erreur synchronisation après indexation: {e}")
        
        thread = threading.Thread(target=_thread_with_session_cleanup(sync_after_indexing_worker), daemon=True)
        thread.start()
    
    def _wait_for_indexing_complete(self, timeout=300):
        """Attendre que l'indexation de CKAN soit terminée"""
        log.info("Attente que l'indexation de CKAN soit terminée...")
        
        # Use internal URL for same-container access
        ckan_internal_url = self.ckan_url
        if 'localhost:8080' in ckan_internal_url or 'localhost:5000' not in ckan_internal_url:
            # Replace public URL with internal URL
            ckan_internal_url = ckan_internal_url.replace('localhost:8080', 'localhost:5000')
            ckan_internal_url = ckan_internal_url.replace('http://localhost:8080', 'http://localhost:5000')
            if 'localhost:5000' not in ckan_internal_url:
                ckan_internal_url = 'http://localhost:5000'
        
        for i in range(timeout):
            try:
                # Vérifier que l'API répond
                headers = {}
                if self.ckan_api_key:
                    headers['Authorization'] = self.ckan_api_key
                response = requests.get(
                    f"{ckan_internal_url}/api/3/action/package_list",
                    headers=headers,
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success'):
                        # Vérifier que l'index de recherche est prêt (même si vide)
                        search_response = requests.get(
                            f"{ckan_internal_url}/api/3/action/package_search?rows=0",
                            headers=headers,
                            timeout=5
                        )
                        if search_response.status_code == 200:
                            search_data = search_response.json()
                            if search_data.get('success'):
                                # Ne pas exiger que count > 0, juste que la recherche fonctionne
                                log.info("Indexation de CKAN terminée")
                                return
            except requests.RequestException as e:
                # Ne logger les erreurs de connexion refusée qu'occasionnellement (c'est normal pendant le démarrage)
                error_str = str(e)
                is_connection_refused = 'Connection refused' in error_str or 'Failed to establish' in error_str
                
                # Logger seulement toutes les 24 tentatives pour les erreurs de connexion refusée
                # (c'est normal pendant le démarrage/indexation, pas besoin de spammer les logs)
                if is_connection_refused:
                    if i % 24 == 0 and i > 0:
                        log.debug(f"CKAN pas encore prêt pour l'indexation (connexion refusée - normal pendant le démarrage)")
                else:
                    # Pour les autres erreurs, logger plus souvent
                    if i % 12 == 0:
                        log.debug(f"Erreur vérification indexation: {e}")
            
            time.sleep(5)
            if i % 12 == 0:  # Log toutes les minutes
                log.debug(f"Attente indexation... ({i*5}/{timeout*5}s)")
        
        log.warning("Timeout indexation atteint, mais CKAN semble fonctionner. Continuons...")
        # Ne pas lever d'exception, juste continuer
    
    def _wait_for_ckan_ready(self, timeout=120):
        """Attendre que CKAN soit prêt"""
        log.debug("Attente que CKAN soit prêt...")
        
        # Use internal URL for same-container access
        ckan_internal_url = self.ckan_url
        if 'localhost:8080' in ckan_internal_url or 'localhost:5000' not in ckan_internal_url:
            # Replace public URL with internal URL
            ckan_internal_url = ckan_internal_url.replace('localhost:8080', 'localhost:5000')
            ckan_internal_url = ckan_internal_url.replace('http://localhost:8080', 'http://localhost:5000')
            if 'localhost:5000' not in ckan_internal_url:
                ckan_internal_url = 'http://localhost:5000'
        
        headers = {}
        if self.ckan_api_key:
            headers['Authorization'] = self.ckan_api_key
        for i in range(timeout):
            try:
                response = requests.get(
                    f"{ckan_internal_url}/api/3/action/package_list",
                    headers=headers,
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success'):
                        log.info("CKAN est prêt")
                        return
            except requests.RequestException as e:
                # Ne logger les erreurs de connexion refusée qu'occasionnellement (c'est normal pendant le démarrage)
                error_str = str(e)
                is_connection_refused = 'Connection refused' in error_str or 'Failed to establish' in error_str
                
                # Logger seulement toutes les 20 tentatives pour les erreurs de connexion refusée
                # (c'est normal pendant le démarrage, pas besoin de spammer les logs)
                if is_connection_refused:
                    if i % 20 == 0 and i > 0:
                        log.debug(f"CKAN pas encore démarré (connexion refusée - normal pendant le démarrage)")
                else:
                    # Pour les autres erreurs, logger plus souvent
                    if i % 10 == 0:
                        log.debug(f"Erreur vérification CKAN: {e}")
            
            time.sleep(2)
            if i % 10 == 0:
                log.debug(f"Attente CKAN... ({i*2}/{timeout*2}s)")
        
        log.warning("Timeout atteint, mais CKAN semble fonctionner. Continuons...")
        # Ne pas lever d'exception, juste continuer
    
    def _start_pygeoapi(self):
        """Démarrer le processus pygeoapi"""
        # Vérifier si pygeoapi est déjà en cours d'exécution
        if self._check_pygeoapi_running():
            log.info("pygeoapi déjà en cours d'exécution, réutilisation")
            return
        
        try:
            log.info("Démarrage de pygeoapi...")
            
            # Configuration des variables d'environnement pour pygeoapi
            env = os.environ.copy()
            env['PYGEOAPI_CONFIG'] = self.config_path
            env['PYGEOAPI_OPENAPI'] = '/srv/app/pygeoapi/openapi.yml'
            
            self.pygeoapi_process = subprocess.Popen([
                'pygeoapi', 'serve', '--flask'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            
            time.sleep(3)  # Attendre le démarrage
            
        except Exception as e:
            log.error(f"Erreur démarrage pygeoapi: {e}")
            raise
    
    def _check_pygeoapi_running(self):
        """Vérifier si pygeoapi est déjà en cours d'exécution"""
        try:
            # Vérifier via une requête HTTP
            response = requests.get(f"{self.pygeoapi_url}/collections", timeout=2)
            return response.status_code == 200
        except requests.RequestException:
            return False
    
    def _is_pygeoapi_running(self):
        """Vérifier si pygeoapi est en cours d'exécution"""
        try:
            response = requests.get(f"{self.pygeoapi_url}/collections", timeout=2)
            return response.status_code == 200
        except requests.RequestException:
            return False
    
    def _wait_for_pygeoapi(self, timeout=30):
        """Attendre que pygeoapi soit prêt"""
        log.debug("Attente que pygeoapi soit prêt...")
        
        for i in range(timeout):
            try:
                response = requests.get(f"{self.pygeoapi_url}/collections", timeout=2)
                if response.status_code == 200:
                    log.info("pygeoapi est prêt")
                    return
            except requests.RequestException:
                pass
            
            time.sleep(1)
            if i % 5 == 0:
                log.debug(f"Attente pygeoapi... ({i}/{timeout}s)")
        
        raise Exception("pygeoapi n'est pas prêt après le timeout")
    
    # (Supprimé) Toute écriture YAML depuis le plugin.
    
    def _is_geospatial_dataset(self, dataset):
        """Détecter si un dataset est géospatial"""
        # Logique simple : chercher des ressources avec formats géospatiaux
        # Inclure 'zip' pour les ZIP shapefile / KMZ (import datagis ou OGR)
        geospatial_formats = {'geojson', 'shp', 'shapefile', 'kml', 'kmz', 'gpkg', 'geopackage', 'zip'}
        
        for resource in dataset.get('resources', []):
            format_lower = (resource.get('format') or '').lower()
            if format_lower in geospatial_formats:
                return True
            
            # Pour les CSV, vérifier les colonnes géospatiales même si datastore_active n'est pas encore True
            # (xloader peut être en cours de chargement)
            if format_lower == 'csv':
                # Vérifier si la ressource a des colonnes géospatiales dans le datastore
                # Même si datastore_active=False, les données peuvent déjà être là
                try:
                    resource_id = resource.get('id')
                    if resource_id:
                        # Utiliser l'API datastore_search pour vérifier les colonnes
                        import requests
                        url = f"{self.ckan_url}/api/action/datastore_search"
                        params = {'resource_id': resource_id, 'limit': 0}
                        headers = {'Authorization': self.ckan_api_key} if self.ckan_api_key else {}
                        
                        response = requests.get(url, params=params, headers=headers, timeout=10)
                        if response.status_code == 200:
                            result = response.json()
                            if result.get('success'):
                                fields = result.get('result', {}).get('fields', [])
                                field_names = [f.get('id', '').lower() for f in fields]
                                field_set = set(field_names)
                                
                                # Détection NORMALISÉE (espaces/underscores/tirets ignorés) pour
                                # attraper « Geo Shape », « Geo Point », the_geom, _geom, etc. sans
                                # faux positifs type "population". On teste un nom normalisé.
                                def _n(s):
                                    return (s or '').lower().replace(' ', '').replace('_', '').replace('-', '')
                                geom_names = {
                                    'geometry', 'geom', 'thegeom', 'shape', 'geoshape', 'geoshape2d',
                                    'geopoint', 'geopoint2d', 'coordinates', 'stasgeojson', 'geojson',
                                    'wkt', 'contour', 'position',
                                }
                                norm_names = [_n(fn) for fn in field_names]
                                for fn, n in zip(field_names, norm_names):
                                    if n in geom_names or n.endswith('geom'):
                                        log.debug(f"Dataset géospatial détecté via colonne: {fn} (ressource {resource_id})")
                                        return True
                                # Paire lat/lon (noms normalisés)
                                norm_set = set(norm_names)
                                has_lat = norm_set & {'latitude', 'lat', 'ylat'}
                                has_lon = norm_set & {'longitude', 'lon', 'lng', 'long', 'xlon'}
                                if has_lat and has_lon:
                                    log.debug(f"Dataset géospatial détecté via colonnes lat/lon (ressource {resource_id})")
                                    return True
                        elif response.status_code == 404:
                            # Datastore pas encore créé, pas d'erreur
                            log.debug(f"Datastore pas encore créé pour ressource {resource_id}, ignoré pour l'instant")
                except Exception as e:
                    log.debug(f"Erreur vérification colonnes géospatiales pour ressource {resource.get('id')}: {e}")
                    # En cas d'erreur, ne pas considérer comme géospatial (attendre que xloader termine)
        
        return False
    
    def _sync_dataset_to_config(self, dataset):
        """Ajouter un dataset à la configuration pygeoapi"""
        try:
            collection_name = dataset['name']
            
            # Charger la config actuelle
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}
            
            # S'assurer que la section resources existe
            if 'resources' not in config:
                config['resources'] = {}
            
            # Créer la configuration de collection
            collection_config = {
                'type': 'collection',
                'title': dataset.get('title', dataset['name']),
                'description': dataset.get('notes', ''),
                'keywords': [tag['name'] for tag in dataset.get('tags', [])],
                'extents': {
                    'spatial': {'bbox': [[-5.0, 41.0, 10.0, 51.0]]},  # BFC
                    'temporal': {'interval': [[dataset.get('metadata_created'), dataset.get('metadata_modified')]]}
                },
                'crs': ['http://www.opengis.net/def/crs/OGC/1.3/CRS84'],
                'storageCrs': 'http://www.opengis.net/def/crs/OGC/1.3/CRS84',
                'limits': {'default': 10000000, 'max': 10000000},  # Très élevé pour permettre toutes les données
                'providers': [{
                    'name': 'ckan_provider.provider.CKANProvider',
                    'type': 'feature',
                    'data': f"ckan-{collection_name}",
                    'options': {
                        'ckan_url': self.ckan_url,
                        'api_key': self.ckan_api_key or '',
                        'dataset_id': dataset['id'],
                        'max_record_count': 10000000,  # Très élevé pour permettre toutes les données
                        'limits': {'default': 10000000, 'max': 10000000}  # Très élevé pour permettre toutes les données
                    }
                }]
            }
            
            # Ajouter à la config
            config['resources'][collection_name] = collection_config
            
            # Sauvegarder la config
            with open(self.config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, indent=2, allow_unicode=True)
            
            log.info(f"Dataset {collection_name} ajouté à la config pygeoapi")
            
        except Exception as e:
            log.error(f"Erreur sync dataset {dataset.get('name', 'unknown')}: {e}")
    
    # (Supprimé) Toute la gestion de process pygeoapi
    
    def _sync_dataset_async(self, package_id):
        """Synchroniser un dataset de manière asynchrone"""
        log.info(f"_sync_dataset_async appelée avec package_id: {package_id}")
        if not package_id:
            log.warning("Pas de package_id fourni, abandon")
            return
        
        def sync_worker():
            log.info(f"Thread de synchronisation démarré pour: {package_id}")
            try:
                context = {'ignore_auth': True}
                log.debug(f"Récupération du dataset: {package_id}")
                dataset = get_action('package_show')(context, {'id': package_id})
                log.debug(f"Dataset récupéré: {dataset.get('name', 'unknown')}")
                
                if self._is_geospatial_dataset(dataset):
                    log.debug(f"Dataset géospatial détecté, synchronisation...")
                    sync = self._get_ckan_sync()
                    result = sync.sync_dataset(dataset['name'])
                    if result.get('success'):
                        log.info(f"Dataset {package_id} synchronisé")
                    else:
                        log.error(f"Erreur synchronisation dataset {package_id}: {result.get('error')}")
                else:
                    log.debug(f"Dataset {package_id} non géospatial, ignoré")
                    
            except Exception as e:
                log.error(f"Erreur sync dataset {package_id}: {e}")
                import traceback
                log.error(f"Traceback: {traceback.format_exc()}")
        
        log.info(f"Lancement du thread de synchronisation...")
        thread = threading.Thread(target=_thread_with_session_cleanup(sync_worker), daemon=True)
        thread.start()
        log.info(f"Thread lancé: {thread.name}")
    
    # IPluginObserver - Écouter les signaux des actions API
    def before_load(self, plugin):
        """Appelé avant le chargement d'un plugin"""
        log.info(f"IPluginObserver: Plugin {plugin} en cours de chargement")
    
    def after_load(self, service):
        """Appelé après le chargement d'un plugin"""
        log.info(f"IPluginObserver: Plugin {service} chargé")
    
    def before_unload(self, plugin):
        """Appelé avant le déchargement d'un plugin"""
        log.info(f"IPluginObserver: Plugin {plugin} en cours de déchargement")
    
    def after_unload(self, service):
        """Appelé après le déchargement d'un plugin"""
        log.info(f"IPluginObserver: Plugin {service} déchargé")
    
    def _remove_dataset_from_config(self, package_id):
        """Supprimer un dataset de la configuration pygeoapi"""
        try:
            sync = self._get_ckan_sync()
            success = sync.remove_dataset(package_id)
            if success:
                log.info(f"Dataset {package_id} supprimé de la configuration pygeoapi")
            else:
                log.warning(f"Dataset {package_id} non trouvé dans la configuration pygeoapi")
        except Exception as e:
            log.error(f"Erreur suppression dataset {package_id}: {e}")
            import traceback
            log.error(traceback.format_exc())
    
    def _datagis_table_name_for_resource(self, resource_id):
        """Nom de table datagis pour une ressource (même convention que import-geospatial-to-datagis)."""
        if not resource_id:
            return None
        clean = resource_id.replace('-', '_')
        clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in clean)
        name = f"res_{clean}"
        if len(name) > 63:
            name = f"res_{clean[:59]}"
        return name
    
    def _drop_datagis_table_for_resource(self, resource_id):
        """Supprime la table datagis et l'entrée metadata pour une ressource. Retourne True si OK ou si rien à faire."""
        if not resource_id:
            log.debug("[_drop_datagis_table_for_resource] Pas de resource_id, skip")
            return True
        try:
            import psycopg2
        except ImportError:
            log.warning("psycopg2 non disponible, suppression table datagis ignorée")
            return False
        table_name = self._datagis_table_name_for_resource(resource_id)
        if not table_name:
            log.debug(f"[_drop_datagis_table_for_resource] Pas de nom de table pour {resource_id}, skip")
            return True
        log.info(f"[_drop_datagis_table_for_resource] resource_id={resource_id} → table={table_name}")
        host = os.getenv('POSTGRES_HOST', 'db')
        port = int(os.getenv('POSTGRES_PORT', '5432'))
        dbname = os.getenv('DATAGIS_DB', 'datagis')
        user = os.getenv('POSTGRES_USER', 'ckan')
        password = os.getenv('POSTGRES_PASSWORD', '')
        try:
            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname, user=user, password=password,
                connect_timeout=5
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("""
                SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s);
            """, (table_name,))
            if cur.fetchone()[0]:
                cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
                log.info(f"[_drop_datagis_table_for_resource] Table datagis supprimée: {table_name}")
            else:
                log.info(f"[_drop_datagis_table_for_resource] Table {table_name} n'existait pas, rien à supprimer")
            try:
                cur.execute("DELETE FROM datagis_import_metadata WHERE table_name = %s;", (table_name,))
            except psycopg2.ProgrammingError:
                pass
            cur.close()
            conn.close()
            return True
        except psycopg2.OperationalError as e:
            if 'does not exist' in str(e).lower() or 'database' in str(e).lower():
                log.debug(f"Base datagis ou table absente: {table_name}")
                return True
            log.warning(f"Erreur suppression table datagis {table_name}: {e}")
            return False
        except Exception as e:
            log.warning(f"Erreur suppression table datagis {table_name}: {e}")
            return False
    
    def _delete_mapfile_for_dataset(self, dataset_name):
        """Supprime le mapfile d'un dataset s'il existe. Retourne True si supprimé ou absent."""
        log.info(f"[SUPPRESSION MAPFILE] _delete_mapfile_for_dataset appelé: dataset_name={dataset_name!r}")
        if not dataset_name:
            log.debug("[SUPPRESSION MAPFILE] dataset_name vide → skip")
            return True
        mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
        path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
        try:
            if os.path.exists(path) and os.path.isfile(path):
                os.remove(path)
                log.info(f"[SUPPRESSION MAPFILE] Fichier supprimé: {path}")
                return True
            log.info(f"[SUPPRESSION MAPFILE] Fichier absent (rien à supprimer): {path}")
            return True
        except Exception as e:
            log.error(f"[SUPPRESSION MAPFILE] Erreur suppression {path}: {e}", exc_info=True)
            return False
    
    # Hooks CKAN pour la synchronisation automatique
    def after_create(self, context, data_dict):
        """Appelé après création d'un dataset"""
        log.info("HOOK AFTER_CREATE DÉCLENCHÉ !!! ")
        log.info(f"Dataset créé: {data_dict.get('name', 'unknown')}")
        log.info(f"Dataset ID: {data_dict.get('id', 'unknown')}")
        log.info(f"Context: {context}")
        log.info(f"Data dict keys: {list(data_dict.keys())}")
        log.info(f"Plugin instance: {self}")
        log.info(f"Thread: {threading.current_thread().name}")
        
        # Vérifier si c'est un harvest (pour logging)
        is_harvest = False
        extras = data_dict.get('extras', [])
        if isinstance(extras, list):
            for extra in extras:
                if isinstance(extra, dict) and extra.get('key') in ['harvest_source_id', 'harvest_source_title']:
                    is_harvest = True
                    log.debug(f"Dataset moissonné détecté (source: {extra.get('value', 'unknown')})")
                    break
        elif isinstance(extras, dict):
            if extras.get('harvest_source_id') or extras.get('harvest_source_title'):
                is_harvest = True
                log.debug(f"Dataset moissonné détecté")
        
        # Vérifier les ressources présentes
        resources = data_dict.get('resources', [])
        log.info(f"Nombre de ressources dans data_dict: {len(resources)}")
        if resources:
            for idx, res in enumerate(resources[:3]):  # Log seulement les 3 premières
                log.info(f"Ressource {idx+1}: format={res.get('format', 'N/A')}, datastore_active={res.get('datastore_active', False)}")
        
        try:
            self._sync_dataset_async(data_dict.get('name'))
            log.info("Synchronisation pygeoapi lancée avec succès")
            # Synchroniser avec CSW/pycsw
            self._sync_csw_async(data_dict.get('name'))
            # Générer le mapfile MapServer si géospatial
            # IMPORTANT: Pour les harvests, les ressources peuvent être ajoutées après,
            # donc on génère le mapfile même si pas de ressources détectées maintenant
            # Le mapfile sera régénéré quand les ressources seront créées
            if is_harvest:
                log.debug(f"Harvest détecté: génération mapfile différée (sera régénéré lors de la création des ressources)")
            self._generate_mapfile_async(data_dict.get('name'))
        except Exception as e:
            log.error(f"Erreur synchronisation: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
        return data_dict
    
    def after_update(self, context, data_dict):
        """Appelé après modification d'un dataset (y compris soft delete: state=deleted depuis l'UI)."""
        dataset_name = data_dict.get('name')
        state = data_dict.get('state', '')
        nb_resources = len(data_dict.get('resources', []))
        log.info(f"[after_update] Dataset: {dataset_name}, state={state}, nb ressources={nb_resources}")
        # Suppression "douce" depuis l'UI : CKAN fait un patch state=deleted, after_delete n'est pas appelé
        if state == 'deleted':
            log.info("[SUPPRESSION MAPFILE] after_update: state=deleted → cleanup mapfile + datagis")
            self._cleanup_dataset_mapfile_and_datagis(dataset_name, data_dict)
            return data_dict
        try:
            self._sync_dataset_async(dataset_name)
            self._sync_csw_async(dataset_name)
            log.info(f"[SUPPRESSION MAPFILE] after_update: régénération mapfile pour {dataset_name} (dataset non supprimé)")
            self._generate_mapfile_async(dataset_name)
        except Exception as e:
            log.error(f"Erreur synchronisation: {e}")
        return data_dict
    
    def _cleanup_dataset_mapfile_and_datagis(self, dataset_name, data_dict):
        """Supprime mapfile, config pygeoapi et tables datagis pour un dataset (appelé par after_delete ou after_update state=deleted)."""
        log.info(f"[SUPPRESSION MAPFILE] _cleanup_dataset_mapfile_and_datagis: dataset_name={dataset_name!r}")
        if not data_dict:
            data_dict = {}
        resources = data_dict.get('resources', [])
        log.info(f"[SUPPRESSION MAPFILE] _cleanup_dataset: name={dataset_name}, nb ressources={len(resources)}")
        try:
            log.info(f"[SUPPRESSION MAPFILE] Retrait du dataset de la config pygeoapi...")
            self._remove_dataset_from_config(dataset_name)
        except Exception as e:
            log.error(f"Erreur suppression config pygeoapi: {e}")
        try:
            if dataset_name:
                log.info(f"[SUPPRESSION MAPFILE] Suppression mapfile pour dataset {dataset_name}...")
                deleted = self._delete_mapfile_for_dataset(dataset_name)
                log.info(f"[SUPPRESSION MAPFILE] _delete_mapfile_for_dataset({dataset_name}) = {deleted}")
        except Exception as e:
            log.warning(f"Erreur suppression mapfile: {e}")
        for res in resources:
            rid = res.get('id') if isinstance(res, dict) else getattr(res, 'id', None)
            if rid:
                log.info(f"[_cleanup_dataset] Suppression table datagis pour ressource {rid}...")
                self._drop_datagis_table_for_resource(rid)

    def after_delete(self, context, data_dict):
        """Appelé après suppression/purge d'un dataset. Supprime le mapfile et les tables datagis."""
        log.debug("[after_delete] ========== SUPPRESSION DATASET (purge) ==========")
        log.info(f"[after_delete] Dataset supprimé: name={data_dict.get('name', 'unknown')}, id={data_dict.get('id', 'N/A')}")
        dataset_name = data_dict.get('name')
        resources = data_dict.get('resources', [])
        log.info(f"[after_delete] Ressources du dataset (tables datagis à supprimer): {len(resources)}")
        self._cleanup_dataset_mapfile_and_datagis(dataset_name, data_dict)
        log.debug(f"[after_delete] ========== FIN SUPPRESSION DATASET ==========")
        return data_dict
    
    def after_resource_create(self, context, data_dict):
        """Appelé après création d'une ressource"""
        log.info("HOOK AFTER_RESOURCE_CREATE DÉCLENCHÉ !!! ")
        log.info(f"Ressource créée: {data_dict.get('name', 'unknown')}")
        log.info(f"Package ID: {data_dict.get('package_id', 'unknown')}")
        log.info(f"Resource ID: {data_dict.get('id', 'unknown')}")
        log.info(f"Format: {data_dict.get('format', 'N/A')}")
        log.info(f"Datastore active: {data_dict.get('datastore_active', False)}")
        log.info(f"Context: {context}")
        log.info(f"Data dict keys: {list(data_dict.keys())}")
        log.info(f"Plugin instance: {self}")
        log.info(f"Thread: {threading.current_thread().name}")
        package_id = data_dict.get('package_id')
        if package_id:
            try:
                # Vérifier si c'est un harvest
                try:
                    check_context = {'ignore_auth': True}
                    dataset = get_action('package_show')(check_context, {'id': package_id})
                    is_harvest = False
                    extras = dataset.get('extras', [])
                    if isinstance(extras, list):
                        for extra in extras:
                            if isinstance(extra, dict) and extra.get('key') in ['harvest_source_id', 'harvest_source_title']:
                                is_harvest = True
                                log.info(f"Ressource ajoutée à un dataset moissonné (source: {extra.get('value', 'unknown')})")
                                break
                    elif isinstance(extras, dict):
                        if extras.get('harvest_source_id') or extras.get('harvest_source_title'):
                            is_harvest = True
                            log.info(f"Ressource ajoutée à un dataset moissonné")
                    
                    if is_harvest:
                        log.debug(f"Harvest détecté: génération mapfile pour ressource {data_dict.get('name', 'unknown')}")
                except Exception as e:
                    log.warning(f"Erreur vérification harvest: {e}")
                
                self._sync_dataset_async(package_id)
                log.info("Synchronisation pygeoapi lancée avec succès")
                # Synchroniser avec CSW/pycsw
                self._sync_csw_async(package_id)
                # Générer le mapfile MapServer uniquement si la ressource n'est pas géospatiale non-datastore :
                # pour GeoJSON/SHP/ZIP etc. l'import datagis est asynchrone, le mapfile sera généré après
                # succès de l'import (_import_resource_to_datagis_async). Évite le log "AUCUNE SOURCE".
                try:
                    format_ = data_dict.get('format', '').upper()
                    mimetype = (data_dict.get('mimetype') or '').lower()
                    url = data_dict.get('url', '')
                    is_geospatial_nd = (
                        format_ in {'SHP', 'SHAPEFILE', 'GEOJSON', 'GPKG', 'GEOPACKAGE', 'KML', 'KMZ', 'ZIP'}
                        or 'zip' in mimetype or url.lower().endswith(('.shp', '.zip', '.geojson', '.gpkg', '.kml', '.kmz'))
                    ) and not data_dict.get('datastore_active')
                    if not is_geospatial_nd:
                        context = {'ignore_auth': True}
                        dataset = get_action('package_show')(context, {'id': package_id})
                        dataset_name = dataset.get('name')
                        if dataset_name:
                            log.info(f"Génération mapfile déclenchée depuis after_resource_create pour {dataset_name}")
                            self._generate_mapfile_async(dataset_name)
                            log.info("Génération mapfile lancée avec succès")
                        else:
                            log.warning(f"Dataset name non trouvé pour package_id {package_id}")
                    else:
                        log.debug(f"Mapfile non généré ici pour ressource géospatiale (sera généré après import datagis)")
                except Exception as e:
                    log.error(f"Erreur génération mapfile: {e}")
                    import traceback
                    log.error(f"Traceback: {traceback.format_exc()}")
            except Exception as e:
                log.error(f"Erreur synchronisation: {e}")
                import traceback
                log.error(f"Traceback: {traceback.format_exc()}")
        else:
            log.warning("Pas de package_id trouvé")
        
        # Créer une vue GeoJSON pour les Shapefiles ZIP
        try:
            self._create_geojson_view_for_shapefile(context, data_dict)
        except Exception as e:
            log.warning(f"Impossible de créer une vue GeoJSON pour Shapefile: {e}")
        
        # Importer dans datagis si c'est un fichier géospatial non-datastore
        try:
            format_ = data_dict.get('format', '').upper()
            mimetype = (data_dict.get('mimetype') or '').lower()
            url = data_dict.get('url', '')
            
            # Détecter les formats géospatiaux (y compris ZIP avec shapefiles)
            is_geospatial = False
            is_zip = False
            
            # Vérifier le format explicite
            if format_ in {'SHP', 'SHAPEFILE', 'GEOJSON', 'GPKG', 'GEOPACKAGE', 'KML', 'KMZ', 'ZIP'}:
                is_geospatial = True
                if format_ == 'ZIP':
                    is_zip = True
            
            # Vérifier le mimetype
            if 'zip' in mimetype or 'application/zip' in mimetype:
                is_zip = True
                # Si c'est un ZIP, vérifier si c'est probablement un shapefile
                if url.lower().endswith('.zip'):
                    is_geospatial = True
            
            # Vérifier l'extension de l'URL
            if url.lower().endswith(('.shp', '.zip', '.geojson', '.gpkg', '.kml', '.kmz')):
                is_geospatial = True
                if url.lower().endswith('.zip'):
                    is_zip = True
            
            if is_geospatial and not data_dict.get('datastore_active'):
                resource_id = data_dict.get('id')
                resource_name = data_dict.get('name', 'N/A')
                log.info("=" * 80)
                log.info(f" IMPORT AUTOMATIQUE DATAGIS (HOOK after_resource_create)")
                log.info(f"   Ressource ID: {resource_id}")
                log.info(f"   Nom: {resource_name}")
                log.info(f"   Format: {format_}")
                log.info(f"   MIME type: {mimetype}")
                log.debug(f"   ZIP détecté: {is_zip}")
                log.info(f"   Datastore active: {data_dict.get('datastore_active', False)}")
                log.info("=" * 80)
                self._import_resource_to_datagis_async(data_dict)
        except Exception as e:
            log.warning(f"Erreur import datagis pour ressource: {e}")
        
        return data_dict
    
    def get_blueprint(self):
        """Retourne les Blueprints pour les endpoints OGC API et WMS"""
        blueprints = get_blueprint()
        if isinstance(blueprints, list):
            log.info(f"Enregistrement de {len(blueprints)} blueprints OGC:")
            for bp in blueprints:
                log.info(f"   - {bp.name} (url_prefix: {bp.url_prefix})")
            return blueprints
        else:
            # Si get_blueprint() retourne un seul blueprint, on le retourne tel quel
            log.warning(f"get_blueprint() a retourné un seul blueprint au lieu d'une liste")
            log.info(f"Blueprint: {blueprints.name} (url_prefix: {blueprints.url_prefix})")
            return blueprints
    
    def get_helpers(self):
        """Retourne les helpers pour les templates"""
        return {
            'get_organization_id_from_package': ogc_helpers.get_organization_id_from_package,
            'get_organization_name_from_package': ogc_helpers.get_organization_name_from_package,
            'get_collection_name_from_dataset': ogc_helpers.get_collection_name_from_dataset,
            'get_ogc_base_url': ogc_helpers.get_ogc_base_url,
            'get_site_title': ogc_helpers.get_site_title,
            'is_geospatial_dataset': ogc_helpers.is_geospatial_dataset,
            'get_sld_style_name': ogc_helpers.get_sld_style_name,
            'get_sld_style_for_resource': ogc_helpers.get_sld_style_for_resource,
            'has_sld_style_for_resource': ogc_helpers.has_sld_style_for_resource,
            'layer_name_for_resource': ogc_helpers.layer_name_for_resource,
            'get_mapserver_url': ogc_helpers.get_mapserver_url,
            'get_pygeoapi_url': ogc_helpers.get_pygeoapi_url,
            'safe_entity_url': ogc_helpers.safe_entity_url,
            'has_mapfile': ogc_helpers.has_mapfile,
            'format_resource_size': ogc_helpers.format_resource_size,
            'get_home_statistics': ogc_helpers.get_home_statistics,
            'get_popular_tags': ogc_helpers.get_popular_tags,
            'get_categories': ogc_helpers.get_categories,
        }
    
    def get_actions(self):
        """
        Surcharge l'action resource_show pour ajouter les champs OGC
        """
        def resource_show_with_ogc(context, data_dict):
            """
            Version étendue de resource_show qui ajoute les champs OGC
            """
            # Appeler l'action originale depuis le module CKAN core pour éviter la récursion
            # On importe directement depuis ckan.logic.action.get
            try:
                from ckan.logic.action.get import resource_show as core_resource_show
                original_action = core_resource_show
            except (ImportError, AttributeError):
                # Fallback: utiliser get_action mais avec un flag pour éviter la récursion
                if context.get('_ogc_skip'):
                    # On est déjà dans notre propre action, éviter la récursion infinie
                    log.error("[resource_show_with_ogc] Récursion détectée, retour vide")
                    return {}
                
                # Utiliser get_action mais avec un flag pour éviter la récursion
                context['_ogc_skip'] = True
                try:
                    original_action = get_action('resource_show')
                finally:
                    del context['_ogc_skip']
            
            result = original_action(context, data_dict)
            
            # Ajouter les champs OGC
            try:
                resource_id = result.get('id')
                package_id = result.get('package_id')
                
                log.debug(f"[resource_show_with_ogc] Ajout champs OGC pour ressource {resource_id}, package {package_id}")
                
                if not package_id:
                    return result
                
                # Récupérer le dataset pour vérifier s'il est géospatial
                try:
                    context_internal = {'ignore_auth': True}
                    dataset = get_action('package_show')(context_internal, {'id': package_id})
                except Exception as e:
                    log.debug(f"Impossible de récupérer le dataset {package_id} pour vérifier OGC: {e}")
                    return result
                
                dataset_name = dataset.get('name')
                if not dataset_name:
                    return result
                
                # Vérifier si la ressource est géospatiale
                is_geospatial = self._is_geospatial_resource(result, dataset)
                log.info(f"[resource_show_with_ogc] Ressource {resource_id} is_geospatial={is_geospatial} (dataset: {dataset_name})")
                result['is_geospatial'] = is_geospatial
                
                # Vérifier si WMS/WFS est disponible
                has_wms_wfs = False
                wms_url = None
                wfs_url = None
                
                if is_geospatial:
                    # Vérifier si le mapfile existe
                    has_mapfile = ogc_helpers.has_mapfile(dataset_name)
                    log.debug(f"[resource_show_with_ogc] Vérification mapfile pour {dataset_name}: {has_mapfile}")
                    
                    # Vérifier si le dataset a une organisation
                    org = dataset.get('organization')
                    org_name = None
                    if org:
                        if isinstance(org, dict):
                            org_name = org.get('name')
                        elif hasattr(org, 'name'):
                            org_name = org.name
                    
                    # Si pas d'organisation dans dataset, essayer via owner_org
                    if not org_name:
                        owner_org = dataset.get('owner_org')
                        log.info(f"[resource_show_with_ogc] Pas d'organisation dans dataset, essai via owner_org: {owner_org}")
                        if owner_org:
                            try:
                                org_obj = get_action('organization_show')(context_internal, {'id': owner_org})
                                if org_obj:
                                    org_name = org_obj.get('name') if isinstance(org_obj, dict) else getattr(org_obj, 'name', None)
                                    log.debug(f"[resource_show_with_ogc] Organisation trouvée via owner_org: {org_name}")
                            except Exception as e:
                                log.warning(f"[resource_show_with_ogc] Erreur lors de la récupération de l'organisation {owner_org}: {e}")
                    
                    log.info(f"[resource_show_with_ogc] Conditions WMS/WFS: is_geospatial={is_geospatial}, has_mapfile={has_mapfile}, org_name={org_name}")
                    
                    # WMS/WFS est disponible si : géospatial + mapfile (+ organisation pour cohérence)
                    if has_mapfile:
                        has_wms_wfs = True
                        log.info(f"[resource_show_with_ogc] WMS/WFS disponible pour ressource {resource_id}")
                        
                        # URLs directes MapServer (pour QGIS/ArcGIS et API)
                        mapserver_url = ogc_helpers.get_mapserver_url()
                        ckan_site_url = ogc_helpers.get_ogc_base_url()
                        
                        # URL WMS : direct MapServer (base pour le dataset, style compilé dans le mapfile)
                        wms_url = f"{mapserver_url}/wms?map=/mapserver/mapfiles/{dataset_name}.map"
                        
                        # URL WFS : proxy CKAN (corrige les noms de balises XML) ou direct MapServer
                        wfs_url = f"{ckan_site_url}/wfs?map=/mapserver/mapfiles/{dataset_name}.map&SERVICE=WFS&REQUEST=GetCapabilities"
                    else:
                        log.warning(f"[resource_show_with_ogc] WMS/WFS non disponible: has_mapfile={has_mapfile}")
                
                result['has_wms_wfs'] = has_wms_wfs
                result['wms_url'] = wms_url
                result['wfs_url'] = wfs_url
                
                log.debug(f"[resource_show_with_ogc] Champs OGC ajoutés: is_geospatial={is_geospatial}, has_wms_wfs={has_wms_wfs}")
                
            except Exception as e:
                log.error(f"[resource_show_with_ogc] Erreur lors de l'ajout des champs OGC: {e}")
                import traceback
                log.error(f"[resource_show_with_ogc] Traceback: {traceback.format_exc()}")
                # En cas d'erreur, ajouter quand même les champs avec des valeurs par défaut
                result.setdefault('is_geospatial', False)
                result.setdefault('has_wms_wfs', False)
                result.setdefault('wms_url', None)
                result.setdefault('wfs_url', None)
            
            return result
        
        def package_show_with_ogc(context, data_dict):
            """
            Version étendue de package_show qui ajoute les champs OGC au niveau dataset.
            Les champs OGC (is_geospatial, has_wms_wfs, wms_url, wfs_url, ogc_resources)
            ne sont ajoutés que si include_ogc=True (False par défaut).
            """
            try:
                from ckan.logic.action.get import package_show as core_package_show
                original_action = core_package_show
            except (ImportError, AttributeError):
                context['_ogc_skip'] = True
                try:
                    original_action = get_action('package_show')
                finally:
                    context.pop('_ogc_skip', None)
            
            result = original_action(context, data_dict)
            
            include_ogc = data_dict.get('include_ogc', False)
            if not include_ogc:
                return result
            
            try:
                dataset_name = result.get('name')
                if not dataset_name:
                    return result
                
                is_geospatial = self._is_geospatial_dataset(result)
                has_mapfile = ogc_helpers.has_mapfile(dataset_name)
                has_wms_wfs = is_geospatial and has_mapfile
                
                wms_url = None
                wfs_url = None
                
                if has_wms_wfs:
                    mapserver_url = ogc_helpers.get_mapserver_url()
                    ckan_site_url = ogc_helpers.get_ogc_base_url()
                    wms_url = f"{mapserver_url}/wms?map=/mapserver/mapfiles/{dataset_name}.map"
                    wfs_url = f"{ckan_site_url}/wfs?map=/mapserver/mapfiles/{dataset_name}.map&SERVICE=WFS&REQUEST=GetCapabilities"
                
                result['is_geospatial'] = is_geospatial
                result['has_wms_wfs'] = has_wms_wfs
                result['wms_url'] = wms_url
                result['wfs_url'] = wfs_url
                
                # Liste des ressources avec leurs URLs OGC (pour copier-coller dans QGIS)
                ogc_resources = []
                for res in result.get('resources', []):
                    res_geo = self._is_geospatial_resource(res, result)
                    if res_geo and has_wms_wfs:
                        ogc_resources.append({
                            'id': res.get('id'),
                            'name': res.get('name'),
                            'is_geospatial': True,
                            'has_wms_wfs': True,
                            'wms_url': wms_url,
                            'wfs_url': wfs_url,
                        })
                    else:
                        ogc_resources.append({
                            'id': res.get('id'),
                            'name': res.get('name'),
                            'is_geospatial': res_geo,
                            'has_wms_wfs': False,
                            'wms_url': None,
                            'wfs_url': None,
                        })
                result['ogc_resources'] = ogc_resources
                
            except Exception as e:
                log.warning(f"[package_show_with_ogc] Erreur ajout champs OGC: {e}")
                result.setdefault('is_geospatial', False)
                result.setdefault('has_wms_wfs', False)
                result.setdefault('wms_url', None)
                result.setdefault('wfs_url', None)
                result.setdefault('ogc_resources', [])
            
            return result
        
        def organization_show_with_ogc(context, data_dict):
            """
            Version étendue de organization_show qui ajoute les champs OGC.
            Permet d'avoir tous les WMS/WFS d'une organisation pour QGIS.
            Les champs OGC ne sont ajoutés que si include_ogc=True (False par défaut).
            """
            try:
                from ckan.logic.action.get import organization_show as core_org_show
                original_action = core_org_show
            except (ImportError, AttributeError):
                context['_ogc_skip'] = True
                try:
                    original_action = get_action('organization_show')
                finally:
                    context.pop('_ogc_skip', None)
            
            result = original_action(context, data_dict)
            
            include_ogc = data_dict.get('include_ogc', False)
            if not include_ogc:
                return result
            
            try:
                org_name = result.get('name')
                if not org_name:
                    return result
                
                ckan_site_url = ogc_helpers.get_ogc_base_url()
                
                # URLs agrégées pour toute l'organisation (tous les datasets WMS/WFS)
                # Prêtes pour copier-coller dans QGIS
                result['wms_url'] = f"{ckan_site_url}/wms/{org_name}"
                result['wfs_url'] = f"{ckan_site_url}/maps/{org_name}"
                
                # URLs avec GetCapabilities (alternative pour certains clients)
                result['wms_getcapabilities_url'] = f"{ckan_site_url}/wms/{org_name}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
                result['wfs_getcapabilities_url'] = f"{ckan_site_url}/maps/{org_name}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
                
                # Liste des datasets avec WMS/WFS (pour accès individuel)
                context_internal = {'ignore_auth': True}
                try:
                    search_result = get_action('package_search')(
                        context_internal,
                        {'fq': f'organization:{org_name}', 'rows': 1000}
                    )
                    datasets = search_result.get('results', [])
                except Exception as e:
                    log.debug(f"[organization_show_with_ogc] package_search: {e}")
                    datasets = result.get('packages', []) or []
                
                mapserver_url = ogc_helpers.get_mapserver_url()
                ogc_datasets = []
                for pkg in datasets:
                    if isinstance(pkg, str):
                        pkg_name = pkg
                        pkg_title = pkg
                        is_geo = False
                    else:
                        pkg_name = pkg.get('name') or pkg.get('id')
                        pkg_title = pkg.get('title', pkg_name)
                        is_geo = self._is_geospatial_dataset(pkg)
                    if not pkg_name:
                        continue
                    has_mapfile = ogc_helpers.has_mapfile(pkg_name)
                    has_wms_wfs = is_geo and has_mapfile
                    
                    wms_url = None
                    wfs_url = None
                    if has_wms_wfs:
                        wms_url = f"{mapserver_url}/wms?map=/mapserver/mapfiles/{pkg_name}.map"
                        wfs_url = f"{ckan_site_url}/wfs?map=/mapserver/mapfiles/{pkg_name}.map&SERVICE=WFS&REQUEST=GetCapabilities"
                    
                    ogc_datasets.append({
                        'name': pkg_name,
                        'title': pkg_title,
                        'is_geospatial': is_geo,
                        'has_wms_wfs': has_wms_wfs,
                        'wms_url': wms_url,
                        'wfs_url': wfs_url,
                    })
                
                result['ogc_datasets'] = ogc_datasets
                result['ogc_datasets_count'] = len([d for d in ogc_datasets if d.get('has_wms_wfs')])
                
            except Exception as e:
                log.warning(f"[organization_show_with_ogc] Erreur ajout champs OGC: {e}")
                result.setdefault('wms_url', None)
                result.setdefault('wfs_url', None)
                result.setdefault('wms_getcapabilities_url', None)
                result.setdefault('wfs_getcapabilities_url', None)
                result.setdefault('ogc_datasets', [])
                result.setdefault('ogc_datasets_count', 0)
            
            return result
        
        def ogc_purge_artifacts(context, data_dict):
            """Action DÉDIÉE (sysadmin) : retire les artefacts OGC d'un jeu — mapfile WMS/WFS,
            entrée pygeoapi, tables datagis restantes. À appeler explicitement lors d'une
            suppression : CKAN 2.11 n'expose PAS de hook `after_dataset_purge`, donc le
            nettoyage automatique ne se déclenche pas sur `dataset_purge`. data_dict :
            {name|id, resources?}."""
            user = context.get('auth_user_obj')
            if not (user is not None and getattr(user, 'sysadmin', False)):
                raise toolkit.NotAuthorized('ogc_purge_artifacts : réservé aux administrateurs')
            name = data_dict.get('name') or data_dict.get('id')
            resources = data_dict.get('resources') or []
            log.info(f"[ogc_purge_artifacts] nettoyage OGC pour {name} ({len(resources)} ressources)")
            self._cleanup_dataset_mapfile_and_datagis(name, {'name': name, 'resources': resources})
            return {'name': name, 'cleaned': True}

        def dtz_geo_reprocess(context, data_dict):
            """(Re)construire la géométrie cartographique + le mapfile d'un jeu ou d'une
            ressource, de façon IDEMPOTENTE. Réservé à un éditeur du jeu (droit package_update)
            ou à un sysadmin. data_dict : {resource_id} pour une ressource, ou {id|name} pour
            un jeu (toutes ses ressources datastore). {force:true} refait tout."""
            rid = data_dict.get('resource_id')
            name = data_dict.get('name') or data_dict.get('id')
            force = bool(data_dict.get('force'))
            internal = {'ignore_auth': True}
            rids = []
            if rid:
                res = get_action('resource_show')(internal, {'id': rid})
                toolkit.check_access('package_update', context, {'id': res.get('package_id')})
                rids = [rid]
            elif name:
                pkg = get_action('package_show')(internal, {'id': name})
                toolkit.check_access('package_update', context, {'id': pkg['id']})
                rids = [r['id'] for r in pkg.get('resources', []) if r.get('datastore_active')]
            else:
                raise toolkit.ValidationError({'resource_id': 'resource_id ou name requis'})
            for r in rids:
                if force:
                    try:
                        get_action('resource_patch')(internal, {'id': r, 'dtz_geo_status': ''})
                    except Exception:
                        pass
                # Queue dédiée « geo » : un worker séparé traite la géométrisation, pour
                # qu'un gros job (millions de lignes) ne bloque plus la file xloader.
                toolkit.enqueue_job(dtz_geo_process_job, [r, force], title=f"dtz geo reprocess {r}",
                                    queue='geo')
            log.info("[geo] re-traitement enfilé pour %s ressource(s) (force=%s)", len(rids), force)
            return {'enqueued': rids, 'force': force}

        # Permettre les requêtes GET (paramètres dans l'URL) comme les actions core
        resource_show_with_ogc.side_effect_free = True
        package_show_with_ogc.side_effect_free = True
        organization_show_with_ogc.side_effect_free = True

        return {
            'resource_show': resource_show_with_ogc,
            'package_show': package_show_with_ogc,
            'organization_show': organization_show_with_ogc,
            'ogc_purge_artifacts': ogc_purge_artifacts,
            'dtz_geo_reprocess': dtz_geo_reprocess,
        }
    
    def after_resource_show(self, context, data_dict):
        """
        Appelé après récupération d'une ressource via resource_show (si appelé par CKAN)
        Note: Ce hook n'est peut-être pas appelé pour l'action resource_show,
        c'est pourquoi on utilise aussi IActions pour surcharger l'action directement
        """
        # Cette méthode est conservée au cas où CKAN l'appellerait, mais on utilise principalement get_actions()
        return data_dict
    
    def _is_geospatial_resource(self, resource, dataset=None):
        """
        Vérifie si une ressource est géospatiale
        
        Args:
            resource: Dictionnaire de la ressource
            dataset: Dictionnaire du dataset (optionnel, récupéré si non fourni)
        
        Returns:
            bool: True si géospatial, False sinon
        """
        try:
            # PRIORITÉ 1: Si le dataset a un mapfile, la ressource est géospatiale
            # (c'est l'indicateur le plus fiable car le mapfile n'existe que pour les datasets géospatiaux)
            if dataset:
                dataset_name = dataset.get('name')
                if dataset_name:
                    has_mapfile = ogc_helpers.has_mapfile(dataset_name)
                    if has_mapfile:
                        log.debug(f"[_is_geospatial_resource] Dataset {dataset_name} a un mapfile, ressource considérée comme géospatiale")
                        return True
            
            # PRIORITÉ 2: Vérifier le format de la ressource
            format_value = resource.get('format', '').strip().upper()
            geospatial_formats = {
                'GEOJSON', 'SHP', 'SHAPEFILE', 'KML', 'KMZ', 
                'GPX', 'GPKG', 'GEOPACKAGE', 'GEOTIFF', 'TIF', 'TIFF', 'ZIP'
            }
            
            if format_value in geospatial_formats:
                log.debug(f"[_is_geospatial_resource] Format géospatial détecté: {format_value}")
                return True
            
            # PRIORITÉ 3: Vérifier l'extension de l'URL
            url = (resource.get('url') or '').lower()
            if any(url.endswith(ext) for ext in ['.zip', '.shp', '.geojson', '.gpkg', '.kml', '.kmz', '.gpx']):
                log.debug(f"[_is_geospatial_resource] Extension géospatiale détectée dans URL: {url}")
                return True
            
            # PRIORITÉ 4: Vérifier si datastore est actif avec colonnes géométriques
            # IMPORTANT: Ne vérifier que si datastore_active est explicitement True
            # pour éviter les appels HTTP inutiles pour des ressources non-datastore
            datastore_active = resource.get('datastore_active', False)
            if datastore_active is True:
                # Vérifier les colonnes géométriques via l'API datastore_search
                resource_id = resource.get('id')
                if resource_id:
                    try:
                        import requests
                        import os
                        # IMPORTANT: Utiliser l'URL interne pour éviter les timeouts
                        # Dans le conteneur, on utilise http://ckan:5000 ou http://localhost:5000
                        # Ne pas utiliser l'URL publique qui peut causer des timeouts
                        ckan_url = os.getenv('CKAN_INTERNAL_URL', 'http://localhost:5000')
                        
                        # Si CKAN_INTERNAL_URL n'est pas défini, essayer de détecter l'URL interne
                        if ckan_url == 'http://localhost:5000':
                            # Vérifier si on est dans un conteneur Docker/Kubernetes
                            # Dans ce cas, utiliser le service interne
                            if os.path.exists('/.dockerenv') or os.getenv('KUBERNETES_SERVICE_HOST'):
                                ckan_url = 'http://ckan:5000'
                            else:
                                # Sinon, utiliser localhost
                                ckan_url = 'http://localhost:5000'
                        
                        # Nettoyer l'URL si elle contient des ports externes
                        if 'localhost:8080' in ckan_url or 'localhost:8083' in ckan_url:
                            ckan_url = ckan_url.replace('localhost:8080', 'localhost:5000').replace('localhost:8083', 'localhost:5000')
                        
                        # Ne pas utiliser l'URL publique (https://) car elle peut causer des timeouts
                        if ckan_url.startswith('https://'):
                            # Remplacer par l'URL interne
                            if 'ckan2.data.example.org' in ckan_url or 'ckan' in ckan_url:
                                ckan_url = 'http://ckan:5000' if os.path.exists('/.dockerenv') or os.getenv('KUBERNETES_SERVICE_HOST') else 'http://localhost:5000'
                        
                        api_key = os.getenv('CKAN_API_KEY', '')
                        headers = {}
                        if api_key:
                            headers['Authorization'] = api_key
                        
                        # Faire une requête limit=0 pour obtenir uniquement les métadonnées
                        # Augmenter le timeout à 15 secondes pour éviter les timeouts
                        response = requests.get(
                            f"{ckan_url}/api/action/datastore_search",
                            params={'resource_id': resource_id, 'limit': 0},
                            headers=headers,
                            timeout=15
                        )
                        
                        # Gérer les réponses HTTP
                        if response.status_code == 200:
                            result = response.json()
                            if result.get('success'):
                                fields = result.get('result', {}).get('fields', [])
                                # Vérifier les colonnes géométriques (inclure st_asgeojson, geo_point_2d)
                                geom_fields = [
                                    'geom', 'geometry', 'the_geom', 'latitude', 'longitude', 
                                    'lon', 'lat', 'x', 'y', 'geo_point', 'geo_point_2d', 
                                    'coordinates', 'coord', 'st_asgeojson', 'geojson'
                                ]
                                field_names = [f.get('id', '').lower() for f in fields]
                                # Vérifier aussi si le nom du champ contient 'geo' ou 'geom'
                                has_geom = any(
                                    field_name in [g.lower() for g in geom_fields] or 
                                    'geo' in field_name or 'geom' in field_name 
                                    for field_name in field_names
                                )
                                if has_geom:
                                    log.debug(f"[_is_geospatial_resource] Colonnes géométriques détectées dans datastore")
                                    return True
                        elif response.status_code == 404:
                            # 404 est normal si la ressource n'est pas encore dans le datastore
                            # (xloader en cours, ou datastore_active=True mais table pas encore créée)
                            # Ne pas logger comme erreur, juste ignorer silencieusement
                            log.debug(f"[_is_geospatial_resource] Ressource {resource_id} pas encore dans datastore (404), ignoré")
                        else:
                            # Autres codes d'erreur HTTP (500, 503, etc.) - logger en debug seulement
                            log.debug(f"[_is_geospatial_resource] Erreur HTTP {response.status_code} pour ressource {resource_id}")
                    except requests.exceptions.Timeout:
                        # Timeout - logger en debug seulement pour éviter le bruit
                        log.debug(f"[_is_geospatial_resource] Timeout lors de la vérification datastore pour ressource {resource_id}")
                    except Exception as e:
                        # En cas d'erreur, ne pas considérer comme géospatial
                        # Logger en debug seulement pour éviter le bruit dans les logs
                        log.debug(f"[_is_geospatial_resource] Erreur vérification colonnes géométriques pour ressource {resource_id}: {e}")
                        pass
            
            # PRIORITÉ 5: Vérifier si le dataset lui-même est géospatial (via _is_geospatial_dataset)
            # (déjà vérifié en PRIORITÉ 1 pour le mapfile, mais on vérifie aussi les autres critères)
            if dataset:
                if self._is_geospatial_dataset(dataset):
                    log.debug(f"[_is_geospatial_resource] Dataset {dataset.get('name', 'N/A')} est géospatial, ressource considérée comme géospatiale")
                    return True
            
            return False
            
        except Exception as e:
            log.debug(f"Erreur vérification géospatiale ressource: {e}")
            return False
    
    def after_resource_update(self, context, data_dict):
        """Appelé après modification d'une ressource"""
        package_id = data_dict.get('package_id')
        resource_id = data_dict.get('id')
        
        if package_id:
            try:
                self._sync_dataset_async(package_id)
                log.info("Synchronisation pygeoapi lancée avec succès (after_resource_update)")
                # Synchroniser avec CSW/pycsw
                self._sync_csw_async(package_id)
                
                # Vérifier si la ressource est maintenant dans le datastore
                # IMPORTANT: Vérifier l'état RÉEL de la ressource, pas seulement data_dict
                # car XLoader peut activer datastore_active directement en DB sans passer par l'API
                datastore_active = False
                if data_dict.get('datastore_active'):
                    datastore_active = True
                elif resource_id:
                    # Vérifier l'état réel de la ressource dans la base de données
                    try:
                        context_internal = {'ignore_auth': True}
                        resource = get_action('resource_show')(context_internal, {'id': resource_id})
                        if resource.get('datastore_active'):
                            datastore_active = True
                            log.debug(f"Ressource {resource_id} a maintenant datastore_active=True (détecté via resource_show)")
                    except Exception as e:
                        log.warning(f"Impossible de vérifier l'état de la ressource {resource_id}: {e}")

                # Géo EN COLONNE : dès que le datastore est actif, enfiler le job qui construit
                # la géométrie PostGIS (_geom) puis le mapfile. Idempotent (le job re-vérifie),
                # garde anti-boucle sur l'état déjà posé (les patchs de statut re-déclenchent ce hook).
                if datastore_active and resource_id and not data_dict.get('dtz_geo_status'):
                    try:
                        toolkit.enqueue_job(dtz_geo_process_job, [resource_id], title=f"dtz geo {resource_id}",
                                            queue='geo')
                        log.info(f"[geo] job de géométrisation enfilé pour {resource_id}")
                    except Exception as e:
                        log.warning(f"[geo] enqueue job échec pour {resource_id}: {e}")

                # Déterminer si la ressource est géospatiale et récupérer le nom du dataset (pour mapfile)
                format_ = data_dict.get('format', '').upper()
                mimetype = (data_dict.get('mimetype') or '').lower()
                url = data_dict.get('url', '')
                is_geospatial = False
                if format_ in {'SHP', 'SHAPEFILE', 'GEOJSON', 'GPKG', 'GEOPACKAGE', 'KML', 'KMZ', 'ZIP'}:
                    is_geospatial = True
                if 'GEOPACKAGE' in (format_ or '') or 'GPKG' in (format_ or ''):
                    is_geospatial = True
                if 'zip' in mimetype or 'application/zip' in mimetype:
                    if url and url.lower().endswith('.zip'):
                        is_geospatial = True
                if url and url.lower().endswith(('.shp', '.zip', '.geojson', '.gpkg', '.kml', '.kmz')):
                    is_geospatial = True
                dataset_name = None
                try:
                    context_internal = {'ignore_auth': True}
                    dataset = get_action('package_show')(context_internal, {'id': package_id})
                    dataset_name = dataset.get('name')
                except Exception as e:
                    log.warning(f"Impossible de récupérer le dataset (package_show) pour mapfile: {e}")
                
                # Si CSV dans datastore : supprimer l'ancienne table datagis (ex. après remplacement ZIP→CSV)
                # pour éviter que le mapfile utilise des données obsolètes.
                if format_ == 'CSV' and datastore_active and resource_id:
                    try:
                        self._drop_datagis_table_for_resource(resource_id)
                    except Exception as e:
                        log.debug(f"Pas de table datagis à supprimer ou erreur: {e}")
                
                # Générer le mapfile si la ressource est dans le datastore OU si elle est géospatiale (datagis)
                # Ainsi toute modification d'une ressource géo (metadata, SLD, ou après import) met à jour le mapfile
                if dataset_name and (datastore_active or is_geospatial):
                    try:
                        self._generate_mapfile_async(dataset_name)
                        log.info("Génération mapfile lancée avec succès (after_resource_update)")
                    except Exception as e:
                        log.warning(f"Erreur génération mapfile (after_resource_update): {e}")
                
                # Ré-importer dans datagis si la ressource est géospatiale et non-datastore
                # (ex. remplacement de fichier GeoJSON/SHP/GPKG pour la même ressource)
                if is_geospatial and not datastore_active:
                    try:
                        log.info("=" * 80)
                        log.info(f" RÉ-IMPORT DATAGIS (HOOK after_resource_update)")
                        log.info(f"   Ressource ID: {resource_id}")
                        log.info(f"   Format: {format_}")
                        log.info("=" * 80)
                        self._import_resource_to_datagis_async(data_dict)
                    except Exception as e:
                        log.warning(f"Erreur import datagis (after_resource_update): {e}")
            except Exception as e:
                log.error(f"Erreur synchronisation (after_resource_update): {e}")
        return data_dict
    
    def before_resource_delete(self, context, data_dict, resource=None):
        """Appelé avant suppression d'une ressource. CKAN appelle after_resource_delete avec la liste des ressources restantes, pas la ressource supprimée : on stocke l'id ici."""
        log.debug("[SUPPRESSION] ========== before_resource_delete DÉCLENCHÉ ==========")
        if isinstance(data_dict, dict):
            context['_ogc_deleted_resource_id'] = data_dict.get('id')
            context['_ogc_deleted_package_id'] = data_dict.get('package_id')
            log.info(f"[SUPPRESSION] Ressource à supprimer: id={data_dict.get('id')}, package_id={data_dict.get('package_id')}, name={data_dict.get('name', 'N/A')}")
        else:
            log.warning(f"[SUPPRESSION] before_resource_delete: data_dict n'est pas un dict (type={type(data_dict).__name__}), after_resource_delete pourrait ne pas avoir resource_id")
        log.debug("[SUPPRESSION] ========== FIN before_resource_delete ==========")
        return data_dict

    def after_resource_delete(self, context, data_dict):
        """Appelé après suppression d'une ressource. CKAN passe pkg_dict.get('resources', []) = liste des ressources restantes, pas un dict."""
        log.debug("[SUPPRESSION] ========== after_resource_delete DÉCLENCHÉ ==========")
        package_id = None
        resource_id = None
        if isinstance(data_dict, list):
            resource_id = context.get('_ogc_deleted_resource_id')
            package_id = context.get('_ogc_deleted_package_id')
            if package_id is None and data_dict:
                package_id = data_dict[0].get('package_id') if isinstance(data_dict[0], dict) else getattr(data_dict[0], 'package_id', None)
            log.info(f"[SUPPRESSION] Contexte: resource_id={resource_id}, package_id={package_id}, nb ressources restantes={len(data_dict)}")
        else:
            package_id = data_dict.get('package_id')
            resource_id = data_dict.get('id')
            log.info(f"[SUPPRESSION] Contexte (dict): resource_id={resource_id}, package_id={package_id}")
        if resource_id:
            log.info(f"[SUPPRESSION] Suppression table datagis pour ressource {resource_id}...")
            self._drop_datagis_table_for_resource(resource_id)
        if package_id:
            try:
                log.info(f"[SUPPRESSION] Sync pygeoapi + CSW pour package {package_id}...")
                self._sync_dataset_async(package_id)
                self._sync_csw_async(package_id)
                ctx = {'ignore_auth': True}
                try:
                    dataset = get_action('package_show')(ctx, {'id': package_id})
                    dataset_name = dataset.get('name')
                    nb_resources = len(dataset.get('resources', []))
                    log.info(f"[SUPPRESSION] package_show OK: dataset_name={dataset_name}, nb ressources={nb_resources}")
                    if dataset_name:
                        # Retirer immédiatement la couche de la ressource supprimée du mapfile (sync)
                        if resource_id:
                            layer_name = ogc_helpers.layer_name_for_resource(resource_id)
                            mapfiles_dir = os.getenv('MAPFILES_DIR', '/mapserver/mapfiles')
                            mapfile_path = os.path.join(mapfiles_dir, f"{dataset_name}.map")
                            log.debug(f"[SUPPRESSION] Retrait couche mapfile: layer_name={layer_name}, mapfile_path={mapfile_path}, existe={os.path.exists(mapfile_path)}")
                            if layer_name:
                                removed = ogc_helpers.remove_layer_from_mapfile(mapfile_path, layer_name)
                                if removed:
                                    log.debug(f"[SUPPRESSION] Couche {layer_name} retirée du mapfile {dataset_name}.map (OK)")
                                else:
                                    log.warning(f"[SUPPRESSION] remove_layer_from_mapfile a retourné False pour {layer_name} / {mapfile_path}")
                            else:
                                log.warning(f"[SUPPRESSION] layer_name vide pour resource_id={resource_id}, skip retrait couche")
                        is_geo = self._is_geospatial_dataset(dataset)
                        log.info(f"[SUPPRESSION] _is_geospatial_dataset(dataset) = {is_geo} pour {dataset_name}")
                        if is_geo:
                            # Il reste des ressources géospatiales : régénérer le mapfile (cohérence complète)
                            log.info(f"[SUPPRESSION] Dataset {dataset_name} a encore des ressources géo → régénération mapfile")
                            self._generate_mapfile_async(dataset_name)
                        else:
                            # Dernière ressource géospatiale supprimée : supprimer le mapfile
                            log.debug(f"[SUPPRESSION] Plus de ressources géo → suppression mapfile {dataset_name}.map")
                            deleted = self._delete_mapfile_for_dataset(dataset_name)
                            log.info(f"[SUPPRESSION] _delete_mapfile_for_dataset({dataset_name}) = {deleted}")
                    else:
                        log.warning(f"[SUPPRESSION] dataset_name vide, skip mapfile")
                except Exception as e:
                    log.error(f"[SUPPRESSION] Exception dans after_resource_delete (package_show/mapfile): {e}", exc_info=True)
            except Exception as e:
                log.error(f"[SUPPRESSION] Erreur synchronisation: {e}", exc_info=True)
        else:
            log.warning(f"[SUPPRESSION] package_id vide, skip mapfile/pygeoapi/csw")
        log.debug("[SUPPRESSION] ========== FIN after_resource_delete ==========")
        return data_dict
    
    def _sync_csw_async(self, package_id):
        """
        Synchroniser un dataset avec pycsw de manière asynchrone
        
        Args:
            package_id: ID ou nom du dataset à synchroniser
        """
        if not package_id:
            log.warning("Pas de package_id fourni pour synchronisation CSW, abandon")
            return
        
        def sync_csw_worker():
            log.info(f"Thread de synchronisation CSW démarré pour: {package_id}")
            try:
                # Vérifier que pycsw est configuré
                if not os.path.exists(self.pycsw_config):
                    log.warning(f"Configuration pycsw non trouvée: {self.pycsw_config}, synchronisation CSW ignorée")
                    return
                
                # Utiliser le script ckan_pycsw.py pour synchroniser
                # Le script charge tous les datasets, donc on peut simplement l'appeler
                # Il mettra à jour uniquement les datasets qui ont changé
                ckan_pycsw_script = '/srv/app/src/ckanext-spatial/bin/ckan_pycsw.py'
                
                if not os.path.exists(ckan_pycsw_script):
                    log.warning(f"Script ckan_pycsw.py non trouvé: {ckan_pycsw_script}, synchronisation CSW ignorée")
                    return
                
                # Utiliser l'URL interne de CKAN pour la synchronisation
                ckan_internal_url = self.ckan_url
                if 'localhost:8080' in ckan_internal_url or 'localhost:5000' not in ckan_internal_url:
                    ckan_internal_url = ckan_internal_url.replace('localhost:8080', 'localhost:5000')
                    ckan_internal_url = ckan_internal_url.replace('http://localhost:8080', 'http://localhost:5000')
                    if 'localhost:5000' not in ckan_internal_url:
                        ckan_internal_url = 'http://localhost:5000'
                
                log.info(f"Synchronisation CSW pour dataset: {package_id}")
                log.info(f"URL CKAN: {ckan_internal_url}")
                log.info(f"Config pycsw: {self.pycsw_config}")
                
                # Exécuter la synchronisation pycsw
                # Le script ckan_pycsw.py attend: load [-p config_file] [-u ckan_url]
                # Il n'accepte pas d'option -k pour la clé API
                # La clé API doit être configurée dans le fichier pycsw.cfg si nécessaire
                import subprocess
                cmd = [
                    'python3', ckan_pycsw_script,
                    'load',
                    '-p', self.pycsw_config,
                    '-u', ckan_internal_url
                ]
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes max
                )
                
                if result.returncode == 0:
                    log.info(f"Synchronisation CSW réussie pour dataset: {package_id}")
                    if result.stdout:
                        log.debug(f"Sortie ckan_pycsw: {result.stdout[:500]}")  # Limiter la sortie
                else:
                    # Ne pas logger comme erreur critique si c'est juste une erreur de compatibilité Python
                    error_msg = result.stderr or result.stdout or "Unknown error"
                    if "TypeError" in error_msg and "RawConfigParser.get()" in error_msg:
                        log.warning(f"Erreur de compatibilité Python dans pycsw (le patch devrait corriger cela): {package_id}")
                        log.warning(f"Message: {error_msg[:500]}")
                    else:
                        log.error(f"Erreur synchronisation CSW pour dataset {package_id}: {error_msg[:500]}")
                    if result.stdout and len(result.stdout) > 500:
                        log.debug(f"Sortie complète ckan_pycsw: {result.stdout[:1000]}")
                        
            except subprocess.TimeoutExpired:
                log.error(f"Timeout lors de la synchronisation CSW pour dataset: {package_id}")
            except Exception as e:
                # Ne pas faire échouer le système si la synchronisation CSW échoue
                error_msg = str(e)
                if "TypeError" in error_msg and "RawConfigParser.get()" in error_msg:
                    log.warning(f"Erreur de compatibilité Python dans pycsw (le patch devrait corriger cela): {package_id}")
                else:
                    log.error(f"Erreur sync CSW dataset {package_id}: {error_msg}")
                    import traceback
                    log.debug(f"Traceback: {traceback.format_exc()}")
        
        log.info(f"Lancement du thread de synchronisation CSW...")
        thread = threading.Thread(target=_thread_with_session_cleanup(sync_csw_worker), daemon=True)
        thread.start()
        log.info(f"Thread CSW lancé: {thread.name}")
    
    def _generate_mapfile_async(self, dataset_name):
        """
        Génère un mapfile MapServer pour un dataset de manière asynchrone
        Utilise MapfileGenerator directement avec auto_create_geometry=True pour garantir
        que les colonnes geometry PostGIS sont créées automatiquement depuis les colonnes GeoJSON TEXT
        
        Args:
            dataset_name: Nom du dataset
        """
        if not dataset_name:
            return
        
        def generate_worker():
            try:
                log.info(f"Génération mapfile MapServer pour: {dataset_name}")
                
                # Vérifier si le dataset est géospatial
                # IMPORTANT: Pour les harvests, les ressources peuvent être ajoutées après la création du dataset
                # Donc on essaie plusieurs fois avec des délais
                context = {'ignore_auth': True}
                
                # Essayer plusieurs fois pour les harvests et pour laisser le temps à xloader de terminer
                # xloader peut prendre quelques secondes pour charger les données dans le datastore
                max_attempts = 3
                dataset = None
                is_geospatial = False
                is_harvest = False
                
                for attempt in range(max_attempts):
                    try:
                        dataset = get_action('package_show')(context, {'id': dataset_name})
                        
                        # Vérifier si c'est un harvest
                        extras = dataset.get('extras', [])
                        if isinstance(extras, list):
                            for extra in extras:
                                if isinstance(extra, dict) and extra.get('key') in ['harvest_source_id', 'harvest_source_title']:
                                    is_harvest = True
                                    log.debug(f"Dataset {dataset_name} détecté comme moissonné (source: {extra.get('value', 'unknown')})")
                                    break
                        elif isinstance(extras, dict):
                            if extras.get('harvest_source_id') or extras.get('harvest_source_title'):
                                is_harvest = True
                                log.debug(f"Dataset {dataset_name} détecté comme moissonné")
                        
                        # Vérifier si géospatial
                        # IMPORTANT: _is_geospatial_dataset vérifie maintenant les colonnes même si datastore_active=False
                        is_geospatial = self._is_geospatial_dataset(dataset)
                        
                        if is_geospatial:
                            log.debug(f"Dataset {dataset_name} détecté comme géospatial (tentative {attempt+1}/{max_attempts})")
                            break
                        elif attempt < max_attempts - 1:
                            # Attendre un peu pour laisser le temps à xloader de terminer le chargement
                            # Augmenter le délai progressivement (3s, 5s)
                            wait_time = 3 if attempt == 0 else 5
                            log.debug(f"Dataset {dataset_name} non géospatial encore (tentative {attempt+1}/{max_attempts}), attente {wait_time}s pour laisser xloader terminer...")
                            log.info(f"Ressources actuelles: {len(dataset.get('resources', []))}")
                            # Vérifier si certaines ressources ont datastore_active=False (xloader en cours)
                            resources = dataset.get('resources', [])
                            csv_resources = [r for r in resources if r.get('format', '').upper() == 'CSV']
                            if csv_resources:
                                log.debug(f"Ressources CSV trouvées: {len(csv_resources)}, datastore_active: {[r.get('datastore_active', False) for r in csv_resources]}")
                            import time
                            time.sleep(wait_time)
                        else:
                            log.debug(f"Dataset {dataset_name} non géospatial (tentative {attempt+1}/{max_attempts})")
                            break
                    except Exception as e:
                        log.warning(f"Erreur récupération dataset {dataset_name} (tentative {attempt+1}/{max_attempts}): {e}")
                        if attempt < max_attempts - 1:
                            import time
                            time.sleep(2)
                        else:
                            raise
                
                if not dataset:
                    log.error(f"Impossible de récupérer le dataset {dataset_name}")
                    return
                
                if not is_geospatial:
                    log.debug(f"[SUPPRESSION MAPFILE] Dataset {dataset_name} non géospatial après {max_attempts} tentatives → suppression mapfile si existant")
                    # Supprimer le mapfile s'il existe (ex: toutes les ressources ont été supprimées)
                    deleted = self._delete_mapfile_for_dataset(dataset_name)
                    log.info(f"[SUPPRESSION MAPFILE] _delete_mapfile_for_dataset({dataset_name}) = {deleted}")
                    if is_harvest:
                        log.info(f"Note: Pour les harvests, le mapfile sera généré automatiquement lors de la création des ressources géospatiales")
                    return
                
                # Utiliser MapfileGenerator directement au lieu d'appeler le script
                # Cela garantit que auto_create_geometry=True est utilisé
                try:
                    import sys
                    import importlib.util
                    sys.path.insert(0, '/srv/app/ckanext-ogc/scripts')
                    # Charger le module dynamiquement car le nom contient un tiret
                    script_path = '/srv/app/ckanext-ogc/scripts/generate-mapfile.py'
                    spec = importlib.util.spec_from_file_location("generate_mapfile", script_path)
                    generate_mapfile_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(generate_mapfile_module)
                    MapfileGenerator = generate_mapfile_module.MapfileGenerator
                    
                    # Créer une instance avec auto_create_geometry=True pour garantir
                    # la création automatique des colonnes geometry PostGIS
                    # Obtenir ckan_storage_path depuis l'environnement ou la configuration CKAN
                    ckan_storage_path = os.getenv('CKAN_STORAGE_PATH', '/ckan_storage')
                    # Essayer aussi de récupérer depuis la configuration CKAN si disponible
                    try:
                        from ckan.common import config
                        if config.get('ckan.storage_path'):
                            ckan_storage_path = config.get('ckan.storage_path')
                    except Exception:
                        pass
                    
                    generator = MapfileGenerator(
                        ckan_url='http://ckan:5000',
                        ckan_api_key=self.ckan_api_key or '',
                        postgis_host='db',
                        postgis_port=5432,
                        postgis_db='datastore',
                        postgis_user='ckan',
                        postgis_password=os.getenv('POSTGRES_PASSWORD', 'ckan'),
                        mapfiles_dir='/mapserver/mapfiles',
                        auto_create_geometry=True,  # IMPORTANT: Activer la création automatique
                        use_datagis=True,
                        ckan_storage_path=ckan_storage_path
                    )
                    
                    log.info(f"Génération mapfile avec auto_create_geometry=True pour {dataset_name}...")
                    log.info(f"Détails: dataset_id={dataset.get('id')}, title={dataset.get('title', 'N/A')}")
                    
                    # Générer le mapfile (cela créera automatiquement la colonne geometry si nécessaire)
                    try:
                        success = generator.generate_mapfile(dataset)
                        
                        if success:
                            mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
                            log.info(f"Mapfile généré avec succès pour {dataset_name}")
                            log.debug(f"Fichier: {mapfile_path}")
                            
                            # Vérifier que le fichier existe vraiment
                            if os.path.exists(mapfile_path):
                                file_size = os.path.getsize(mapfile_path)
                                log.debug(f"Mapfile vérifié: {file_size} octets")
                            else:
                                log.warning(f"Mapfile généré mais fichier non trouvé: {mapfile_path}")
                        else:
                            log.error(f"Échec de la génération du mapfile pour {dataset_name}")
                            log.error(f"Causes possibles:")
                            log.error(f"   - PostGIS non activé dans datastore")
                            log.error(f"   - Colonne geometry non créée (vérifier les colonnes GeoJSON)")
                            log.error(f"   - Erreur lors de la création de la colonne geometry")
                            log.error(f"   - Aucune ressource géospatiale trouvée")
                            log.error(f"Utilisez le script de diagnostic: /srv/app/scripts/diagnose-mapfile.sh {dataset_name} --fix")
                            # Supprimer l'ancien mapfile s'il existe (ex: toutes les ressources supprimées)
                            log.debug(f"[SUPPRESSION MAPFILE] generate_mapfile a retourné False → suppression mapfile {dataset_name}.map si existant")
                            deleted = self._delete_mapfile_for_dataset(dataset_name)
                            log.info(f"[SUPPRESSION MAPFILE] _delete_mapfile_for_dataset({dataset_name}) = {deleted}")
                    except OSError as os_err:
                        # Logging détaillé de l'erreur OSError
                        log.error(f"OSError lors de la génération du mapfile pour {dataset_name}")
                        log.error(f"   Type d'erreur: {type(os_err).__name__}")
                        log.error(f"   Message: {str(os_err)}")
                        log.error(f"   Errno: {os_err.errno if hasattr(os_err, 'errno') else 'N/A'}")
                        log.error(f"   Strerror: {os_err.strerror if hasattr(os_err, 'strerror') else 'N/A'}")
                        log.error(f"   Filename: {os_err.filename if hasattr(os_err, 'filename') else 'N/A'}")
                        log.error(f"   Code d'erreur: {os_err.errno if hasattr(os_err, 'errno') else 'N/A'}")
                        
                        # Vérifier les permissions et l'espace disque
                        mapfiles_dir = '/mapserver/mapfiles'
                        if os.path.exists(mapfiles_dir):
                            import stat
                            dir_stat = os.stat(mapfiles_dir)
                            log.error(f"Permissions du répertoire {mapfiles_dir}: {oct(stat.S_IMODE(dir_stat.st_mode))}")
                            log.error(f"Propriétaire: {dir_stat.st_uid}:{dir_stat.st_gid}")
                            
                            # Vérifier l'espace disque
                            import shutil
                            total, used, free = shutil.disk_usage(mapfiles_dir)
                            log.error(f"Espace disque: {free / (1024**3):.2f} GB libres sur {total / (1024**3):.2f} GB")
                        else:
                            log.error(f"Répertoire {mapfiles_dir} n'existe pas")
                        
                        import traceback
                        log.error(f"Traceback complet:")
                        log.error(traceback.format_exc())
                        # Fallback vers l'ancienne méthode
                        self._generate_mapfile_via_script(dataset_name)
                        
                except ImportError as e:
                    log.error(f"Impossible d'importer MapfileGenerator: {e}")
                    log.error(f"Fallback vers l'appel du script...")
                    import traceback
                    log.error(f"Traceback: {traceback.format_exc()}")
                    # Fallback vers l'ancienne méthode si l'import échoue
                    self._generate_mapfile_via_script(dataset_name)
                except Exception as e:
                    log.error(f"Erreur lors de la génération directe du mapfile pour {dataset_name}: {e}")
                    log.error(f"Type d'erreur: {type(e).__name__}")
                    import traceback
                    log.error(f"Traceback complet:")
                    log.error(traceback.format_exc())
                    # Fallback vers l'ancienne méthode
                    self._generate_mapfile_via_script(dataset_name)
                        
            except Exception as e:
                log.error(f"Erreur génération mapfile pour {dataset_name}: {e}")
                import traceback
                log.error(traceback.format_exc())
        
        thread = threading.Thread(target=_thread_with_session_cleanup(generate_worker), daemon=True)
        thread.start()
    
    def _generate_mapfile_via_script(self, dataset_name):
        """
        Méthode de fallback pour générer le mapfile via le script en ligne de commande
        """
        mapfile_script = os.getenv('MAPFILE_GENERATOR_SCRIPT', '/srv/app/ckanext-ogc/scripts/generate-mapfile.py')
        
        if not os.path.exists(mapfile_script):
            log.warning(f"Script de génération mapfile non trouvé: {mapfile_script}")
            return
        
        try:
            import subprocess
            
            result = subprocess.run(
                [
                    'python3', mapfile_script,
                    '--dataset', dataset_name,
                    '--ckan-url', 'http://ckan:5000',
                    '--ckan-api-key', self.ckan_api_key or '',
                    '--mapfiles-dir', '/mapserver/mapfiles',
                    '--postgis-host', 'db',
                    '--postgis-port', '5432',
                    '--postgis-db', 'datastore',
                    '--postgis-user', 'ckan',
                    '--postgis-password', os.getenv('POSTGRES_PASSWORD', ''),
                    '--use-datagis',
                    '--auto-create-geometry'  # Ajouter le flag pour la création automatique
                ],
                capture_output=True,
                text=True,
                timeout=120,
                env=dict(os.environ, POSTGRES_PASSWORD=os.getenv('POSTGRES_PASSWORD', ''))
            )
            
            if result.returncode == 0:
                log.info(f"Mapfile généré avec succès pour {dataset_name}")
                if result.stdout:
                    log.debug(f"Script output: {result.stdout[:500]}")
            else:
                log.warning(f"Erreur génération mapfile: {result.stderr[:500]}")
                if result.stdout:
                    log.debug(f"Script stdout: {result.stdout[:500]}")
        except Exception as e:
            log.warning(f"Impossible de générer mapfile via script: {e}")
            import traceback
            log.debug(traceback.format_exc())
    
    def _create_geojson_view_for_shapefile(self, context, resource_dict):
        """
        Crée automatiquement une vue GeoJSON pour les ressources Shapefile ZIP
        afin de permettre leur affichage dans CKAN
        
        Args:
            context: Context CKAN
            resource_dict: Dictionnaire de la ressource
        """
        resource_id = resource_dict.get('id')
        if not resource_id:
            return
        
        format_ = resource_dict.get('format', '').upper()
        url = resource_dict.get('url', '')
        package_id = resource_dict.get('package_id')
        
        # Vérifier si c'est un Shapefile ZIP
        is_shapefile = (
            format_ in ['SHP', 'SHAPEFILE', 'ZIP'] or
            url.lower().endswith('.zip') or
            url.lower().endswith('.shp')
        )
        
        if not is_shapefile:
            return
        
        # Pour les fichiers ZIP, ne pas créer de vue GeoJSON directement
        # car geojson_view ne peut pas parser les fichiers ZIP
        # À la place, utiliser l'endpoint OGC API Features ou WFS si disponible
        is_zip = format_ == 'ZIP' or url.lower().endswith('.zip')
        
        if is_zip:
            # Pour les ZIP, vérifier si le dataset a un mapfile (donc WFS disponible)
            # Si oui, utiliser l'endpoint WFS pour obtenir le GeoJSON
            try:
                dataset = toolkit.get_action('package_show')(
                    {'ignore_auth': True},
                    {'id': package_id}
                )
                dataset_name = dataset.get('name')
                org_name = None
                if dataset.get('organization'):
                    org_name = dataset['organization'].get('name')
                
                # Vérifier si un mapfile existe
                mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
                has_mapfile = os.path.exists(mapfile_path)
                
                if has_mapfile and org_name:
                    # Utiliser geo_view avec WMS au lieu de geojson_view pour les ZIP
                    # car geojson_view essaie de parser directement le fichier ZIP
                    ckan_site_url = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
                    wms_url = f"{ckan_site_url}/wms?map=/mapserver/mapfiles/{dataset_name}.map&LAYERS={dataset_name}&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&FORMAT=image/png&CRS=EPSG:3857&WIDTH=800&HEIGHT=600&BBOX=-20037508.34,-20037508.34,20037508.34,20037508.34"
                    geojson_url = f"{ckan_site_url}/maps/{org_name}/collections/{dataset_name}/items?limit=1000"
                    
                    # Vérifier si une vue existe déjà
                    try:
                        views = toolkit.get_action('resource_view_list')(
                            {'ignore_auth': True},
                            {'id': resource_id}
                        )
                        for view in views:
                            view_type = view.get('view_type')
                            # Supprimer les anciennes vues geojson_view qui pointent vers ZIP
                            if view_type == 'geojson_view':
                                try:
                                    toolkit.get_action('resource_view_delete')(
                                        {'ignore_auth': True},
                                        {'id': view['id']}
                                    )
                                    log.info(f"Vue geojson_view supprimée pour {resource_id} (remplacée par geo_view)")
                                except Exception as e:
                                    log.warning(f"Erreur lors de la suppression de la vue: {e}")
                            # Si une vue geo_view existe déjà, vérifier/mettre à jour l'URL
                            elif view_type == 'geo_view':
                                # geo_view utilise wms_url dans les paramètres
                                current_wms = view.get('wms_url') or view.get('resource_url', '')
                                if geojson_url not in current_wms and dataset_name not in current_wms:
                                    # Mettre à jour avec l'URL WMS
                                    toolkit.get_action('resource_view_update')(
                                        {'ignore_auth': True},
                                        {
                                            'id': view['id'],
                                            'wms_url': wms_url,
                                            'wms_layer': dataset_name
                                        }
                                    )
                                    log.info(f"Vue geo_view mise à jour avec l'URL WMS pour {resource_id}")
                                else:
                                    log.info(f"Vue geo_view existe déjà avec la bonne URL pour {resource_id}")
                                return
                    except Exception as e:
                        log.warning(f"Erreur lors de la vérification des vues existantes: {e}")
                    
                    # Créer une vue geo_view avec WMS (meilleure pour les fichiers ZIP)
                    view_data = {
                        'resource_id': resource_id,
                        'title': 'Carte',
                        'view_type': 'geo_view',
                        'description': 'Vue cartographique générée automatiquement depuis le Shapefile',
                        'wms_url': wms_url,
                        'wms_layer': dataset_name
                    }
                    
                    toolkit.get_action('resource_view_create')(
                        {'ignore_auth': True},
                        view_data
                    )
                    log.info(f"Vue geo_view créée avec URL WMS pour la ressource ZIP {resource_id}")
                    return
                else:
                    log.debug(f"Pas de mapfile pour {dataset_name}, impossible de créer une vue GeoJSON pour ZIP")
                    return
            except Exception as e:
                log.warning(f"Erreur lors de la création de la vue GeoJSON pour ZIP: {e}")
                return
        
        # Pour les fichiers .shp non-ZIP, créer une vue GeoJSON normale
        # (geojson_view peut gérer les fichiers .shp directs)
        if not HAS_FIONA:
            log.debug("fiona non disponible, impossible de créer une vue GeoJSON pour Shapefile")
            return
        
        # Vérifier si une vue GeoJSON existe déjà
        try:
            views = toolkit.get_action('resource_view_list')(
                {'ignore_auth': True},
                {'id': resource_id}
            )
            for view in views:
                if view.get('view_type') == 'geojson_view':
                    log.info(f"Vue GeoJSON existe déjà pour la ressource {resource_id}")
                    return
        except Exception as e:
            log.warning(f"Erreur lors de la vérification des vues existantes: {e}")
        
        # Créer une vue GeoJSON standard pour les fichiers .shp non-ZIP
        try:
            view_data = {
                'resource_id': resource_id,
                'title': 'Carte (GeoJSON)',
                'view_type': 'geojson_view',
                'description': 'Vue cartographique générée automatiquement depuis le Shapefile'
            }
            
            toolkit.get_action('resource_view_create')(
                {'ignore_auth': True},
                view_data
            )
            log.info(f"Vue GeoJSON créée pour la ressource Shapefile {resource_id}")
        except Exception as e:
            log.warning(f"Impossible de créer une vue GeoJSON: {e}")
            # Ne pas échouer si la vue ne peut pas être créée (peut déjà exister)
            pass
    
    def _import_geospatial_to_datagis(self):
        """
        Importe les fichiers géospatiaux non-datastore dans datagis
        """
        try:
            import subprocess
            import os
            
            # Récupérer le chemin de storage depuis la config CKAN
            try:
                from ckan.common import config as ckan_config
                ckan_storage_path = ckan_config.get('ckan.storage_path', '/var/lib/ckan/default')
            except Exception:
                ckan_storage_path = os.getenv('CKAN_STORAGE_PATH', '/var/lib/ckan/default')
            
            script_path = os.getenv('IMPORT_DATAGIS_SCRIPT', '/usr/local/bin/import-geospatial-to-datagis.py')
            
            # Vérifier si le script existe
            if os.path.exists(script_path):
                log.debug(f"Script trouvé: {script_path}")
                log.info(f"Paramètres:")
                log.info(f"   - CKAN URL: {self.ckan_url}")
                log.info(f"   - Datagis DB: {os.getenv('DATAGIS_DB', 'datagis')}")
                log.info(f"   - PostGIS Host: {os.getenv('POSTGRES_HOST', 'db')}:{os.getenv('POSTGRES_PORT', '5432')}")
                log.info(f"   - Storage Path: {ckan_storage_path}")
                
                # Utiliser Popen pour afficher les logs en temps réel
                cmd = [
                    'python3', script_path,
                    '--ckan-url', self.ckan_internal_url,
                    '--ckan-api-key', self.ckan_api_key or '',
                    '--datagis-db', os.getenv('DATAGIS_DB', 'datagis'),
                    '--postgis-host', os.getenv('POSTGRES_HOST', 'db'),
                    '--postgis-port', os.getenv('POSTGRES_PORT', '5432'),
                    '--postgis-user', os.getenv('POSTGRES_USER', 'ckan'),
                    '--postgis-password', os.getenv('POSTGRES_PASSWORD', ''),
                    '--ckan-storage-path', ckan_storage_path  # Chemin de storage CKAN
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,  # Line buffered
                    universal_newlines=True
                )
                
                # Lire les logs en temps réel
                log_lines = []
                try:
                    for line in iter(process.stdout.readline, ''):
                        if line:
                            line = line.rstrip()
                            log_lines.append(line)
                            # Logger les lignes importantes en temps réel
                            if any(marker in line for marker in ['', '', '', '', '', '', '', '', '', '', '', 'ERROR', 'WARNING', 'importé', 'Importé', 'ressources importées', 'DÉBUT IMPORT', 'IMPORT.*RÉUSSI', 'Table:', 'ogr2ogr', 'Fichier trouvé', 'Fichier normalisé', 'DÉBUT IMPORT RESSOURCE']):
                                log.info(f"   {line}")
                            # Logger aussi toutes les lignes toutes les 50 lignes pour voir la progression
                            elif len(log_lines) % 50 == 0:
                                log.debug(f"   [{len(log_lines)} lignes] {line}")
                    
                    process.wait(timeout=3600)  # 1 heure de timeout
                    result = subprocess.CompletedProcess(
                        cmd,
                        process.returncode,
                        '\n'.join(log_lines),
                        ''
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    log.error(f"Timeout import datagis après 3600 secondes (1 heure)")
                    raise
                
                if result.returncode == 0:
                    log.info(f"Import datagis réussi (code: {result.returncode})")
                    log.info(f"Total lignes de log: {len(log_lines)}")
                else:
                    log.warning(f"Erreur import datagis (code: {result.returncode})")
                    # Chercher les erreurs dans les logs
                    for line in log_lines[-100:]:  # Dernières 100 lignes
                        if any(marker in line for marker in ['', 'ERROR', 'Erreur', 'Échec', 'does not exist', 'failed', 'Failed']):
                            log.warning(f"   {line}")
            else:
                log.warning(f"Script import datagis non trouvé: {script_path}")
                log.debug(f"L'import datagis sera ignoré. Pour l'activer:")
                log.debug(f"   1. Vérifier que le script existe: {script_path}")
                log.info(f"   2. Ou définir IMPORT_DATAGIS_SCRIPT avec le chemin correct")
        except Exception as e:
            log.error(f"Erreur import datagis: {e}")
            import traceback
            log.error(traceback.format_exc())
    
    def _generate_all_mapfiles_at_startup(self):
        """
        Génère tous les mapfiles au démarrage
        Utilise un verrou de fichier pour éviter que plusieurs workers lancent la génération en parallèle
        """
        import fcntl
        import subprocess
        import os
        import time
        
        # Chemin du verrou de fichier
        lock_file_path = '/tmp/generate_mapfiles_startup.lock'
        mapfile_script = os.getenv('MAPFILE_GENERATOR_SCRIPT', '/srv/app/ckanext-ogc/scripts/generate-mapfile.py')
        timeout_seconds = int(os.getenv('MAPFILE_GENERATION_TIMEOUT', '1800'))  # 30 minutes par défaut
        
        # Vérifier si un processus est déjà en cours
        try:
            lock_file = open(lock_file_path, 'w')
            try:
                # Essayer d'acquérir le verrou (non-bloquant)
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Un autre processus a déjà le verrou
                lock_file.close()
                log.debug(" Génération de mapfiles déjà en cours par un autre worker, skip...")
                return
        except Exception as e:
            log.warning(f"Erreur lors de l'acquisition du verrou: {e}, continuation...")
            lock_file = None
        
        try:
            if os.path.exists(mapfile_script):
                log.info(f"Démarrage de la génération de tous les mapfiles... (timeout: {timeout_seconds}s)")
                
                # Lancer le processus avec stdout/stderr en temps réel pour afficher la progression
                process = subprocess.Popen(
                    [
                        'python3', mapfile_script,
                        '--ckan-url', self.ckan_internal_url,
                        '--ckan-api-key', self.ckan_api_key or '',
                        '--use-datagis',
                        '--auto-create-geometry'
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Combiner stderr dans stdout
                    text=True,
                    bufsize=1,  # Ligne par ligne
                    universal_newlines=True
                )
                
                # Lire la sortie en temps réel et logger
                output_lines = []
                start_time = time.time()
                last_progress_log_time = start_time
                
                try:
                    while True:
                        # Vérifier le timeout avant de lire
                        elapsed = time.time() - start_time
                        if elapsed > timeout_seconds:
                            process.kill()
                            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
                        
                        # Lire une ligne avec timeout
                        line = process.stdout.readline()
                        if not line:
                            if process.poll() is not None:
                                break  # Processus terminé
                            time.sleep(0.1)
                            continue
                        
                        line = line.rstrip()
                        output_lines.append(line)
                        
                        # Logger les lignes importantes en temps réel
                        if any(marker in line for marker in ['', '', '', '', '', 'ERROR', 'WARNING']):
                            log.info(f"   {line}")
                        elif time.time() - last_progress_log_time > 10:  # Afficher une ligne toutes les 10 secondes
                            if line.strip():
                                log.info(f"   {line} ({int(elapsed)}s)")
                                last_progress_log_time = time.time()
                    
                    # Attendre la fin du processus
                    return_code = process.wait()
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                
                # Créer un objet résultat pour la compatibilité avec le code existant
                class Result:
                    def __init__(self, returncode, stdout, stderr=''):
                        self.returncode = returncode
                        self.stdout = stdout
                        self.stderr = stderr
                
                result = Result(return_code, '\n'.join(output_lines), '')
                
                if result.returncode == 0:
                    # Analyser la sortie pour détecter les erreurs même si returncode == 0
                    # output_lines a déjà été rempli pendant l'exécution
                    error_lines = [line for line in output_lines if any(marker in line for marker in ['ERROR', '', '', 'WARNING', 'Erreur', 'Échec'])]
                    
                    if error_lines:
                        log.warning(f"Génération mapfiles terminée avec des avertissements:")
                        for error_line in error_lines[-20:]:  # Dernières 20 lignes d'erreur
                            log.warning(f"   {error_line}")
                    else:
                        log.info(f"Génération mapfiles réussie sans erreurs")
                    
                    # Afficher un résumé des mapfiles générés
                    success_lines = [line for line in output_lines if 'MAPFILE' in line or 'Mapfile' in line]
                    if success_lines:
                        log.info(f"Mapfiles générés avec succès: {len(success_lines)}")
                        for success_line in success_lines[-10:]:  # Derniers 10 mapfiles générés
                            log.info(f"   {success_line}")
                else:
                    log.error(f"Erreur génération mapfiles (code retour: {result.returncode})")
                    if result.stderr:
                        log.error(f"Erreurs stderr:")
                        for line in result.stderr.split('\n')[-30:]:  # Dernières 30 lignes
                            if line.strip():
                                log.error(f"   {line}")
                    if result.stdout:
                        # Chercher les erreurs dans stdout aussi
                        error_lines = [line for line in result.stdout.split('\n') if any(marker in line for marker in ['ERROR', '', 'Erreur', 'Échec', 'Traceback'])]
                        if error_lines:
                            log.error(f"Erreurs dans stdout:")
                            for line in error_lines[-30:]:  # Dernières 30 lignes d'erreur
                                log.error(f"   {line}")
            else:
                log.warning(f"Script generate-mapfile non trouvé: {mapfile_script}")
        except subprocess.TimeoutExpired:
            log.error(f"Timeout lors de la génération des mapfiles (dépassement de {timeout_seconds} secondes)")
            log.error(f"Augmenter le timeout avec: export MAPFILE_GENERATION_TIMEOUT=3600")
        except Exception as e:
            log.error(f"Erreur génération mapfiles: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
        finally:
            # Libérer le verrou
            if lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                    lock_file.close()
                    # Supprimer le fichier de verrou
                    try:
                        os.unlink(lock_file_path)
                    except Exception:
                        pass
                except Exception:
                    pass
    
    def _import_resource_to_datagis_async(self, resource_dict):
        """
        Importe une ressource géospatiale dans datagis de manière asynchrone
        
        Args:
            resource_dict: Dictionnaire de la ressource
        """
        def import_worker():
            try:
                import subprocess
                import os
                import time
                
                resource_id = resource_dict.get('id')
                if not resource_id:
                    log.warning("Pas d'ID de ressource pour l'import datagis")
                    return
                
                # Verrou par ressource pour éviter imports concurrents (after_resource_create + after_resource_update)
                with self._datagis_import_locks_guard:
                    if resource_id not in self._datagis_import_locks:
                        self._datagis_import_locks[resource_id] = threading.Lock()
                    resource_lock = self._datagis_import_locks[resource_id]
                with resource_lock:
                    # Attendre un peu pour que le fichier soit complètement uploadé
                    time.sleep(2)
                    
                    script_path = os.getenv('IMPORT_DATAGIS_SCRIPT', '/usr/local/bin/import-geospatial-to-datagis.py')
                    
                    if os.path.exists(script_path):
                        log.info(f"Lancement import datagis pour ressource {resource_id}...")
                        
                        # Récupérer le chemin de storage depuis la config CKAN
                        try:
                            from ckan.common import config as ckan_config
                            ckan_storage_path = ckan_config.get('ckan.storage_path', '/var/lib/ckan/default')
                        except Exception:
                            ckan_storage_path = os.getenv('CKAN_STORAGE_PATH', '/var/lib/ckan/default')
                        
                        # Lancer le script avec l'ID de la ressource spécifique
                        # Le script peut accepter --resource-id pour importer une seule ressource
                        cmd = [
                            'python3', script_path,
                            '--ckan-url', self.ckan_internal_url,
                            '--ckan-api-key', self.ckan_api_key or '',
                            '--datagis-db', os.getenv('DATAGIS_DB', 'datagis'),
                            '--postgis-host', os.getenv('POSTGRES_HOST', 'db'),
                            '--postgis-port', os.getenv('POSTGRES_PORT', '5432'),
                            '--postgis-user', os.getenv('POSTGRES_USER', 'ckan'),
                            '--postgis-password', os.getenv('POSTGRES_PASSWORD', ''),
                            '--ckan-storage-path', ckan_storage_path,  # Chemin de storage CKAN
                            '--resource-id', resource_id  # Importer seulement cette ressource
                        ]
                        
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=300  # 5 minutes max pour un ZIP
                        )
                        
                        if result.returncode == 0:
                            log.info(f"Import datagis réussi pour ressource {resource_id}")
                            # Déclencher la génération du mapfile pour ce dataset (table datagis maintenant disponible)
                            package_id = resource_dict.get('package_id')
                            if package_id:
                                try:
                                    context_internal = {'ignore_auth': True}
                                    dataset = get_action('package_show')(context_internal, {'id': package_id})
                                    dataset_name = dataset.get('name')
                                    if dataset_name:
                                        self._generate_mapfile_async(dataset_name)
                                        log.info(f"Génération mapfile déclenchée après import datagis pour {dataset_name}")
                                except Exception as mapfile_err:
                                    log.warning(f"Impossible de déclencher la génération mapfile après import datagis: {mapfile_err}")
                            # Combiner stdout et stderr car logging écrit sur stderr
                            all_output = ""
                            if result.stdout:
                                all_output += result.stdout
                            if result.stderr:
                                if all_output:
                                    all_output += "\n"
                                all_output += result.stderr
                            
                            if all_output:
                                log.info("Détails de l'import:")
                                for line in all_output.split('\n'):
                                    # Logger les lignes avec des marqueurs importants
                                    if any(marker in line for marker in ['', '', '', '', '', '', '', '', '', 'ERROR', 'WARNING', 'importé', 'Importé', 'DÉBUT IMPORT', 'IMPORT.*RÉUSSI', 'Table:', 'ogr2ogr']):
                                        log.info(f"   {line}")
                        else:
                            log.warning(f"Import datagis échoué pour ressource {resource_id}")
                            # Ne pas bloquer si datagis n'existe pas - c'est optionnel
                            # Combiner stdout et stderr pour diagnostiquer (limite 2000 car pour cause réelle)
                            all_output = ""
                            if result.stdout:
                                all_output += result.stdout
                            if result.stderr:
                                if all_output:
                                    all_output += "\n"
                                all_output += result.stderr
                            
                            if all_output:
                                error_text = all_output[:2000]
                                log.warning(f"   Erreur (sortie script): {error_text}")
                                # Si c'est juste que la base n'existe pas, c'est OK
                                if 'does not exist' in error_text.lower() or 'database' in error_text.lower():
                                    log.debug(f"La base datagis n'existe pas encore - ce n'est pas bloquant")
                                    log.info(f"Pour créer la base: docker exec -it db psql -U {os.getenv('POSTGRES_USER', 'ckan')} -c \"CREATE DATABASE datagis OWNER {os.getenv('POSTGRES_USER', 'ckan')} ENCODING 'utf-8';\"")
                                    log.info(f"Puis activer PostGIS: docker exec -it db psql -U {os.getenv('POSTGRES_USER', 'ckan')} -d datagis -c \"CREATE EXTENSION IF NOT EXISTS postgis;\"")
                                else:
                                    # Chercher les erreurs dans la sortie combinée
                                    for line in all_output.split('\n'):
                                        if any(marker in line for marker in ['', 'ERROR', 'Erreur', 'Échec', 'failed', 'Failed']):
                                            log.warning(f"   {line}")
                    else:
                        log.warning(f"Script import datagis non trouvé: {script_path}")
                        log.info(f"Pour traiter les ZIP contenant des shapefiles:")
                        log.debug(f"   1. Le format doit être détecté comme 'ZIP'")
                        log.info(f"   2. Le script {script_path} doit exister")
                        log.info(f"   3. Le script extrait le .shp du ZIP et l'importe dans PostGIS/datagis")
            except subprocess.TimeoutExpired:
                log.error(f"Timeout lors de l'import datagis pour ressource {resource_dict.get('id')}")
            except Exception as e:
                log.error(f"Erreur import datagis async: {e}")
                import traceback
                log.error(traceback.format_exc())
        
        thread = threading.Thread(target=_thread_with_session_cleanup(import_worker), daemon=True)
        thread.start()
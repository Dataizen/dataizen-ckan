"""
Jobs asynchrones pour les synchronisations
"""
import json
import logging
import os
import re
import subprocess
import time
from typing import Dict, Any

log = logging.getLogger(__name__)

# Préfixes Redis pour la progression (lus par les actions status)
REDIS_DATAGIS_PROGRESS_PREFIX = "datagis_progress:"
REDIS_PYGEOAPI_PROGRESS_PREFIX = "pygeoapi_progress:"
REDIS_MAPFILE_PROGRESS_PREFIX = "mapfile_progress:"
REDIS_PROGRESS_TTL = 3600  # 1 heure


def sync_pygeoapi_job():
    """
    Job asynchrone pour la synchronisation pygeoapi
    Cette fonction est exécutée par le worker RQ
    """
    log.info("[pygeoapi job] Entrée dans sync_pygeoapi_job (worker a bien appelé la fonction)")
    job_id = None
    redis_conn = None
    try:
        from rq import get_current_job
        job = get_current_job()
        if job:
            job_id = job.id
            redis_conn = getattr(job, 'connection', None)
    except Exception:
        pass

    def _progress_callback(current, total, name=None):
        message = name if name else f"{current}/{total} collections synchronisées"
        log.info("[pygeoapi sync] %s", message)  # Toujours visible dans les logs CKAN
        if redis_conn and job_id:
            try:
                payload = json.dumps({
                    'current': current,
                    'total': total,
                    'message': message
                })
                redis_conn.setex(
                    REDIS_PYGEOAPI_PROGRESS_PREFIX + job_id,
                    REDIS_PROGRESS_TTL,
                    payload
                )
            except Exception as e:
                log.debug("Redis pygeoapi progress write failed: %s", e)

    try:
        log.info("Démarrage de la synchronisation pygeoapi (job asynchrone)")
        _progress_callback(0, 1, "Démarrage de la synchronisation...")
        # Utiliser CKANSync : une seule mise à jour du fichier de configuration
        # puis redémarrage de pygeoapi (pas de backup par dataset).
        try:
            # 1) Import direct depuis le package ckan_provider (installé dans site-packages)
            # 2) Fallback: chemin copié par le Dockerfile (/srv/app/pygeoapi-providers/ckan_provider)
            try:
                from ckan_provider.ckan_sync import CKANSync
            except (ImportError, ModuleNotFoundError):
                import sys
                fallback_path = "/srv/app/pygeoapi-providers/ckan_provider"
                if fallback_path not in sys.path and os.path.isdir(fallback_path):
                    sys.path.insert(0, os.path.dirname(fallback_path))
                from ckan_provider.ckan_sync import CKANSync
            log.info("CKANSync chargé (ckan_provider)")
            ckan_url = os.getenv("CKAN_URL", "http://ckan:5000")
            ckan_api_key = os.getenv("CKAN_API_KEY", "")
            pygeoapi_config_path = os.getenv("PYGEOAPI_CONFIG_PATH", "/srv/app/pygeoapi/local.config.yml")
            pygeoapi_restart_cmd = os.getenv("PYGEOAPI_RESTART_CMD", "/srv/app/restart-pygeoapi.sh")
            log.info("[pygeoapi sync] CKAN=%s, config=%s", ckan_url, pygeoapi_config_path)
            sync = CKANSync(
                ckan_url=ckan_url,
                ckan_api_key=ckan_api_key,
                pygeoapi_config_path=pygeoapi_config_path,
                pygeoapi_restart_cmd=pygeoapi_restart_cmd,
            )
            log.info("[pygeoapi sync] Appel CKANSync.sync() (découverte datasets → update config → restart)")
            success = sync.sync(progress_callback=_progress_callback)
            log.info("[pygeoapi sync] CKANSync.sync() terminé, success=%s", success)
        except (ImportError, FileNotFoundError, ModuleNotFoundError) as e:
            log.warning("CKANSync indisponible (%s), fallback OGCSynchronizer (sync par dataset)", e)
            from ckanext.ogc.sync import OGCSynchronizer
            ckan_url = os.getenv("CKAN_URL", "http://ckan:5000")
            ckan_api_key = os.getenv("CKAN_API_KEY", "")
            pygeoapi_config_path = os.getenv("PYGEOAPI_CONFIG_PATH", "/srv/app/pygeoapi/local.config.yml")
            synchronizer = OGCSynchronizer(
                ckan_url=ckan_url,
                ckan_api_key=ckan_api_key,
                pygeoapi_config_path=pygeoapi_config_path,
                require_ckan_ready=False,
                trigger_reindex_if_needed=False,
            )
            synchronizer.sync_all_datasets(progress_callback=_progress_callback)
            success = True
        if not success:
            raise RuntimeError("Synchronisation pygeoapi a échoué")
        
        if redis_conn and job_id:
            try:
                redis_conn.delete(REDIS_PYGEOAPI_PROGRESS_PREFIX + job_id)
            except Exception:
                pass
        log.info("Synchronisation pygeoapi terminée avec succès")
        return {
            'success': True,
            'message': 'Synchronisation pygeoapi terminée avec succès'
        }
    except Exception as e:
        if redis_conn and job_id:
            try:
                redis_conn.delete(REDIS_PYGEOAPI_PROGRESS_PREFIX + job_id)
            except Exception:
                pass
        error_msg = str(e)
        log.error(f"Erreur synchronisation pygeoapi: {error_msg}")
        import traceback
        log.error(traceback.format_exc())
        return {
            'success': False,
            'error': error_msg
        }


def sync_mapserver_job():
    """
    Job asynchrone pour la synchronisation MapServer (génération des mapfiles)
    Cette fonction est exécutée par le worker RQ
    """
    try:
        log.info("Démarrage de la synchronisation MapServer (job asynchrone)")
        
        # Lancer le script de génération des mapfiles
        script_path = '/usr/local/bin/generate-mapfile.py'
        
        if not os.path.exists(script_path):
            # Chercher dans d'autres emplacements possibles
            possible_paths = [
                '/srv/app/ckanext-ogc/scripts/generate-mapfile.py',
                '/srv/app/ckanext-ogc/ckanext/ogc/scripts/generate-mapfile.py',
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    script_path = path
                    break
            else:
                raise FileNotFoundError("Script generate-mapfile.py non trouvé")
        
        # Récupérer les variables d'environnement pour la configuration
        ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        
        log.info(f"Script: {script_path}")
        log.info(f"CKAN URL: {ckan_url}")
        
        # Construire la commande (-u = sortie non bufferisée pour voir les logs en direct)
        cmd = [
            'python3', '-u', script_path,
            '--ckan-url', ckan_url,
            '--ckan-api-key', ckan_api_key or '',
            '--use-datagis',
            '--auto-create-geometry'
        ]
        
        log.info(f"Commande complète: {' '.join(cmd[:5])} ... [paramètres masqués]")
        log.info(f"Démarrage du script...")
        
        job_id = None
        redis_conn = None
        try:
            from rq import get_current_job
            job = get_current_job()
            if job:
                job_id = job.id
                redis_conn = getattr(job, 'connection', None)
        except Exception:
            pass
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        output_lines = []
        start_time = time.time()
        last_log_time = start_time
        
        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed > 3600:
                    process.kill()
                    raise subprocess.TimeoutExpired(cmd, 3600)
                
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue
                
                line = line.rstrip()
                output_lines.append(line)
                
                # Parser MAPFILE_PROGRESS: N/Total et écrire dans Redis pour l'UI
                if redis_conn and job_id and 'MAPFILE_PROGRESS:' in line:
                    m = re.search(r'MAPFILE_PROGRESS:\s*(\d+)/(\d+)', line)
                    if m:
                        try:
                            payload = json.dumps({
                                'current': int(m.group(1)),
                                'total': int(m.group(2)),
                                'message': f"{m.group(1)}/{m.group(2)} mapfiles traités"
                            })
                            redis_conn.setex(
                                REDIS_MAPFILE_PROGRESS_PREFIX + job_id,
                                REDIS_PROGRESS_TTL,
                                payload
                            )
                        except Exception as e:
                            log.debug("Redis mapfile progress write failed: %s", e)
                
                if any(marker in line for marker in ['', '', '', '', '', 'ERROR', 'WARNING', 'INFO', 'MAPFILE_PROGRESS']):
                    log.info(f"{line}")
                elif time.time() - last_log_time > 10:
                    if line.strip():
                        log.info(f"{line}")
                        last_log_time = time.time()
            
            return_code = process.wait()
            output = '\n'.join(output_lines)
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_MAPFILE_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            
            log.info(f"Script terminé avec code retour: {return_code}")
            if output:
                log.info(f"Sortie complète ({len(output_lines)} lignes)")
                # Afficher les dernières lignes
                if len(output_lines) > 0:
                    log.info(f"Dernières lignes: {chr(10).join(output_lines[-10:])}")
        except subprocess.TimeoutExpired:
            process.kill()
            output = '\n'.join(output_lines)
            log.error("Timeout lors de la synchronisation MapServer (délai dépassé)")
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_MAPFILE_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {
                'success': False,
                'error': 'Timeout - la synchronisation prend trop de temps (délai: 1 heure)',
                'output': output
            }
        
        if return_code == 0:
            # Analyser la sortie pour compter les mapfiles générés (une ligne "Mapfile généré pour" par dataset)
            success_count = len([line for line in output_lines if 'Mapfile généré pour' in line])
            error_count = len([line for line in output_lines if '' in line or 'ERROR' in line])
            
            message = f'Synchronisation MapServer terminée avec succès'
            if success_count > 0:
                message += f' ({success_count} mapfile(s) généré(s))'
            if error_count > 0:
                message += f' ({error_count} erreur(s) détectée(s))'
            
            log.info(f"{message}")
            return {
                'success': True,
                'message': message,
                'output': output,
                'success_count': success_count,
                'error_count': error_count
            }
        else:
            error_msg = output or 'Erreur inconnue'
            error_lines = error_msg.split('\n')[-10:] if error_msg else []
            log.error(f"Erreur lors de la génération des mapfiles (code: {return_code})")
            if error_lines:
                log.error(f"Dernières lignes d'erreur: {chr(10).join(error_lines)}")
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_MAPFILE_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {
                'success': False,
                'error': '\n'.join(error_lines) if error_lines else f'Erreur inconnue (code retour: {return_code})',
                'output': output
            }
    except subprocess.TimeoutExpired:
        log.error("Timeout lors de la synchronisation MapServer (délai dépassé)")
        return {
            'success': False,
            'error': 'Timeout - la synchronisation prend trop de temps (délai: 1 heure)'
        }
    except Exception as e:
        error_msg = str(e)
        log.error(f"Erreur synchronisation mapserver: {error_msg}")
        import traceback
        log.error(traceback.format_exc())
        return {
            'success': False,
            'error': error_msg
        }


def sync_mapserver_dataset_job(dataset_name: str):
    """
    Job asynchrone pour générer le mapfile d'un seul dataset.
    Appelé depuis la page dataset (bouton « Générer mapfile »).
    """
    if not dataset_name or not isinstance(dataset_name, str):
        log.error("sync_mapserver_dataset_job: dataset_name requis")
        return {'success': False, 'error': 'dataset_name requis'}
    dataset_name = dataset_name.strip()
    try:
        log.info("Génération mapfile pour le dataset: %s", dataset_name)
        script_path = '/usr/local/bin/generate-mapfile.py'
        if not os.path.exists(script_path):
            for path in ['/srv/app/ckanext-ogc/scripts/generate-mapfile.py',
                         '/srv/app/ckanext-ogc/ckanext/ogc/scripts/generate-mapfile.py']:
                if os.path.exists(path):
                    script_path = path
                    break
            else:
                raise FileNotFoundError("Script generate-mapfile.py non trouvé")
        ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        cmd = [
            'python3', '-u', script_path,
            '--ckan-url', ckan_url,
            '--ckan-api-key', ckan_api_key or '',
            '--dataset', dataset_name,
            '--use-datagis',
            '--auto-create-geometry'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        output = (result.stdout or '') + (result.stderr or '')
        if result.returncode == 0:
            log.info("Mapfile généré pour %s", dataset_name)
            return {'success': True, 'message': f'Mapfile généré pour le dataset {dataset_name}', 'output': output}
        log.warning("Génération mapfile %s: code %s", dataset_name, result.returncode)
        return {'success': False, 'error': output or f'Code retour {result.returncode}', 'output': output}
    except Exception as e:
        log.error("Erreur génération mapfile pour %s: %s", dataset_name, e)
        return {'success': False, 'error': str(e)}


def sync_pygeoapi_dataset_job(dataset_id: str):
    """
    Job asynchrone pour synchroniser un seul dataset vers pygeoapi.
    Appelé depuis la page dataset (bouton « Synchroniser pygeoapi »).
    """
    if not dataset_id or not isinstance(dataset_id, str):
        log.error("sync_pygeoapi_dataset_job: dataset_id requis")
        return {'success': False, 'error': 'dataset_id requis'}
    dataset_id = dataset_id.strip()
    try:
        log.info("Synchronisation pygeoapi pour le dataset: %s", dataset_id)
        try:
            from ckan_provider.ckan_sync import CKANSync
        except (ImportError, ModuleNotFoundError):
            import sys
            fallback = "/srv/app/pygeoapi-providers/ckan_provider"
            if fallback not in sys.path and os.path.isdir(fallback):
                sys.path.insert(0, os.path.dirname(fallback))
            from ckan_provider.ckan_sync import CKANSync
        ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        pygeoapi_config_path = os.getenv('PYGEOAPI_CONFIG_PATH', '/srv/app/pygeoapi/local.config.yml')
        pygeoapi_restart_cmd = os.getenv('PYGEOAPI_RESTART_CMD', '/srv/app/restart-pygeoapi.sh')
        sync = CKANSync(
            ckan_url=ckan_url,
            ckan_api_key=ckan_api_key,
            pygeoapi_config_path=pygeoapi_config_path,
            pygeoapi_restart_cmd=pygeoapi_restart_cmd,
        )
        result = sync.sync_dataset(dataset_id)
        if result.get('success') or result.get('updated'):
            log.info("Pygeoapi synchronisé pour %s", dataset_id)
            return {'success': True, 'message': f'Pygeoapi synchronisé pour le dataset {dataset_id}'}
        log.warning("sync_dataset %s: %s", dataset_id, result)
        return {'success': False, 'error': result.get('error', str(result))}
    except Exception as e:
        log.error("Erreur sync pygeoapi pour %s: %s", dataset_id, e)
        import traceback
        log.error(traceback.format_exc())
        return {'success': False, 'error': str(e)}


def sync_datagis_dataset_job(package_id: str):
    """
    Job asynchrone pour importer dans datagis les ressources géospatiales d'un seul dataset.
    Appelé depuis la page dataset (bouton « Import Datagis »).
    """
    if not package_id or not isinstance(package_id, str):
        log.error("sync_datagis_dataset_job: package_id requis")
        return {'success': False, 'error': 'package_id requis'}
    package_id = package_id.strip()
    try:
        log.info("Import datagis pour le dataset: %s", package_id)
        script_path = os.getenv('IMPORT_DATAGIS_SCRIPT', '/usr/local/bin/import-geospatial-to-datagis.py')
        if not os.path.exists(script_path):
            for path in ['/srv/app/ckan/scripts/import-geospatial-to-datagis.py',
                         '/srv/app/scripts/import-geospatial-to-datagis.py']:
                if os.path.exists(path):
                    script_path = path
                    break
            else:
                raise FileNotFoundError("Script import-geospatial-to-datagis.py non trouvé")
        ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        postgis_host = os.getenv('POSTGRES_HOST', 'db')
        postgis_port = os.getenv('POSTGRES_PORT', '5432')
        datagis_db = os.getenv('DATAGIS_DB', 'datagis')
        postgis_user = os.getenv('POSTGRES_USER', 'ckan')
        postgis_password = os.getenv('POSTGRES_PASSWORD', 'ckan')
        ckan_storage_path = os.getenv('CKAN_STORAGE_PATH', '/ckan_storage')
        cmd = [
            'python3', script_path,
            '--ckan-url', ckan_url,
            '--ckan-api-key', ckan_api_key or '',
            '--package-id', package_id,
            '--datagis-db', datagis_db,
            '--postgis-host', postgis_host,
            '--postgis-port', postgis_port,
            '--postgis-user', postgis_user,
            '--postgis-password', postgis_password or '',
            '--ckan-storage-path', ckan_storage_path
        ]

        job_id = None
        redis_conn = None
        try:
            from rq import get_current_job
            job = get_current_job()
            if job:
                job_id = job.id
                redis_conn = getattr(job, 'connection', None)
        except Exception:
            pass

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        output_lines = []
        start_time = time.time()
        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed > 3600:
                    process.kill()
                    raise subprocess.TimeoutExpired(cmd, 3600)

                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue

                line = line.rstrip()
                output_lines.append(line)

                if redis_conn and job_id and 'DATAGIS_PROGRESS:' in line:
                    m = re.search(r'DATAGIS_PROGRESS:\s*(\d+)/(\d+)', line)
                    if m:
                        try:
                            payload = json.dumps({
                                'current': int(m.group(1)),
                                'total': int(m.group(2)),
                                'message': f"{m.group(1)}/{m.group(2)} ressources traitees"
                            })
                            redis_conn.setex(
                                REDIS_DATAGIS_PROGRESS_PREFIX + job_id,
                                REDIS_PROGRESS_TTL,
                                payload
                            )
                        except Exception as e:
                            log.debug("Redis dataset datagis progress write failed: %s", e)

                if any(marker in line for marker in ['DATAGIS_PROGRESS', '', '', '', '', 'ERROR', 'WARNING']):
                    log.info("%s", line)

            return_code = process.wait()
            out = '\n'.join(output_lines)
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_DATAGIS_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass

            if return_code == 0:
                log.info("Import datagis terminé pour %s", package_id)
                return {'success': True, 'message': f'Import datagis terminé pour le dataset {package_id}', 'output': out}
            log.warning("Import datagis %s: code %s", package_id, return_code)
            return {'success': False, 'error': out or f'Code retour {return_code}', 'output': out}
        except subprocess.TimeoutExpired:
            process.kill()
            out = '\n'.join(output_lines)
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_DATAGIS_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {'success': False, 'error': 'Timeout - import datagis trop long', 'output': out}
    except Exception as e:
        log.error("Erreur import datagis pour %s: %s", package_id, e)
        return {'success': False, 'error': str(e)}


def sync_datagis_job():
    """
    Job asynchrone pour l'import des fichiers géospatiaux dans datagis
    Cette fonction est exécutée par le worker RQ
    """
    try:
        log.info("Démarrage de l'import datagis (job asynchrone)")
        
        # Lancer le script d'import datagis
        script_path = os.getenv('IMPORT_DATAGIS_SCRIPT', '/usr/local/bin/import-geospatial-to-datagis.py')
        
        if not os.path.exists(script_path):
            # Chercher dans d'autres emplacements possibles
            possible_paths = [
                '/srv/app/ckan/scripts/import-geospatial-to-datagis.py',
                '/srv/app/scripts/import-geospatial-to-datagis.py',
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    script_path = path
                    break
            else:
                raise FileNotFoundError("Script import-geospatial-to-datagis.py non trouvé")
        
        # Récupérer les variables d'environnement pour la configuration
        ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
        ckan_api_key = os.getenv('CKAN_API_KEY', '')
        postgis_host = os.getenv('POSTGRES_HOST', 'db')
        postgis_port = os.getenv('POSTGRES_PORT', '5432')
        datagis_db = os.getenv('DATAGIS_DB', 'datagis')
        postgis_user = os.getenv('POSTGRES_USER', 'ckan')
        postgis_password = os.getenv('POSTGRES_PASSWORD', 'ckan')
        ckan_storage_path = os.getenv('CKAN_STORAGE_PATH', '/ckan_storage')
        
        log.info(f"Script: {script_path}")
        log.info(f"CKAN URL: {ckan_url}")
        log.info(f"PostGIS: {postgis_user}@{postgis_host}:{postgis_port}/{datagis_db}")
        log.info(f"Storage Path: {ckan_storage_path}")
        
        # Construire la commande (même format qu'au démarrage)
        cmd = [
            'python3', script_path,
            '--ckan-url', ckan_url,
            '--ckan-api-key', ckan_api_key,
            '--datagis-db', datagis_db,  # Utiliser --datagis-db comme au démarrage
            '--postgis-host', postgis_host,
            '--postgis-port', postgis_port,
            '--postgis-user', postgis_user,
            '--postgis-password', postgis_password,
            '--ckan-storage-path', ckan_storage_path  # Important pour trouver les fichiers
        ]
        
        log.info(f"Commande complète: {' '.join(cmd[:5])} ... [paramètres masqués]")
        log.info(f"Démarrage du script...")
        
        # Connexion Redis pour exposer la progression à l'UI (via job_id)
        job_id = None
        redis_conn = None
        try:
            from rq import get_current_job
            job = get_current_job()
            if job:
                job_id = job.id
                redis_conn = getattr(job, 'connection', None)
        except Exception:
            pass
        
        # Utiliser Popen pour lire les logs en temps réel (comme au démarrage)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Lire la sortie en temps réel
        output_lines = []
        start_time = time.time()
        last_log_time = start_time
        
        try:
            while True:
                # Vérifier le timeout
                elapsed = time.time() - start_time
                if elapsed > 3600:
                    process.kill()
                    raise subprocess.TimeoutExpired(cmd, 3600)
                
                # Lire une ligne
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break  # Processus terminé
                    time.sleep(0.1)
                    continue
                
                line = line.rstrip()
                output_lines.append(line)
                
                # Parser DATAGIS_PROGRESS: N/Total et écrire dans Redis pour l'UI
                if redis_conn and job_id and 'DATAGIS_PROGRESS:' in line:
                    m = re.search(r'DATAGIS_PROGRESS:\s*(\d+)/(\d+)', line)
                    if m:
                        try:
                            payload = json.dumps({
                                'current': int(m.group(1)),
                                'total': int(m.group(2)),
                                'message': f"{m.group(1)}/{m.group(2)} fichiers traités"
                            })
                            redis_conn.setex(
                                REDIS_DATAGIS_PROGRESS_PREFIX + job_id,
                                REDIS_PROGRESS_TTL,
                                payload
                            )
                        except Exception as e:
                            log.debug("Redis progress write failed: %s", e)
                
                # Logger les lignes importantes en temps réel (comme au démarrage)
                if any(marker in line for marker in ['', '', '', '', '', '', '', '', '', '', '', 'ERROR', 'WARNING', 'importé', 'Importé', 'ressources importées', 'DÉBUT IMPORT', 'IMPORT.*RÉUSSI', 'Table:', 'ogr2ogr', 'Fichier trouvé', 'Fichier normalisé', 'DÉBUT IMPORT RESSOURCE', 'DATAGIS_PROGRESS']):
                    log.info(f"{line}")
                elif time.time() - last_log_time > 10:  # Afficher une ligne toutes les 10 secondes
                    if line.strip():
                        log.info(f"{line}")
                        last_log_time = time.time()
            
            # Attendre la fin du processus
            return_code = process.wait()
            output = '\n'.join(output_lines)
            
            log.info(f"Script terminé avec code retour: {return_code}")
            if output:
                log.info(f"Sortie complète ({len(output_lines)} lignes)")
                # Afficher les dernières lignes
                if len(output_lines) > 0:
                    log.info(f"Dernières lignes: {chr(10).join(output_lines[-10:])}")
        except subprocess.TimeoutExpired:
            process.kill()
            output = '\n'.join(output_lines)
            log.error("Timeout lors de l'import datagis (délai dépassé)")
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_DATAGIS_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {
                'success': False,
                'error': 'Timeout - l\'import prend trop de temps (délai: 1 heure)',
                'output': output
            }
        
        if return_code == 0:
            # Analyser la sortie pour compter les imports réussis
            success_count = len([line for line in output_lines if '' in line and ('importé' in line.lower() or 'imported' in line.lower())])
            error_count = len([line for line in output_lines if '' in line or 'ERROR' in line])
            
            message = f'Import datagis terminé avec succès'
            if success_count > 0:
                message += f' ({success_count} fichier(s) importé(s))'
            if error_count > 0:
                message += f' ({error_count} erreur(s) détectée(s))'
            
            log.info(f"{message}")
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_DATAGIS_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {
                'success': True,
                'message': message,
                'output': output,
                'success_count': success_count,
                'error_count': error_count
            }
        else:
            error_msg = output or 'Erreur inconnue'
            error_lines = error_msg.split('\n')[-10:] if error_msg else []
            log.error(f"Erreur lors de l'import datagis (code: {return_code})")
            if error_lines:
                log.error(f"Dernières lignes d'erreur: {chr(10).join(error_lines)}")
            if redis_conn and job_id:
                try:
                    redis_conn.delete(REDIS_DATAGIS_PROGRESS_PREFIX + job_id)
                except Exception:
                    pass
            return {
                'success': False,
                'error': '\n'.join(error_lines) if error_lines else f'Erreur inconnue (code retour: {return_code})',
                'output': output
            }
    except subprocess.TimeoutExpired:
        log.error("Timeout lors de l'import datagis (délai dépassé)")
        return {
            'success': False,
            'error': 'Timeout - l\'import prend trop de temps (délai: 1 heure)'
        }
    except Exception as e:
        error_msg = str(e)
        log.error(f"Erreur import datagis: {error_msg}")
        import traceback
        log.error(traceback.format_exc())
        return {
            'success': False,
            'error': error_msg
        }

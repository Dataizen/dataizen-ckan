"""
Plugin d'administration pour CKAN
- Suppression en masse de datasets, membres, organisations
- Lancement des synchronisations (mapserver, pygeoapi)
- État des moissonnages en cours
"""
import logging
import os
import subprocess
from typing import Dict, List, Any, Optional

from ckan.plugins import SingletonPlugin, implements
from ckan.plugins.interfaces import IBlueprint, IActions, IAuthFunctions, IConfigurer, IMiddleware
from ckan.plugins.toolkit import (
    get_action, check_access, NotAuthorized, ValidationError,
    ObjectNotFound, get_validator, add_template_directory, enqueue_job
)

try:
    from ckan import model
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False

log = logging.getLogger(__name__)


def _get_redis_connection():
    """Retourne une connexion Redis pour lire la progression des jobs (même Redis que RQ)."""
    try:
        from ckan.lib.redis import connect_to_redis
        return connect_to_redis()
    except Exception:
        pass
    try:
        from ckan.common import config
        url = config.get('ckan.redis.url') or os.getenv('CKAN_REDIS_URL')
        if url:
            import redis
            return redis.from_url(url)
    except Exception:
        pass
    return None


class AdminToolsPlugin(SingletonPlugin):
    """Plugin d'administration pour CKAN"""
    
    implements(IBlueprint)
    implements(IActions)
    implements(IAuthFunctions)
    implements(IConfigurer)
    implements(IMiddleware)
    
    def make_middleware(self, app, config):
        """Enregistre un hook sur chaque requête Flask pour tracer path, IP, User-Agent, etc."""
        if hasattr(app, 'before_request') and hasattr(app, 'after_request'):
            import time as _time
            from flask import g, request

            @app.before_request
            def _request_log_start():
                g._request_log_start = _time.perf_counter()

            @app.after_request
            def _request_log_after(response):
                try:
                    start = getattr(g, '_request_log_start', None)
                    duration = (_time.perf_counter() - start) if start is not None else 0.0
                    from ckanext.admin_tools.request_log import push_request
                    push_request(request, response.status_code if response else 0, duration)
                except Exception as e:
                    log.debug("request_log after_request: %s", e)
                return response

            log.info("Extension admin_tools: suivi des requêtes (path, IP, User-Agent) activé")
        return app

    def make_error_log_middleware(self, app, config):
        """No-op error-log middleware (requis par IMiddleware en CKAN >= 2.10)."""
        return app

    def update_config(self, config_):
        """Ajouter les templates au chemin de recherche"""
        add_template_directory(config_, 'templates')
        log.info("Extension admin_tools: templates directory ajoutée")
    
    def get_blueprint(self):
        """Retourne les blueprints pour les pages admin"""
        from ckanext.admin_tools.views import get_blueprint
        blueprint = get_blueprint()
        log.info(f"Extension admin_tools: blueprint enregistré avec préfixe {blueprint.url_prefix}")
        return blueprint
    
    def get_actions(self):
        """Retourne les actions API personnalisées"""
        # Wrapper les méthodes dans des fonctions pour éviter AttributeError
        def admin_bulk_delete_datasets(context, data_dict):
            return self._bulk_delete_datasets(context, data_dict)
        
        def admin_bulk_delete_organizations(context, data_dict):
            return self._bulk_delete_organizations(context, data_dict)
        
        def admin_bulk_delete_members(context, data_dict):
            return self._bulk_delete_members(context, data_dict)
        
        def admin_sync_pygeoapi(context, data_dict):
            return self._sync_pygeoapi(context, data_dict)
        
        def admin_sync_pygeoapi_status(context, data_dict):
            return self._sync_pygeoapi_status(context, data_dict)
        
        def admin_sync_mapserver(context, data_dict):
            return self._sync_mapserver(context, data_dict)
        
        def admin_sync_mapserver_status(context, data_dict):
            return self._sync_mapserver_status(context, data_dict)
        
        def admin_sync_datagis(context, data_dict):
            return self._sync_datagis(context, data_dict)
        
        def admin_sync_datagis_status(context, data_dict):
            return self._sync_datagis_status(context, data_dict)
        
        def admin_get_harvest_status(context, data_dict):
            return self._get_harvest_status(context, data_dict)
        
        def admin_get_all_organizations(context, data_dict):
            return self._get_all_organizations(context, data_dict)
        
        def admin_abort_harvest_job(context, data_dict):
            return self._abort_harvest_job(context, data_dict)
        
        def admin_get_request_log(context, data_dict):
            return self._get_request_log(context, data_dict)
        
        def dataset_generate_mapfile(context, data_dict):
            return self._dataset_generate_mapfile(context, data_dict)
        
        def dataset_sync_pygeoapi(context, data_dict):
            return self._dataset_sync_pygeoapi(context, data_dict)
        
        def dataset_import_datagis(context, data_dict):
            return self._dataset_import_datagis(context, data_dict)

        def dataset_import_datagis_status(context, data_dict):
            return self._dataset_import_datagis_status(context, data_dict)
        
        return {
            'admin_bulk_delete_datasets': admin_bulk_delete_datasets,
            'admin_bulk_delete_organizations': admin_bulk_delete_organizations,
            'admin_bulk_delete_members': admin_bulk_delete_members,
            'admin_sync_pygeoapi': admin_sync_pygeoapi,
            'admin_sync_pygeoapi_status': admin_sync_pygeoapi_status,
            'admin_sync_mapserver': admin_sync_mapserver,
            'admin_sync_mapserver_status': admin_sync_mapserver_status,
            'admin_sync_datagis': admin_sync_datagis,
            'admin_sync_datagis_status': admin_sync_datagis_status,
            'admin_get_harvest_status': admin_get_harvest_status,
            'admin_get_all_organizations': admin_get_all_organizations,
            'admin_abort_harvest_job': admin_abort_harvest_job,
            'admin_get_request_log': admin_get_request_log,
            'dataset_generate_mapfile': dataset_generate_mapfile,
            'dataset_sync_pygeoapi': dataset_sync_pygeoapi,
            'dataset_import_datagis': dataset_import_datagis,
            'dataset_import_datagis_status': dataset_import_datagis_status,
        }
    
    def get_auth_functions(self):
        """Retourne les fonctions d'autorisation"""
        return {
            'admin_bulk_delete_datasets': self._auth_admin,
            'admin_bulk_delete_organizations': self._auth_admin,
            'admin_bulk_delete_members': self._auth_admin,
            'admin_sync_pygeoapi': self._auth_admin,
            'admin_sync_pygeoapi_status': self._auth_admin,
            'admin_sync_mapserver': self._auth_admin,
            'admin_sync_mapserver_status': self._auth_admin,
            'admin_sync_datagis': self._auth_admin,
            'admin_sync_datagis_status': self._auth_admin,
            'admin_get_harvest_status': self._auth_admin,
            'admin_get_all_organizations': self._auth_admin,
            'admin_abort_harvest_job': self._auth_admin,
            'admin_get_request_log': self._auth_admin,
            'dataset_generate_mapfile': self._auth_dataset_sync,
            'dataset_sync_pygeoapi': self._auth_dataset_sync,
            'dataset_import_datagis': self._auth_dataset_sync,
            'dataset_import_datagis_status': self._auth_dataset_sync,
        }
    
    def _auth_dataset_sync(self, context, data_dict):
        """Autorise si l'utilisateur peut modifier le dataset (package_update)."""
        package_id = data_dict.get('dataset_id') or data_dict.get('package_id') or (data_dict.get('id') if isinstance(data_dict.get('id'), str) else None)
        if not package_id:
            return {'success': False, 'msg': 'dataset_id ou package_id requis'}
        try:
            check_access('package_update', context, {'id': package_id})
            return {'success': True}
        except NotAuthorized:
            return {'success': False, 'msg': 'Vous n\'avez pas le droit de modifier ce dataset'}
    
    def _auth_admin(self, context, data_dict):
        """Vérifie que l'utilisateur est admin"""
        try:
            check_access('sysadmin', context, {})
            return {'success': True}
        except NotAuthorized:
            return {'success': False, 'msg': 'Accès refusé - admin requis'}
    
    def _bulk_delete_datasets(self, context, data_dict):
        """Supprime plusieurs datasets en masse"""
        dataset_ids = data_dict.get('dataset_ids', [])
        if not dataset_ids:
            raise ValidationError({'dataset_ids': 'Liste de datasets requise'})
        
        results = {
            'deleted': [],
            'failed': [],
            'errors': {}
        }
        
        package_delete = get_action('package_delete')
        
        for dataset_id in dataset_ids:
            try:
                package_delete(context.copy(), {'id': dataset_id})
                results['deleted'].append(dataset_id)
                log.info(f"Dataset supprimé: {dataset_id}")
            except Exception as e:
                error_msg = str(e)
                results['failed'].append(dataset_id)
                results['errors'][dataset_id] = error_msg
                log.error(f"Erreur suppression dataset {dataset_id}: {error_msg}")
        
        return {
            'success': True,
            'deleted_count': len(results['deleted']),
            'failed_count': len(results['failed']),
            'results': results
        }
    
    def _bulk_delete_organizations(self, context, data_dict):
        """Supprime plusieurs organisations en masse, ainsi que tous leurs datasets"""
        org_ids = data_dict.get('organization_ids', [])
        if not org_ids:
            raise ValidationError({'organization_ids': 'Liste d\'organisations requise'})
        
        log.info(f"Début suppression en masse de {len(org_ids)} organisation(s)")
        
        results = {
            'deleted': [],
            'failed': [],
            'errors': {},
            'datasets_deleted': {}  # Nombre de datasets supprimés par organisation
        }
        
        # Initialiser les actions CKAN nécessaires
        # NOTE: organization_packages_list n'existe pas dans CKAN, on utilise package_search à la place
        try:
            organization_delete = get_action('organization_delete')
            package_delete = get_action('package_delete')
            organization_show = get_action('organization_show')
            package_search = get_action('package_search')
            log.debug("Actions CKAN initialisées: organization_delete, package_delete, organization_show, package_search")
        except Exception as e:
            log.error(f"Erreur lors de l'initialisation des actions CKAN: {e}")
            import traceback
            log.error(traceback.format_exc())
            raise ValidationError({'error': f'Erreur initialisation actions CKAN: {str(e)}'})
        
        # Créer un contexte avec ignore_auth pour récupérer TOUS les datasets, y compris les privés
        admin_context = context.copy()
        admin_context['ignore_auth'] = True
        admin_context['use_cache'] = False
        
        for org_id in org_ids:
            try:
                log.info(f"Traitement organisation: {org_id}")
                
                # Récupérer les informations de l'organisation
                try:
                    org_info = organization_show(admin_context.copy(), {'id': org_id, 'include_datasets': False})
                    org_name = org_info.get('name', org_id)
                    org_title = org_info.get('title', org_name)
                    log.debug(f"Organisation trouvée: {org_name} ({org_title})")
                except Exception as e:
                    log.warning(f"Impossible de récupérer les infos de l'organisation {org_id}: {e}")
                    org_name = org_id
                    org_title = org_id
                
                # Récupérer TOUS les datasets de l'organisation (PUBLICS ET PRIVÉS)
                # Utiliser une requête directe à la base de données pour être sûr d'avoir TOUS les datasets
                datasets = []
                dataset_count = 0
                try:
                    from ckan import model
                    from ckan.model import Package
                    
                    log.debug(f"Récupération des datasets de l'organisation {org_name} (publics et privés) via DB directe...")
                    
                    # Récupérer l'organisation depuis la DB
                    org_model = model.Session.query(model.Group).filter_by(
                        type='organization',
                        name=org_name,
                        state='active'
                    ).first()
                    
                    if org_model:
                        # Récupérer tous les packages de l'organisation (publics et privés)
                        packages_query = model.Session.query(Package).join(
                            model.Member,
                            Package.id == model.Member.table_id
                        ).filter(
                            model.Member.group_id == org_model.id,
                            model.Member.table_name == 'package',
                            Package.state == 'active'
                        )
                        
                        # Convertir les modèles en dictionnaires
                        for package in packages_query.all():
                            datasets.append({
                                'id': package.id,
                                'name': package.name,
                                'title': package.title or package.name,
                                'private': package.private
                            })
                        
                        dataset_count = len(datasets)
                        log.debug(f"Organisation {org_name} ({org_title}): {dataset_count} dataset(s) trouvé(s) via DB directe (publics et privés)")
                    else:
                        log.warning(f"Organisation {org_name} non trouvée dans la base de données")
                except Exception as e:
                    log.warning(f"Impossible de récupérer les datasets via DB directe pour {org_name}: {e}")
                    import traceback
                    log.debug(f"Traceback: {traceback.format_exc()}")
                    
                    # Fallback: combiner package_search et organization_show pour être sûr d'avoir tous les datasets
                    try:
                        log.debug(f"Tentative de récupération via package_search + organization_show (fallback)...")
                        
                        # Récupérer via package_search
                        datasets_package_search = []
                        try:
                            search_result = package_search(
                                admin_context.copy(), 
                                {'fq': f'organization:{org_name}', 'rows': 10000}
                            )
                            datasets_package_search = search_result.get('results', [])
                        except Exception:
                            pass
                        
                        # Récupérer via organization_show
                        datasets_org_show = []
                        try:
                            org_info_with_datasets = organization_show(
                                admin_context.copy(), 
                                {'id': org_id, 'include_datasets': True}
                            )
                            packages_list = org_info_with_datasets.get('packages', [])
                            # Convertir les strings en dicts si nécessaire
                            for pkg in packages_list:
                                if isinstance(pkg, dict):
                                    datasets_org_show.append(pkg)
                                elif isinstance(pkg, str):
                                    # Si c'est juste un nom, récupérer les détails
                                    try:
                                        package_show = get_action('package_show')
                                        pkg_details = package_show(admin_context.copy(), {'id': pkg})
                                        datasets_org_show.append(pkg_details)
                                    except Exception:
                                        datasets_org_show.append({'id': pkg, 'name': pkg})
                        except Exception:
                            pass
                        
                        # Combiner les deux listes et dédupliquer par ID
                        datasets_dict = {}
                        for ds in datasets_package_search + datasets_org_show:
                            ds_id = ds.get('id') or ds.get('name', '')
                            if ds_id:
                                datasets_dict[ds_id] = ds
                        
                        datasets = list(datasets_dict.values())
                        dataset_count = len(datasets)
                        log.debug(f"Organisation {org_name} ({org_title}): {dataset_count} dataset(s) trouvé(s) via fallback combiné")
                    except Exception as e2:
                        log.warning(f"Impossible de récupérer les datasets de {org_name} via fallback: {e2}")
                        import traceback
                        log.warning(f"Traceback: {traceback.format_exc()}")
                        datasets = []
                        dataset_count = 0
                
                # Supprimer tous les datasets de l'organisation
                deleted_datasets = 0
                failed_datasets = 0
                if datasets:
                    log.info(f"Suppression de {dataset_count} dataset(s) de l'organisation {org_name}...")
                    for idx, dataset in enumerate(datasets):
                        try:
                            # Gérer différents formats de retour (dict ou string)
                            # package_search retourne des dicts complets, organization_show.packages peut retourner des dicts ou des strings
                            if isinstance(dataset, dict):
                                dataset_id = dataset.get('id') or dataset.get('name', '')
                                dataset_name = dataset.get('name', dataset_id)
                            else:
                                # Si c'est une string (nom du package)
                                dataset_id = str(dataset)
                                dataset_name = dataset_id
                            
                            if not dataset_id:
                                log.warning(f"  Dataset {idx+1}/{dataset_count}: ID manquant, ignoré")
                                failed_datasets += 1
                                continue
                            
                            log.debug(f"  Suppression dataset {idx+1}/{dataset_count}: {dataset_name} ({dataset_id})")
                            # Utiliser admin_context avec ignore_auth pour supprimer même les datasets privés
                            package_delete(admin_context.copy(), {'id': dataset_id})
                            deleted_datasets += 1
                            log.info(f"  Dataset supprimé: {dataset_name}")
                        except Exception as e:
                            failed_datasets += 1
                            error_msg = str(e)
                            dataset_name = 'unknown'
                            if isinstance(dataset, dict):
                                dataset_name = dataset.get('name', dataset.get('id', 'unknown'))
                            elif isinstance(dataset, str):
                                dataset_name = dataset
                            log.error(f"  Erreur suppression dataset {dataset_name}: {error_msg}")
                            import traceback
                            log.debug(f"  Traceback: {traceback.format_exc()}")
                            # Continuer même si un dataset échoue
                    
                    log.info(f"Résultat suppression datasets: {deleted_datasets} supprimé(s), {failed_datasets} échec(s)")
                    
                    # Vérifier à nouveau que tous les datasets ont bien été supprimés (y compris les privés)
                    # Utiliser une requête DB directe pour vérifier
                    try:
                        from ckan import model
                        from ckan.model import Package
                        
                        org_model = model.Session.query(model.Group).filter_by(
                            type='organization',
                            name=org_name,
                            state='active'
                        ).first()
                        
                        remaining_datasets = []
                        if org_model:
                            remaining_packages = model.Session.query(Package).join(
                                model.Member,
                                Package.id == model.Member.table_id
                            ).filter(
                                model.Member.group_id == org_model.id,
                                model.Member.table_name == 'package',
                                Package.state == 'active'
                            ).all()
                            remaining_datasets = [{'id': p.id, 'name': p.name} for p in remaining_packages]
                        
                        if remaining_datasets:
                            log.warning(f"{len(remaining_datasets)} dataset(s) encore présents après suppression (publics et privés), nouvelle tentative...")
                            # Réessayer de supprimer les datasets restants
                            for remaining_dataset in remaining_datasets:
                                try:
                                    remaining_id = remaining_dataset.get('id') or remaining_dataset.get('name', '')
                                    if remaining_id:
                                        remaining_name = remaining_dataset.get('name', remaining_id)
                                        log.debug(f"  Suppression dataset restant: {remaining_name} ({remaining_id})")
                                        package_delete(admin_context.copy(), {'id': remaining_id})
                                        deleted_datasets += 1
                                        log.info(f"  Dataset restant supprimé: {remaining_name}")
                                except Exception as e:
                                    log.warning(f"  Impossible de supprimer le dataset restant {remaining_id}: {e}")
                    except Exception as e:
                        log.warning(f"Erreur lors de la vérification des datasets restants: {e}")
                    
                    # Avertir si certains datasets n'ont pas pu être supprimés
                    if failed_datasets > 0:
                        log.warning(f"{failed_datasets} dataset(s) n'ont pas pu être supprimés pour l'organisation {org_name}")
                        log.warning(f"L'organisation sera quand même supprimée, mais certains datasets peuvent rester orphelins")
                    
                    # Forcer un commit de la session de base de données pour s'assurer que les suppressions sont persistées
                    if MODEL_AVAILABLE:
                        try:
                            model.Session.commit()
                            log.debug("Session de base de données commitée après suppression des datasets")
                        except Exception as e:
                            log.warning(f"Erreur lors du commit de la session: {e}")
                
                # Supprimer l'organisation elle-même (APRÈS avoir supprimé les datasets)
                # C'est important de supprimer les datasets d'abord pour éviter les erreurs de contrainte de clé étrangère
                # Utiliser admin_context avec ignore_auth et use_cache=False
                log.info(f"Suppression de l'organisation {org_name} ({org_title})...")
                try:
                    organization_delete(admin_context.copy(), {'id': org_id})
                    results['deleted'].append(org_id)
                    results['datasets_deleted'][org_id] = {
                        'total': dataset_count,
                        'deleted': deleted_datasets,
                        'failed': failed_datasets
                    }
                    log.info(f"Organisation supprimée: {org_name} ({org_title}) - {deleted_datasets}/{dataset_count} dataset(s) supprimé(s)")
                except Exception as e:
                    error_msg = str(e)
                    log.error(f"Erreur lors de la suppression de l'organisation {org_name}: {error_msg}")
                    import traceback
                    log.error(f"Traceback: {traceback.format_exc()}")
                    raise  # Re-lancer l'exception pour qu'elle soit capturée par le bloc except externe
                
            except Exception as e:
                error_msg = str(e)
                import traceback
                log.error(f"Erreur suppression organisation {org_id}: {error_msg}")
                log.error(f"Traceback complet: {traceback.format_exc()}")
                results['failed'].append(org_id)
                results['errors'][org_id] = error_msg
                # Continuer avec les autres organisations même si une échoue
        
        # Calculer le total de datasets supprimés
        total_datasets_deleted = sum(
            info.get('deleted', 0) 
            for info in results['datasets_deleted'].values()
        )
        
        log.info(f"Résultat final suppression organisations: {len(results['deleted'])} supprimée(s), {len(results['failed'])} échec(s), {total_datasets_deleted} dataset(s) supprimé(s)")
        
        # Si toutes les organisations ont échoué, lever une exception
        if len(results['failed']) == len(org_ids) and len(results['deleted']) == 0:
            error_summary = '; '.join([f"{org_id}: {results['errors'].get(org_id, 'Erreur inconnue')}" for org_id in results['failed']])
            log.error(f"Toutes les organisations ont échoué: {error_summary}")
            raise ValidationError({'error': f'Toutes les organisations ont échoué: {error_summary}'})
        
        return {
            'success': True,
            'deleted_count': len(results['deleted']),
            'failed_count': len(results['failed']),
            'total_datasets_deleted': total_datasets_deleted,
            'results': results
        }
    
    def _bulk_delete_members(self, context, data_dict):
        """Supprime plusieurs membres d'une organisation en masse"""
        org_id = data_dict.get('organization_id')
        member_ids = data_dict.get('member_ids', [])
        
        if not org_id:
            raise ValidationError({'organization_id': 'ID d\'organisation requis'})
        if not member_ids:
            raise ValidationError({'member_ids': 'Liste de membres requise'})
        
        results = {
            'deleted': [],
            'failed': [],
            'errors': {}
        }
        
        organization_member_delete = get_action('organization_member_delete')
        
        for member_id in member_ids:
            try:
                organization_member_delete(context.copy(), {
                    'id': org_id,
                    'username': member_id  # Peut être username ou user_id
                })
                results['deleted'].append(member_id)
                log.info(f"Membre supprimé de {org_id}: {member_id}")
            except Exception as e:
                error_msg = str(e)
                results['failed'].append(member_id)
                results['errors'][member_id] = error_msg
                log.error(f"Erreur suppression membre {member_id} de {org_id}: {error_msg}")
        
        return {
            'success': True,
            'deleted_count': len(results['deleted']),
            'failed_count': len(results['failed']),
            'results': results
        }
    
    def _sync_pygeoapi(self, context, data_dict):
        """Lance la synchronisation pygeoapi de manière asynchrone via un job"""
        try:
            from ckanext.admin_tools.jobs import sync_pygeoapi_job
            
            # Soumettre le job à la queue
            job = enqueue_job(
                sync_pygeoapi_job,
                [],
                queue='default',
                title='Synchronisation pygeoapi'
            )
            
            log.info(f"Job de synchronisation pygeoapi soumis: {job.id}")
            
            return {
                'success': True,
                'message': 'Synchronisation pygeoapi lancée en arrière-plan',
                'job_id': job.id,
                'status': 'queued'
            }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la soumission du job pygeoapi: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _sync_pygeoapi_status(self, context, data_dict):
        """Récupère le statut du job de synchronisation pygeoapi"""
        try:
            job_id = data_dict.get('job_id')
            if not job_id:
                return {
                    'success': False,
                    'error': 'job_id requis'
                }
            
            # Utiliser l'action CKAN job_show pour récupérer le statut
            job_show = get_action('job_show')
            try:
                job_data = job_show(context, {'id': job_id})
            except ObjectNotFound:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            if not job_data:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            status = job_data.get('status', 'unknown')
            result = job_data.get('result')
            
            # Déterminer le statut
            if status == 'finished':
                # Récupérer le résultat
                if result and isinstance(result, dict):
                    return {
                        'success': result.get('success', True),
                        'status': 'finished',
                        'message': result.get('message', 'Synchronisation terminée'),
                        'error': result.get('error')
                    }
                return {
                    'success': True,
                    'status': 'finished',
                    'message': 'Synchronisation terminée'
                }
            elif status == 'failed':
                error_info = job_data.get('error', {})
                error_msg = error_info.get('message', str(error_info)) if isinstance(error_info, dict) else str(error_info)
                return {
                    'success': False,
                    'status': 'failed',
                    'error': error_msg or 'Erreur inconnue'
                }
            elif status == 'running' or status == 'started':
                progress_current = None
                progress_total = None
                progress_message = 'Synchronisation en cours...'
                try:
                    from ckanext.admin_tools.jobs import REDIS_PYGEOAPI_PROGRESS_PREFIX
                    conn = _get_redis_connection()
                    if conn:
                        raw = conn.get(REDIS_PYGEOAPI_PROGRESS_PREFIX + job_id)
                        if raw:
                            import json
                            prog = json.loads(raw)
                            progress_current = prog.get('current')
                            progress_total = prog.get('total')
                            progress_message = prog.get('message', progress_message)
                except Exception:
                    pass
                return {
                    'success': True,
                    'status': 'running',
                    'message': progress_message,
                    'progress_current': progress_current,
                    'progress_total': progress_total
                }
            else:
                return {
                    'success': True,
                    'status': 'queued',
                    'message': 'Synchronisation en attente...'
                }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la récupération du statut: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _sync_mapserver(self, context, data_dict):
        """Lance la synchronisation mapserver de manière asynchrone via un job"""
        try:
            from ckanext.admin_tools.jobs import sync_mapserver_job
            
            # Soumettre le job à la queue
            job = enqueue_job(
                sync_mapserver_job,
                [],
                queue='default',
                title='Synchronisation MapServer'
            )
            
            log.info(f"Job de synchronisation MapServer soumis: {job.id}")
            
            return {
                'success': True,
                'message': 'Synchronisation MapServer lancée en arrière-plan',
                'job_id': job.id,
                'status': 'queued'
            }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la soumission du job MapServer: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _sync_mapserver_status(self, context, data_dict):
        """Récupère le statut du job de synchronisation MapServer"""
        try:
            job_id = data_dict.get('job_id')
            if not job_id:
                return {
                    'success': False,
                    'error': 'job_id requis'
                }
            
            # Utiliser l'action CKAN job_show pour récupérer le statut
            job_show = get_action('job_show')
            try:
                job_data = job_show(context, {'id': job_id})
            except ObjectNotFound:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            if not job_data:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            status = job_data.get('status', 'unknown')
            result = job_data.get('result')
            
            # Déterminer le statut
            if status == 'finished':
                # Récupérer le résultat
                if result and isinstance(result, dict):
                    return {
                        'success': result.get('success', True),
                        'status': 'finished',
                        'message': result.get('message', 'Synchronisation terminée'),
                        'error': result.get('error'),
                        'output': result.get('output'),
                        'success_count': result.get('success_count'),
                        'error_count': result.get('error_count')
                    }
                return {
                    'success': True,
                    'status': 'finished',
                    'message': 'Synchronisation terminée'
                }
            elif status == 'failed':
                error_info = job_data.get('error', {})
                error_msg = error_info.get('message', str(error_info)) if isinstance(error_info, dict) else str(error_info)
                return {
                    'success': False,
                    'status': 'failed',
                    'error': error_msg or 'Erreur inconnue'
                }
            elif status == 'running' or status == 'started':
                progress_current = None
                progress_total = None
                progress_message = 'Génération des mapfiles en cours...'
                try:
                    from ckanext.admin_tools.jobs import REDIS_MAPFILE_PROGRESS_PREFIX
                    conn = _get_redis_connection()
                    if conn:
                        raw = conn.get(REDIS_MAPFILE_PROGRESS_PREFIX + job_id)
                        if raw:
                            import json
                            prog = json.loads(raw)
                            progress_current = prog.get('current')
                            progress_total = prog.get('total')
                            progress_message = prog.get('message', progress_message)
                except Exception:
                    pass
                return {
                    'success': True,
                    'status': 'running',
                    'message': progress_message,
                    'progress_current': progress_current,
                    'progress_total': progress_total
                }
            else:
                return {
                    'success': True,
                    'status': 'queued',
                    'message': 'Synchronisation en attente...'
                }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la récupération du statut: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _dataset_generate_mapfile(self, context, data_dict):
        """Lance la génération du mapfile pour un dataset (job asynchrone)."""
        package_id = data_dict.get('dataset_id') or data_dict.get('package_id') or data_dict.get('id')
        if not package_id:
            return {'success': False, 'error': 'dataset_id ou package_id requis'}
        try:
            pkg = get_action('package_show')(context, {'id': package_id})
            dataset_name = pkg.get('name')
            if not dataset_name:
                return {'success': False, 'error': 'Nom du dataset introuvable'}
            from ckanext.admin_tools.jobs import sync_mapserver_dataset_job
            job = enqueue_job(sync_mapserver_dataset_job, [dataset_name], queue='default', title=f'Mapfile: {dataset_name}')
            log.info("Job mapfile dataset soumis: %s pour %s", job.id, dataset_name)
            return {'success': True, 'message': f'Génération du mapfile lancée pour {dataset_name}', 'job_id': job.id, 'status': 'queued'}
        except Exception as e:
            log.exception("Erreur dataset_generate_mapfile")
            return {'success': False, 'error': str(e)}
    
    def _dataset_sync_pygeoapi(self, context, data_dict):
        """Lance la synchronisation pygeoapi pour un dataset (job asynchrone)."""
        package_id = data_dict.get('dataset_id') or data_dict.get('package_id') or data_dict.get('id')
        if not package_id:
            return {'success': False, 'error': 'dataset_id ou package_id requis'}
        try:
            pkg = get_action('package_show')(context, {'id': package_id})
            dataset_name = pkg.get('name')
            if not dataset_name:
                return {'success': False, 'error': 'Nom du dataset introuvable'}
            from ckanext.admin_tools.jobs import sync_pygeoapi_dataset_job
            job = enqueue_job(sync_pygeoapi_dataset_job, [dataset_name], queue='default', title=f'Pygeoapi: {dataset_name}')
            log.info("Job pygeoapi dataset soumis: %s pour %s", job.id, dataset_name)
            return {'success': True, 'message': f'Synchronisation pygeoapi lancée pour {dataset_name}', 'job_id': job.id, 'status': 'queued'}
        except Exception as e:
            log.exception("Erreur dataset_sync_pygeoapi")
            return {'success': False, 'error': str(e)}
    
    def _dataset_import_datagis(self, context, data_dict):
        """Lance l'import datagis pour les ressources d'un dataset (job asynchrone)."""
        package_id = data_dict.get('dataset_id') or data_dict.get('package_id') or data_dict.get('id')
        if not package_id:
            return {'success': False, 'error': 'dataset_id ou package_id requis'}
        try:
            from ckanext.admin_tools.jobs import sync_datagis_dataset_job
            job = enqueue_job(
                sync_datagis_dataset_job,
                [package_id],
                queue='default',
                title=f'Import Datagis: {package_id}',
                rq_kwargs={'timeout': 3600}
            )
            log.info("Job import datagis dataset soumis: %s pour %s", job.id, package_id)
            return {'success': True, 'message': f'Import datagis lancé pour ce dataset', 'job_id': job.id, 'status': 'queued'}
        except Exception as e:
            log.exception("Erreur dataset_import_datagis")
            return {'success': False, 'error': str(e)}

    def _dataset_import_datagis_status(self, context, data_dict):
        """Récupère le statut du job d'import datagis pour un dataset."""
        return self._sync_datagis_status(context, data_dict)
    
    def _sync_datagis(self, context, data_dict):
        """Lance l'import datagis de manière asynchrone via un job"""
        try:
            from ckanext.admin_tools.jobs import sync_datagis_job
            
            # Timeout 2 h (7200 s) : gros fichiers, 600+ ressources. Si le job dépasse 180 s par défaut,
            # RQ tue le processus (import bloqué à N/Total). rq_kwargs doit contenir 'timeout'.
            # Sinon, définir ckan.jobs.timeout = 7200 dans [app:main] de ckan.ini.
            job = enqueue_job(
                sync_datagis_job,
                [],
                queue='default',
                title='Import Datagis',
                rq_kwargs={'timeout': 7200}
            )
            
            log.info(f"Job d'import datagis soumis: {job.id}")
            
            return {
                'success': True,
                'message': 'Import datagis lancé en arrière-plan',
                'job_id': job.id,
                'status': 'queued'
            }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la soumission du job datagis: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _sync_datagis_status(self, context, data_dict):
        """Récupère le statut du job d'import datagis"""
        try:
            # Accepter data_dict brut, wrappé (ex. {"data": {"job_id": "..."}}) ou "data" = chaîne JSON
            d = data_dict if isinstance(data_dict, dict) else {}
            inner = d.get('data')
            if isinstance(inner, str):
                try:
                    import json
                    inner = json.loads(inner)
                except Exception:
                    inner = {}
            if not isinstance(inner, dict):
                inner = d if isinstance(d.get('data'), dict) else d
            job_id = (inner.get('job_id') if isinstance(inner, dict) else None) or d.get('job_id')
            if not job_id:
                return {
                    'success': False,
                    'error': 'job_id requis',
                    'status': 'not_found'
                }
            job_id = str(job_id).strip()
            
            # Utiliser l'action CKAN job_show pour récupérer le statut
            job_show = get_action('job_show')
            try:
                job_data = job_show(context, {'id': job_id})
            except ObjectNotFound:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            except Exception as e:
                log.warning("job_show failed for job_id=%s: %s", job_id, e)
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            if not job_data:
                return {
                    'success': False,
                    'error': 'Job non trouvé',
                    'status': 'not_found'
                }
            
            status = (job_data.get('status') or 'unknown')
            if isinstance(status, str):
                status = status.lower()
            if status == 'completed':
                status = 'finished'
            result = job_data.get('result')
            
            # Déterminer le statut
            if status == 'finished':
                # Récupérer le résultat
                if result and isinstance(result, dict):
                    return {
                        'success': result.get('success', True),
                        'status': 'finished',
                        'message': result.get('message', 'Import terminé'),
                        'error': result.get('error'),
                        'output': result.get('output'),
                        'success_count': result.get('success_count'),
                        'error_count': result.get('error_count')
                    }
                return {
                    'success': True,
                    'status': 'finished',
                    'message': 'Import terminé'
                }
            elif status == 'failed':
                error_info = job_data.get('error', {})
                error_msg = error_info.get('message', str(error_info)) if isinstance(error_info, dict) else str(error_info)
                return {
                    'success': False,
                    'status': 'failed',
                    'error': error_msg or 'Erreur inconnue'
                }
            elif status in ('running', 'started'):
                # Lire la progression depuis Redis (écrite par le job)
                progress_current = None
                progress_total = None
                progress_message = 'Import en cours...'
                try:
                    from ckanext.admin_tools.jobs import REDIS_DATAGIS_PROGRESS_PREFIX
                    conn = _get_redis_connection()
                    if conn:
                        raw = conn.get(REDIS_DATAGIS_PROGRESS_PREFIX + str(job_id))
                        if raw:
                            import json
                            prog = json.loads(raw)
                            progress_current = prog.get('current')
                            progress_total = prog.get('total')
                            progress_message = prog.get('message', progress_message)
                except Exception:
                    pass
                return {
                    'success': True,
                    'status': 'running',
                    'message': progress_message,
                    'progress_current': progress_current,
                    'progress_total': progress_total
                }
            else:
                return {
                    'success': True,
                    'status': 'queued',
                    'message': 'Import en attente...'
                }
        except Exception as e:
            error_msg = str(e)
            log.error(f"Erreur lors de la récupération du statut: {error_msg}")
            import traceback
            log.error(traceback.format_exc())
            return {
                'success': False,
                'error': error_msg
            }
    
    def _get_all_organizations(self, context, data_dict):
        """Récupère toutes les organisations depuis la base de données, en ignorant les permissions"""
        # Vérifier l'autorisation d'abord
        try:
            check_access('sysadmin', context, {})
        except NotAuthorized:
            log.warning("Tentative d'accès non autorisée à admin_get_all_organizations")
            raise NotAuthorized("Accès refusé - admin requis")
        
        if not MODEL_AVAILABLE:
            raise ValidationError({'error': 'Modèle CKAN non disponible'})
        
        # S'assurer que model est importé
        try:
            from ckan import model
            from ckan.model import Package
        except ImportError:
            raise ValidationError({'error': 'Modèle CKAN non disponible'})
        
        try:
            # Créer un contexte avec ignore_auth pour récupérer TOUTES les organisations
            admin_context = context.copy()
            admin_context['ignore_auth'] = True
            admin_context['use_cache'] = False
            
            package_search = get_action('package_search')
            
            # Interroger directement la base de données pour récupérer TOUTES les organisations
            # organization_list peut filtrer certaines organisations même avec ignore_auth
            # (par exemple, les organisations privées ou avec des restrictions)
            
            # Récupérer toutes les organisations directement depuis la base de données
            org_query = model.Session.query(model.Group).filter(
                model.Group.type == 'organization',
                model.Group.state == 'active'
            ).order_by(model.Group.name)
            
            org_list = []
            for org_model in org_query.all():
                # Convertir le modèle en dictionnaire
                org_dict = {
                    'id': org_model.id,
                    'name': org_model.name,
                    'title': org_model.title or org_model.name,
                    'description': org_model.description or '',
                    'state': org_model.state,
                    'type': org_model.type,
                    'image_url': org_model.image_url or '',
                    'approval_status': getattr(org_model, 'approval_status', None),
                }
                org_list.append(org_dict)
            
            log.debug(f"{len(org_list)} organisation(s) trouvée(s) directement depuis la base de données")
            
            # Pour chaque organisation, récupérer le nombre de datasets
            organizations_with_counts = []
            for org in org_list:
                org_name = org.get('name', org.get('id', ''))
                if not org_name:
                    continue
                
                # Récupérer le nombre de datasets (PUBLICS ET PRIVÉS)
                # Utiliser une requête directe à la base de données pour être sûr d'avoir TOUS les datasets
                dataset_count = 0
                try:
                    # model et Package sont déjà importés au début de la fonction
                    # Récupérer tous les packages de l'organisation directement depuis la base de données
                    # Cela inclut TOUS les datasets (publics et privés) car on ignore les permissions
                    org_model = model.Session.query(model.Group).filter_by(
                        type='organization',
                        name=org_name,
                        state='active'
                    ).first()
                    
                    if org_model:
                        # Compter tous les packages de l'organisation (publics et privés)
                        packages_query = model.Session.query(Package).join(
                            model.Member,
                            Package.id == model.Member.table_id
                        ).filter(
                            model.Member.group_id == org_model.id,
                            model.Member.table_name == 'package',
                            Package.state == 'active'
                        )
                        dataset_count = packages_query.count()
                        log.debug(f"Organisation {org_name}: {dataset_count} dataset(s) via requête DB directe")
                    else:
                        log.warning(f"Organisation {org_name} non trouvée dans la base de données")
                        dataset_count = 0
                except Exception as e:
                    log.warning(f"Impossible de récupérer le count via DB directe pour {org_name}: {e}")
                    # Fallback: combiner organization_show et package_search
                    try:
                        # Essayer avec organization_show
                        organization_show = get_action('organization_show')
                        org_with_datasets = organization_show(
                            admin_context.copy(),
                            {'id': org_name, 'include_datasets': True}
                        )
                        datasets_org_show = org_with_datasets.get('packages', [])
                        count_org_show = len(datasets_org_show) if datasets_org_show else 0
                        
                        # Essayer avec package_search
                        search_result = package_search(
                            admin_context.copy(),
                            {'fq': f'organization:{org_name}', 'rows': 0}
                        )
                        count_package_search = search_result.get('count', 0)
                        
                        # Prendre le maximum des deux pour être sûr d'avoir tous les datasets
                        dataset_count = max(count_org_show, count_package_search)
                        log.debug(f"Organisation {org_name}: {dataset_count} dataset(s) via fallback (org_show: {count_org_show}, package_search: {count_package_search})")
                    except Exception as e2:
                        log.warning(f"Impossible de récupérer le count via fallback pour {org_name}: {e2}")
                        dataset_count = 0
                
                org['dataset_count'] = dataset_count
                organizations_with_counts.append(org)
            
            log.info(f"{len(organizations_with_counts)} organisation(s) avec counts retournée(s)")
            
            return {
                'success': True,
                'result': organizations_with_counts,
                'count': len(organizations_with_counts)
            }
        except Exception as e:
            error_msg = str(e)
            import traceback
            log.error(f"Erreur lors de la récupération des organisations: {error_msg}")
            log.error(f"Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': error_msg,
                'result': []
            }
    
    def _get_harvest_status(self, context, data_dict):
        """Récupère l'état des moissonnages en cours"""
        try:
            # Utiliser les actions CKAN pour récupérer les informations de moissonnage
            try:
                harvest_job_list = get_action('harvest_job_list')
                harvest_source_list = get_action('harvest_source_list')
            except (KeyError, AttributeError):
                # Si les actions ne sont pas disponibles, essayer d'accéder directement au modèle
                try:
                    from ckanext.harvest.model import HarvestJob, HarvestObject, HarvestSource
                    from ckan import model
                    
                    # Récupérer uniquement les jobs en cours (Running ou New)
                    jobs = model.Session.query(HarvestJob).filter(
                        HarvestJob.status.in_(['Running', 'New'])
                    ).order_by(
                        HarvestJob.created.desc()
                    ).limit(50).all()
                    
                    # Récupérer les sources actives
                    sources = model.Session.query(HarvestSource).filter(
                        HarvestSource.active == True
                    ).all()
                    
                    # Compter les objets par état pour chaque job
                    jobs_status = []
                    for job in jobs:
                        objects = model.Session.query(HarvestObject).filter_by(
                            harvest_job_id=job.id
                        ).all()
                        
                        status_counts = {
                            'GATHER': 0,
                            'FETCH': 0,
                            'IMPORT': 0,
                            'COMPLETE': 0,
                            'ERROR': 0,
                            'WAITING': 0
                        }
                        
                        for obj in objects:
                            state = obj.state or 'WAITING'
                            status_counts[state] = status_counts.get(state, 0) + 1
                        
                        total_objects = len(objects)
                        completed = status_counts['COMPLETE'] + status_counts['ERROR']
                        progress = (completed / total_objects * 100) if total_objects > 0 else 0
                        
                        # Récupérer le titre de la source
                        source_title = 'Unknown'
                        if job.harvest_source_id:
                            source = model.Session.query(HarvestSource).filter_by(
                                id=job.harvest_source_id
                            ).first()
                            if source:
                                source_title = source.title or source.name or source.url
                        
                        jobs_status.append({
                            'id': job.id,
                            'source_id': job.harvest_source_id,
                            'source_title': source_title,
                            'status': job.status,
                            'created': job.created.isoformat() if job.created else None,
                            'gather_started': job.gather_started.isoformat() if job.gather_started else None,
                            'gather_finished': job.gather_finished.isoformat() if job.gather_finished else None,
                            'finished': job.finished.isoformat() if job.finished else None,
                            'total_objects': total_objects,
                            'progress': round(progress, 1),
                            'status_counts': status_counts
                        })
                    
                    return {
                        'success': True,
                        'active_sources_count': len(sources),
                        'running_jobs_count': len([j for j in jobs if j.status in ['Running', 'New']]),
                        'jobs': jobs_status,
                        'sources': [{
                            'id': s.id,
                            'title': s.title or s.name or s.url,
                            'url': s.url,
                            'type': s.type,
                            'active': s.active
                        } for s in sources]
                    }
                except ImportError:
                    return {
                        'success': False,
                        'error': 'Extension harvest non disponible'
                    }
            
            # Utiliser les actions CKAN si disponibles
            jobs_data = harvest_job_list(context.copy(), {})
            sources_data = harvest_source_list(context.copy(), {})
            
            # Créer un dictionnaire des sources par ID pour faciliter la recherche
            sources_by_id = {s.get('id'): s for s in sources_data}
            
            # Filtrer les sources actives
            active_sources = [s for s in sources_data if s.get('active', False)]
            
            # Filtrer uniquement les jobs en cours (Running ou New)
            running_jobs = [j for j in jobs_data if j.get('status') in ['Running', 'New']]
            
            # Enrichir les jobs avec le titre de la source et calculer la progression
            enriched_jobs = []
            for job in running_jobs:
                source_id = job.get('source_id')
                source_info = sources_by_id.get(source_id, {})
                source_title = source_info.get('title') or source_info.get('name') or source_id or 'Unknown'
                
                # Calculer la progression si possible
                # Les jobs CKAN peuvent avoir des informations sur les objets
                total_objects = job.get('total_objects', 0)
                progress = job.get('progress', 0)
                
                # Si pas de progression calculée, essayer de l'estimer depuis les objets du job
                if progress == 0 or total_objects == 0:
                    # Essayer de récupérer les objets du job pour calculer la progression
                    try:
                        from ckanext.harvest.model import HarvestObject
                        from ckan import model
                        objects = model.Session.query(HarvestObject).filter_by(
                            harvest_job_id=job.get('id')
                        ).all()
                        total_objects = len(objects)
                        if total_objects > 0:
                            completed = sum(1 for obj in objects if obj.state in ['COMPLETE', 'ERROR'])
                            progress = round((completed / total_objects) * 100, 1)
                            # Stocker aussi les status_counts pour l'affichage
                            status_counts = {}
                            for obj in objects:
                                state = obj.state or 'WAITING'
                                status_counts[state] = status_counts.get(state, 0) + 1
                            enriched_job['status_counts'] = status_counts
                    except Exception as e:
                        log.debug(f"Impossible de calculer la progression pour job {job.get('id')}: {e}")
                        pass
                
                enriched_job = job.copy()
                enriched_job['source_title'] = source_title
                enriched_job['total_objects'] = total_objects
                enriched_job['progress'] = progress
                enriched_jobs.append(enriched_job)
            
            return {
                'success': True,
                'active_sources_count': len(active_sources),
                'running_jobs_count': len(running_jobs),
                'jobs': enriched_jobs,  # Retourner les jobs enrichis
                'sources': active_sources
            }
        except Exception as e:
            error_msg = str(e)
            import traceback
            log.error(f"Erreur récupération état moissonnages: {error_msg}")
            log.error(f"Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': error_msg
            }
    
    def _abort_harvest_job(self, context, data_dict):
        """Arrête un job de moissonnage"""
        try:
            # Vérifier l'autorisation
            check_access('sysadmin', context, {})
        except NotAuthorized:
            log.warning("Tentative d'accès non autorisée à admin_abort_harvest_job")
            raise NotAuthorized("Accès refusé - admin requis")
        
        job_id = data_dict.get('job_id')
        if not job_id:
            raise ValidationError({'error': 'job_id est requis'})
        
        try:
            # Essayer d'utiliser l'action CKAN si disponible
            try:
                harvest_job_abort = get_action('harvest_job_abort')
                result = harvest_job_abort(context.copy(), {'id': job_id})
                log.info(f"Job {job_id} arrêté via action CKAN")
                return {
                    'success': True,
                    'message': f'Job {job_id} arrêté avec succès'
                }
            except (KeyError, AttributeError):
                # Si l'action n'existe pas, utiliser directement le modèle
                try:
                    from ckanext.harvest.model import HarvestJob
                    from ckan import model
                    import datetime
                    
                    job = model.Session.query(HarvestJob).filter_by(id=job_id).first()
                    if not job:
                        raise ObjectNotFound(f'Job {job_id} non trouvé')
                    
                    # Vérifier que le job est en cours
                    if job.status not in ['Running', 'New']:
                        log.warning(f"Job {job_id} n'est pas en cours (statut: {job.status})")
                        return {
                            'success': False,
                            'error': f'Le job n\'est pas en cours (statut: {job.status})'
                        }
                    
                    # Arrêter le job
                    job.status = 'Aborted'
                    job.finished = datetime.datetime.utcnow()
                    model.Session.add(job)
                    model.Session.commit()
                    
                    log.info(f"Job {job_id} arrêté avec succès")
                    return {
                        'success': True,
                        'message': f'Job {job_id} arrêté avec succès'
                    }
                except ImportError:
                    return {
                        'success': False,
                        'error': 'Extension harvest non disponible'
                    }
        except Exception as e:
            error_msg = str(e)
            import traceback
            log.error(f"Erreur lors de l'arrêt du job {job_id}: {error_msg}")
            log.error(f"Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': error_msg
            }
    
    def _get_request_log(self, context, data_dict):
        """Retourne les N dernières requêtes CKAN (path, IP, User-Agent, etc.) pour l'admin."""
        limit = min(int(data_dict.get('limit', 100)), 500)
        from ckanext.admin_tools.request_log import get_request_log
        entries = get_request_log(limit=limit)
        return {
            'success': True,
            'entries': entries,
            'count': len(entries),
        }


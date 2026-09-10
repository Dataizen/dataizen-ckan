"""
ckanext-harvest-xloader plugin.

Bridges ckanext-harvest and ckanext-xloader: when a resource is created
or updated by the harvester, the plugin schedules an xloader job so the
data is pushed to the datastore automatically.
"""
import logging
import re
from datetime import datetime, timedelta

from ckan.plugins import implements, SingletonPlugin
from ckan.plugins.interfaces import IPackageController
from ckan.plugins import toolkit

log = logging.getLogger(__name__)

class HarvestXloaderPlugin(SingletonPlugin):
    name = 'harvest_xloader'
    implements(IPackageController, inherit=True)

    _initialized = False

    def __init__(self, name=None):
        if not self._initialized:
            self.log = logging.getLogger(__name__)
            self._initialized = True
            self._submission_cache = {}

    def _is_harvested(self, context, pkg_dict=None):
        """
        Check if this is a harvest operation.
        Can check from context (package object) or pkg_dict (during before_create/before_update).
        """
        self.log.debug('Checking if dataset is being harvested')
        
        # Vérifier dans pkg_dict (extras) si fourni
        if pkg_dict:
            extras = pkg_dict.get('extras', [])
            if isinstance(extras, list):
                # Format liste de dicts
                for extra in extras:
                    if isinstance(extra, dict) and extra.get('key') in ['harvest_source_id', 'harvest_source_title']:
                        self.log.debug('Found harvest source in pkg_dict extras: %s', extra.get('value'))
                        return True
            elif isinstance(extras, dict):
                # Format dict
                if extras.get('harvest_source_id') or extras.get('harvest_source_title'):
                    self.log.debug('Found harvest source in pkg_dict extras dict')
                    return True
        
        # Vérifier dans le package du contexte
        package = context.get('package')
        if package:
            try:
                harvest_source = package.extras.get('harvest_source_id') or \
                                 package.extras.get('harvest_source_title')
                if harvest_source:
                    self.log.debug('Found harvest source: %s', harvest_source)
                    return True
            except Exception as e:
                self.log.debug('Error checking package extras: %s', str(e))

        # Vérifier dans la base de données (HarvestObject)
        model = context.get('model')
        session = context.get('session')
        if model and session and package:
            try:
                from ckanext.harvest.model import HarvestObject
                harvest_object = session.query(HarvestObject) \
                    .filter_by(package_id=package.id) \
                    .order_by(HarvestObject.import_finished.desc()) \
                    .first()
                if harvest_object:
                    self.log.debug('Found harvest object: %s', harvest_object.id)
                    return True
            except Exception as e:
                self.log.debug('Error checking harvest object: %s', str(e))

        self.log.debug('No harvest information found')
        return False

    def _clean_tag_name(self, tag_name):
        """
        Nettoie le nom d'un tag pour qu'il respecte les règles de validation CKAN.
        Les tags ne peuvent contenir que des caractères alphanumériques, espaces, tirets, underscores et points.
        """
        if not tag_name:
            return tag_name
        
        # Remplacer les apostrophes et autres caractères spéciaux par des espaces ou les supprimer
        # Remplacer les apostrophes par rien (ex: "cours d'eau" -> "cours deau") ou par un espace
        cleaned = tag_name.replace("'", "").replace("'", "").replace("`", "")
        
        # Supprimer tous les caractères non autorisés (garder seulement alphanumériques, espaces, tirets, underscores, points)
        # Pattern: [^a-zA-Z0-9\s\-_.] signifie "tout sauf alphanumériques, espaces, tirets, underscores, points"
        cleaned = re.sub(r'[^a-zA-Z0-9\s\-_.]', '', cleaned)
        
        # Nettoyer les espaces multiples
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        if cleaned != tag_name:
            self.log.debug('Tag nettoyé: "%s" -> "%s"', tag_name, cleaned)
        
        return cleaned

    def _clean_tags(self, pkg_dict):
        """
        Nettoie tous les tags d'un package pour qu'ils respectent les règles de validation CKAN.
        """
        if 'tags' not in pkg_dict or not pkg_dict['tags']:
            return
        
        cleaned_tags = []
        for tag in pkg_dict['tags']:
            if isinstance(tag, dict):
                tag_name = tag.get('name', '')
            elif isinstance(tag, str):
                tag_name = tag
            else:
                continue
            
            cleaned_name = self._clean_tag_name(tag_name)
            
            # Ne garder que les tags non vides après nettoyage
            if cleaned_name:
                if isinstance(tag, dict):
                    cleaned_tags.append({'name': cleaned_name})
                else:
                    cleaned_tags.append(cleaned_name)
            else:
                self.log.warning('Tag supprimé car vide après nettoyage: "%s"', tag_name)
        
        if len(cleaned_tags) != len(pkg_dict['tags']):
            self.log.info('Tags nettoyés: %d -> %d tags valides', len(pkg_dict['tags']), len(cleaned_tags))
        
        pkg_dict['tags'] = cleaned_tags

    def before_create(self, context, pkg_dict):
        """
        Nettoie les tags avant la création du package lors du moissonnage.
        """
        if self._is_harvested(context, pkg_dict):
            self.log.debug('Harvest XLoader: before_create - nettoyage des tags pour dataset: %s', pkg_dict.get('name'))
            self._clean_tags(pkg_dict)
        return pkg_dict

    def before_update(self, context, pkg_dict):
        """
        Nettoie les tags avant la mise à jour du package lors du moissonnage.
        """
        if self._is_harvested(context, pkg_dict):
            self.log.debug('Harvest XLoader: before_update - nettoyage des tags pour dataset: %s', pkg_dict.get('name'))
            self._clean_tags(pkg_dict)
        return pkg_dict

    def after_create(self, context, pkg_dict):
        self.log.debug('Harvest XLoader: after_create for dataset: %s', pkg_dict.get('name'))
        if self._is_harvested(context):
            self.log.info('New harvested dataset: %s', pkg_dict.get('name'))
            self._submit_to_xloader(context, pkg_dict['id'])
        return pkg_dict

    def after_update(self, context, pkg_dict):
        self.log.debug('Harvest XLoader: after_update for dataset: %s', pkg_dict.get('name'))
        if self._is_harvested(context):
            self.log.info('Updated harvested dataset: %s', pkg_dict.get('name'))
            self._submit_to_xloader(context, pkg_dict['id'])
        return pkg_dict

    def _get_format(self, resource_format):
        """Normalize resource format."""
        if not resource_format:
            return ''
        format = resource_format.lower()
        if format.startswith('http'):
            format = format.split('/')[-1].lower()
        format_mapping = {
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
            'vnd.ms-excel': 'xls',
            'csv': 'csv',
            'text/csv': 'csv',
            'application/csv': 'csv',
            'application/vnd.ms-excel': 'xls',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx'
        }
        return format_mapping.get(format, format)

    def _should_submit(self, resource_id):
        """Avoid re-submitting too quickly the same resource."""
        now = datetime.utcnow()
        if resource_id in self._submission_cache:
            last_submission = self._submission_cache[resource_id]
            if now - last_submission < timedelta(minutes=5):
                return False
        self._submission_cache[resource_id] = now
        return True

    def _submit_to_xloader(self, context, package_id):
        """Submit all supported resources to xloader."""
        try:
            pkg_dict = toolkit.get_action('package_show')(context, {'id': package_id})
            log.info('Harvest XLoader: Found %d resources in dataset %s',
                     len(pkg_dict.get('resources', [])), pkg_dict.get('name'))

            for resource in pkg_dict.get('resources', []):
                resource_id = resource.get('id')

                if not self._should_submit(resource_id):
                    log.debug('Resource %s recently submitted, skipping', resource_id)
                    continue

                # Skip resources marked with xloader_skip=True (e.g., API resources)
                if resource.get('xloader_skip', False):
                    log.debug('Resource %s has xloader_skip=True, skipping', resource_id)
                    continue

                # Skip API resources (resource_type='api')
                if resource.get('resource_type') == 'api':
                    log.debug('Resource %s is an API resource, skipping xloader', resource_id)
                    continue

                clean_format = self._get_format(resource.get('format', ''))

                if clean_format.lower() in ['csv', 'xls', 'xlsx', 'ods', 'xlsm', 'xlsb']:
                    try:
                        submit_context = {
                            'ignore_auth': True,
                            'user': context.get('user'),
                            'api_version': 3
                        }
                        log.info('Submitting resource %s to xloader', resource_id)
                        toolkit.get_action('xloader_submit')(submit_context, {
                            'resource_id': resource_id,
                            'ignore_hash': True
                        })
                    except Exception as e:
                        log.error('Error submitting resource %s to xloader: %s - %s',
                                  resource_id, str(e), getattr(e, 'error_dict', {}))
                else:
                    log.info('Resource %s has unsupported format %s, skipping',
                             resource_id, clean_format)

        except Exception as e:
            log.error('Error processing dataset %s: %s', package_id, str(e))

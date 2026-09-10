"""
Plugin pour patcher ckanext-dcat afin de gérer l'absence de scheming_datasets
Ce plugin évite l'erreur "Unknown dataset schema: dataset" quand scheming n'est pas configuré
Et gère le format "js" non supporté par RDFLib
"""

import logging
from flask import Blueprint, redirect, abort
import ckan.plugins as plugins

log = logging.getLogger(__name__)


def _patch_dcat_utils():
    """Patch la fonction read_catalog_page pour gérer le format 'js' non supporté"""
    try:
        import ckanext.dcat.utils as dcat_utils
        if hasattr(dcat_utils, 'read_catalog_page'):
            original_read_catalog_page = dcat_utils.read_catalog_page
            
            def patched_read_catalog_page(_format):
                """Version patchée qui gère le format 'js' non supporté"""
                # Si le format est 'js', rediriger vers 'jsonld' (JSON-LD)
                if _format == 'js':
                    log.warning("Format 'js' non supporté par RDFLib, utilisation de 'jsonld' à la place")
                    _format = 'jsonld'
                # Appeler la fonction originale avec le format corrigé
                return original_read_catalog_page(_format)
            
            dcat_utils.read_catalog_page = patched_read_catalog_page
            log.info("Patch dcat_utils.read_catalog_page appliqué (gestion format 'js')")
    except ImportError:
        log.debug("ckanext.dcat.utils non encore chargé")
    except Exception as e:
        log.warning(f"Erreur lors du patch de read_catalog_page: {e}")


def _apply_dcat_patch():
    """Applique le patch au code de ckanext-dcat"""
    try:
        # Essayer plusieurs chemins d'import possibles
        dcat_base = None
        Profile_class = None
        
        # Essayer d'importer le module base
        try:
            import ckanext.dcat.profiles.base as dcat_base
            # Inspecter le module pour trouver Profile
            if hasattr(dcat_base, 'Profile'):
                Profile_class = dcat_base.Profile
            else:
                # Peut-être que Profile est dans un sous-module ou a un nom différent
                # Inspecter tous les attributs du module
                for attr_name in dir(dcat_base):
                    attr = getattr(dcat_base, attr_name)
                    # Chercher une classe qui pourrait être Profile
                    if (isinstance(attr, type) and 
                        hasattr(attr, '__init__') and 
                        'Profile' in attr_name):
                        Profile_class = attr
                        log.debug(f"Trouvé Profile comme {attr_name}")
                        break
        except ImportError as e:
            log.debug(f"Import de ckanext.dcat.profiles.base échoué: {e}")
            # Ne pas logger en warning, c'est normal si dcat n'est pas encore chargé
            return
        
        # Vérifier si le patch a déjà été appliqué
        if dcat_base and hasattr(dcat_base, '_dcat_patch_applied'):
            return
        
        # Monkey-patch la méthode __init__ de Profile
        if Profile_class and hasattr(Profile_class, '__init__'):
            original_init = Profile_class.__init__
            
            def patched_init(self, graph, compatibility_mode=False, dataset_type=None, **kwargs):
                """
                Version patchée qui gère l'absence de scheming
                Accepte dataset_type et autres arguments pour compatibilité avec les nouvelles versions de ckanext-dcat
                """
                try:
                    # Essayer d'appeler l'original avec tous les arguments possibles
                    # Certaines versions de ckanext-dcat peuvent avoir dataset_type comme argument
                    if dataset_type is not None:
                        # Si dataset_type est fourni, l'utiliser
                        try:
                            return original_init(self, graph, compatibility_mode=compatibility_mode, dataset_type=dataset_type, **kwargs)
                        except TypeError:
                            # Si l'original n'accepte pas dataset_type, essayer sans
                            try:
                                return original_init(self, graph, compatibility_mode=compatibility_mode, **kwargs)
                            except TypeError:
                                # Si ça échoue encore, essayer avec juste les arguments de base
                                return original_init(self, graph, compatibility_mode=compatibility_mode)
                    else:
                        # Pas de dataset_type, appeler normalement
                        try:
                            return original_init(self, graph, compatibility_mode=compatibility_mode, **kwargs)
                        except TypeError:
                            # Si ça échoue, essayer avec juste les arguments de base
                            return original_init(self, graph, compatibility_mode=compatibility_mode)
                except Exception as e:
                    error_msg = str(e)
                    error_type = type(e).__name__
                    # Si c'est une erreur de schéma inconnu, continuer sans scheming
                    if 'Unknown dataset schema' in error_msg or 'ObjectNotFound' in error_type:
                        log.debug(f"Scheming non disponible pour dcat ({error_type}), utilisation du schéma par défaut")
                        # Initialiser manuellement sans scheming
                        try:
                            # Utiliser dataset_type si fourni, sinon 'dataset' par défaut
                            self.dataset_type = dataset_type if dataset_type is not None else 'dataset'
                            self.g = graph
                            self.compatibility_mode = compatibility_mode
                            # Essayer d'appeler les méthodes d'initialisation qui ne nécessitent pas le schéma
                            if hasattr(self, '_init_graph'):
                                try:
                                    self._init_graph()
                                except Exception:
                                    pass
                            # Essayer d'appeler d'autres méthodes d'initialisation si elles existent
                            if hasattr(self, '_init_base'):
                                try:
                                    self._init_base()
                                except Exception:
                                    pass
                            return
                        except Exception as e2:
                            log.debug(f"Erreur lors de l'initialisation manuelle: {e2}")
                            # Si l'initialisation manuelle échoue, essayer quand même de continuer
                            self.dataset_type = dataset_type if dataset_type is not None else 'dataset'
                            self.g = graph
                            self.compatibility_mode = compatibility_mode
                            return
                    else:
                        raise
            
            Profile_class.__init__ = patched_init
            if dcat_base:
                dcat_base._dcat_patch_applied = True
            log.info("Patch dcat appliqué avec succès (monkey-patch Profile.__init__)")
        else:
            # Ne pas logger en warning à chaque fois, seulement en debug
            log.debug("Classe Profile non trouvée dans ckanext.dcat.profiles.base (normal si dcat n'est pas encore chargé)")
            
    except ImportError:
        # ckanext-dcat n'est pas encore chargé
        pass
    except Exception as e:
        log.error(f"Erreur lors de l'application du patch dcat: {e}")
        import traceback
        log.error(traceback.format_exc())


# Blueprint pour intercepter /catalog.js
dcat_patch_blueprint = Blueprint('dcat_patch', __name__)


@dcat_patch_blueprint.route('/catalog.js')
def catalog_js_handler():
    """
    Gère la route /catalog.js qui n'est pas supportée par RDFLib
    Redirige vers /catalog.jsonld (JSON-LD) qui est un format supporté
    """
    log.warning("Tentative d'accès à /catalog.js (format non supporté), redirection vers /catalog.jsonld")
    # Rediriger vers JSON-LD qui est un format RDF supporté
    return redirect('/catalog.jsonld', code=302)


class DCATPatchPlugin(plugins.SingletonPlugin):
    """
    Plugin pour patcher ckanext-dcat au démarrage
    Intercepte les appels à schema_show pour retourner None si scheming n'est pas disponible
    Et gère le format "js" non supporté par RDFLib
    """
    
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IPluginObserver)
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    
    def update_config(self, config):
        """Appelé lors de la configuration - essayer de patcher dès que possible"""
        # Essayer de patcher immédiatement, puis réessayer après un délai
        _apply_dcat_patch()
        _patch_dcat_utils()
        # Aussi essayer après un court délai au cas où dcat n'est pas encore complètement chargé
        import threading
        def delayed_patch():
            import time
            time.sleep(1.0)  # Attendre 1 seconde
            _apply_dcat_patch()
            _patch_dcat_utils()
        threading.Thread(target=delayed_patch, daemon=True).start()
    
    def get_actions(self):
        """Enregistre une action personnalisée pour schema_show"""
        # Toujours enregistrer l'action pour intercepter les appels même si scheming est disponible
        # Cela permet de gérer les cas où scheming est installé mais non configuré
        
        # Wrapper la méthode dans une fonction pour éviter AttributeError avec auth_audit_exempt
        def scheming_dataset_schema_show(context, data_dict):
            """
            Version patchée de schema_show qui retourne None si scheming n'est pas disponible
            Cette action intercepte les appels et retourne None pour éviter les erreurs
            """
            # Vérifier si l'action originale existe vraiment (scheming est configuré)
            try:
                from ckan.logic import _actions
                # Vérifier si l'action existe dans le registre original (avant notre interception)
                # En regardant dans le module ckanext.scheming directement
                try:
                    import ckanext.scheming.logic as scheming_logic
                    if hasattr(scheming_logic, 'scheming_dataset_schema_show'):
                        # L'action originale existe, essayer de l'appeler
                        original_action = scheming_logic.scheming_dataset_schema_show
                        return original_action(context, data_dict)
                except (ImportError, AttributeError):
                    pass
            except Exception:
                pass
            
            # Si on arrive ici, scheming n'est pas disponible ou non configuré
            # Ne pas logger chaque appel pour éviter le spam de logs (c'est appelé pour chaque dataset)
            # log.debug("scheming_dataset_schema_show appelée (scheming non disponible), retour de None")
            return None
        
        return {
            'scheming_dataset_schema_show': scheming_dataset_schema_show,
        }
    
    def before_load(self, plugin):
        """Appelé avant le chargement d'un plugin"""
        pass
    
    def after_load(self, plugin):
        """Appelé après le chargement d'un plugin"""
        # Appliquer le patch quand dcat est chargé
        plugin_name = str(getattr(plugin, '__name__', ''))
        plugin_class = getattr(plugin, '__class__', None)
        plugin_class_name = plugin_class.__name__ if plugin_class else ''
        
        # Détecter le plugin dcat de plusieurs façons
        is_dcat_plugin = (
            'dcat' in plugin_name.lower() and 'dcat_patch' not in plugin_name.lower()
        ) or (
            'dcat' in plugin_class_name.lower() and 'dcat_patch' not in plugin_class_name.lower()
        ) or (
            hasattr(plugin, '__module__') and 'dcat' in str(plugin.__module__).lower() and 'dcat_patch' not in str(plugin.__module__).lower()
        )
        
        if is_dcat_plugin:
            log.debug(f"Plugin dcat détecté ({plugin_name}), application du patch...")
            # Essayer immédiatement
            _apply_dcat_patch()
            _patch_dcat_utils()
            # Aussi essayer après un court délai au cas où le module n'est pas encore complètement chargé
            import threading
            def delayed_patch():
                import time
                time.sleep(0.5)  # Attendre 500ms
                _apply_dcat_patch()
                _patch_dcat_utils()
            threading.Thread(target=delayed_patch, daemon=True).start()
    
    def before_unload(self, plugin):
        """Appelé avant le déchargement d'un plugin"""
        pass

    def after_unload(self, plugin):
        """Appelé après le déchargement d'un plugin (requis par IPluginObserver)."""
        pass

    def get_blueprint(self):
        """Retourne le blueprint pour intercepter /catalog.js"""
        return dcat_patch_blueprint


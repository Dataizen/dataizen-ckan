"""
Extension CKAN pour charger les licences depuis un fichier JSON personnalisé.
"""
import json
import os
import logging
from ckan.plugins import SingletonPlugin, implements, toolkit
from ckan.plugins.interfaces import IConfigurer, IActions, IPluginObserver, IBlueprint
from ckan import model

log = logging.getLogger(__name__)


def custom_license_list(context, data_dict):
    """
    Surcharge l'action license_list pour charger les licences depuis un fichier JSON.
    """
    licenses_file = toolkit.config.get('ckan.custom_licenses_file', 
                                       '/srv/app/config_files/common/licenses.json')
    
    if not os.path.exists(licenses_file):
        log.warning(f" Fichier de licences non trouvé: {licenses_file}, utilisation des licences par défaut")
        # Importer et utiliser la fonction originale de CKAN
        from ckan.logic.action.get import license_list as ckan_license_list
        return ckan_license_list(context, data_dict)
    
    try:
        with open(licenses_file, 'r', encoding='utf-8') as f:
            licenses_data = json.load(f)
        
        log.info(f"{len(licenses_data)} licences chargées depuis {licenses_file}")
        
        # Convertir les données en format CKAN
        # CKAN attend un format spécifique avec certaines propriétés
        result = []
        for license_info in licenses_data:
            # S'assurer que toutes les propriétés requises sont présentes
            license_dict = {
                'id': license_info.get('id', ''),
                'title': license_info.get('title', ''),
                'url': license_info.get('url', ''),
                'domain_content': license_info.get('domain_content', False),
                'domain_data': license_info.get('domain_data', False),
                'domain_software': license_info.get('domain_software', False),
                'family': license_info.get('family', ''),
                'is_generic': license_info.get('is_generic', False),
                'od_conformance': license_info.get('od_conformance', 'not reviewed'),
                'osd_conformance': license_info.get('osd_conformance', 'not reviewed'),
                'maintainer': license_info.get('maintainer', ''),
                'status': license_info.get('status', 'active'),
            }
            result.append(license_dict)
        
        return result
    except Exception as e:
        log.error(f"Erreur lors du chargement des licences depuis {licenses_file}: {e}")
        # Fallback vers l'action par défaut
        from ckan.logic.action.get import license_list as ckan_license_list
        return ckan_license_list(context, data_dict)


class CustomLicensesPlugin(SingletonPlugin):
    """
    Plugin pour charger les licences depuis un fichier JSON personnalisé.
    """
    implements(IConfigurer)
    implements(IActions)
    implements(IPluginObserver)
    implements(IBlueprint)
    
    _custom_license_register = None
    _original_get_license_register = None

    def update_config(self, config):
        """
        Configure le chemin vers le fichier de licences personnalisées.
        """
        licenses_file = config.get('ckan.custom_licenses_file', 
                                   '/srv/app/config_files/common/licenses.json')
        config['ckan.custom_licenses_file'] = licenses_file
        
        # Configurer licenses_group_url pour que CKAN charge depuis le fichier JSON
        # IMPORTANT: Cela doit être fait AVANT que LicenseRegister ne soit créé
        if os.path.exists(licenses_file):
            # Utiliser 'licenses_group_url' (sans préfixe ckan.) car c'est ce que LicenseRegister cherche
            config['licenses_group_url'] = f'file://{licenses_file}'
            # log.info(f"Configuration: licenses_group_url = file://{licenses_file}")
            
            # Supprimer le registre existant s'il existe pour forcer le rechargement
            if hasattr(model.Package, '_license_register'):
                delattr(model.Package, '_license_register')
                # log.info("_license_register supprimé, sera recréé avec licenses_group_url")
            
            try:
                with open(licenses_file, 'r', encoding='utf-8') as f:
                    licenses_data = json.load(f)
                # log.info(f"Configuration: {len(licenses_data)} licences disponibles dans {licenses_file}")
            except Exception as e:
                log.warning(f" Erreur lors du chargement des licences: {e}")
        else:
            log.warning(f" Fichier de licences non trouvé: {licenses_file}")

    def _load_licenses_into_register(self, licenses_file):
        """
        Charge les licences depuis le fichier JSON dans le registre du modèle Package.
        Cela permet à l'interface web d'utiliser les mêmes licences que l'API.
        """
        try:
            with open(licenses_file, 'r', encoding='utf-8') as f:
                licenses_data = json.load(f)
            
            # Importer les classes nécessaires
            from ckan.model.license import License, LicenseRegister
            
            # Créer un nouveau LicenseRegister personnalisé
            custom_register = LicenseRegister()
            
            # Vider le registre existant et le remplacer par nos licences
            custom_register.licenses = []
            
            for license_info in licenses_data:
                # License prend un dictionnaire en argument
                license_dict = {
                    'id': license_info.get('id', ''),
                    'title': license_info.get('title', ''),
                    'url': license_info.get('url', ''),
                    'domain_content': license_info.get('domain_content', False),
                    'domain_data': license_info.get('domain_data', False),
                    'domain_software': license_info.get('domain_software', False),
                    'family': license_info.get('family', ''),
                    'is_generic': license_info.get('is_generic', False),
                    'od_conformance': license_info.get('od_conformance', 'not reviewed'),
                    'osd_conformance': license_info.get('osd_conformance', 'not reviewed'),
                    'maintainer': license_info.get('maintainer', ''),
                    'status': license_info.get('status', 'active'),
                }
                license_obj = License(license_dict)
                custom_register.licenses.append(license_obj)
            
            # Stocker le registre personnalisé comme variable de classe
            CustomLicensesPlugin._custom_license_register = custom_register
            
            # Remplacer le registre dans Package
            model.Package._license_register = custom_register
            
            # Surcharger get_license_register pour toujours retourner notre registre
            if CustomLicensesPlugin._original_get_license_register is None:
                CustomLicensesPlugin._original_get_license_register = model.Package.get_license_register
            
            @classmethod
            def custom_get_license_register(cls):
                if CustomLicensesPlugin._custom_license_register is not None:
                    return CustomLicensesPlugin._custom_license_register
                if CustomLicensesPlugin._original_get_license_register is not None:
                    return CustomLicensesPlugin._original_get_license_register()
                from ckan.model.license import LicenseRegister as LR
                if not hasattr(cls, '_license_register'):
                    cls._license_register = LR()
                return cls._license_register
            
            model.Package.get_license_register = custom_get_license_register
            
            # Surcharger aussi get_license_options pour utiliser notre registre
            original_get_license_options = model.Package.get_license_options
            
            @classmethod
            def custom_get_license_options(cls):
                register = cls.get_license_register()
                return [(l.title, l.id) for l in register.values()]
            
            model.Package.get_license_options = custom_get_license_options
            
            log.info(f"{len(licenses_data)} licences chargées dans le registre du modèle Package")
        except Exception as e:
            log.error(f"Erreur lors du chargement des licences dans le registre: {e}")
            import traceback
            log.error(traceback.format_exc())

    def before_load(self, plugin):
        """
        Appelé avant le chargement d'un plugin.
        Méthode requise par IPluginObserver.
        """
        # Pas d'action nécessaire avant le chargement
        pass

    def after_load(self, plugin):
        """
        Appelé après le chargement d'un plugin.
        Force le rechargement du registre des licences depuis le fichier JSON.
        """
        # Ne charger qu'une seule fois, quand notre propre plugin est chargé
        if plugin == self:
            # log.info("after_load appelé pour custom_licenses - rechargement du registre Package")
            
            licenses_file = toolkit.config.get('ckan.custom_licenses_file', 
                                               '/srv/app/config_files/common/licenses.json')
            
            if os.path.exists(licenses_file):
                try:
                    # Charger directement depuis le fichier et remplacer le registre
                    from ckan.model.license import LicenseRegister
                    
                    # Créer un nouveau registre et charger depuis le fichier
                    custom_register = LicenseRegister()
                    license_url = f'file://{licenses_file}'
                    custom_register.load_licenses(license_url)
                    
                    # Stocker le registre personnalisé comme variable de classe
                    CustomLicensesPlugin._custom_license_register = custom_register
                    
                    # Sauvegarder la méthode originale si pas déjà fait
                    if CustomLicensesPlugin._original_get_license_register is None:
                        CustomLicensesPlugin._original_get_license_register = model.Package.get_license_register
                    
                    # Surcharger get_license_register pour toujours retourner notre registre
                    @classmethod
                    def custom_get_license_register(cls):
                        if CustomLicensesPlugin._custom_license_register is not None:
                            return CustomLicensesPlugin._custom_license_register
                        # Fallback vers l'original si notre registre n'est pas encore chargé
                        if CustomLicensesPlugin._original_get_license_register is not None:
                            return CustomLicensesPlugin._original_get_license_register()
                        # Dernier recours: créer un nouveau registre
                        if not hasattr(cls, '_license_register'):
                            cls._license_register = LicenseRegister()
                        return cls._license_register
                    
                    model.Package.get_license_register = custom_get_license_register
                    
                    # Remplacer aussi directement le registre
                    model.Package._license_register = custom_register
                    
                    # Surcharger aussi get_license_options pour utiliser notre registre
                    # IMPORTANT: Faire cela APRÈS avoir remplacé le registre
                    original_get_license_options = model.Package.get_license_options
                    
                    @classmethod
                    def custom_get_license_options(cls):
                        # Utiliser get_license_register() qui retournera notre registre personnalisé
                        register = cls.get_license_register()
                        return [(l.title, l.id) for l in register.values()]
                    
                    model.Package.get_license_options = custom_get_license_options
                    
                    # log.info("get_license_options surchargée pour utiliser le registre personnalisé")
                    
                    # log.info(f"{len(custom_register)} licences chargées dans le registre Package depuis {licenses_file}")
                    
                    # Vérifier LOV2
                    lov2 = custom_register.get('lov2')
                    if lov2:
                        # log.info(f"LOV2 confirmée dans le registre: {lov2.title}")
                        pass
                    else:
                        log.warning(f" LOV2 non trouvée dans le registre")
                except Exception as e:
                    log.error(f"Erreur lors du rechargement du registre: {e}")
                    import traceback
                    log.error(traceback.format_exc())
            else:
                log.warning(f" Fichier de licences non trouvé: {licenses_file}")

    def before_unload(self, plugin):
        """
        Appelé avant le déchargement d'un plugin.
        Méthode requise par IPluginObserver.
        """
        # Pas d'action nécessaire avant le déchargement
        pass

    def after_unload(self, plugin):
        """
        Appelé après le déchargement d'un plugin.
        Méthode requise par IPluginObserver.
        """
        # Pas d'action nécessaire après le déchargement
        pass

    def get_actions(self):
        """
        Surcharge l'action license_list pour utiliser notre fonction personnalisée.
        """
        return {
            'license_list': custom_license_list,
        }

    def get_blueprint(self):
        """
        Retourne le blueprint pour ajouter une route GET à license_list.
        """
        from ckanext.custom_licenses.views import get_blueprint
        return get_blueprint()


# -*- coding: utf-8 -*-
"""Plugin CKAN `ckanext-dolfin` : harmonisation sémantique DOLFIN.

Regroupe toute la chaîne d'harmonisation (auparavant dispersée dans
ckanext-dataload-router et ckanext-ogc) :
 - magasin de modèles pivot + interface (actions + blueprint + templates),
 - hook d'harmonisation au dépôt (génère NGSI-LD / CSV / GeoJSON depuis un
   mapping `dolfin_mapping`),
 - suivi de dérive : quand un modèle change, les jeux impactés sont signalés
   sur leur fiche (extra `dolfin_model_latest_rev`) et peuvent être régénérés.

Conventions partagées avec ckanext-dataload-router (routeur de dépôt) :
 - `dolfin_mapping` (extra) : mapping colonne->champ du jeu,
 - `harmonized_from` (champ ressource) : marque les sorties dérivées,
 - `xloader_skip` (champ ressource) : n'envoie pas la sortie au datastore.
"""
import os
import json
import tempfile
import logging

from werkzeug.datastructures import FileStorage

from ckan.plugins import toolkit, SingletonPlugin, implements
from ckan.plugins.interfaces import (
    IResourceController, IActions, IAuthFunctions, IConfigurer, IBlueprint,
    ITemplateHelpers,
)

from ckanext.dolfin import views as _views

log = logging.getLogger(__name__)


class DolfinPlugin(SingletonPlugin):
    implements(IResourceController, inherit=True)
    implements(IActions, inherit=True)
    implements(IAuthFunctions, inherit=True)
    implements(IConfigurer, inherit=True)
    implements(IBlueprint, inherit=True)
    implements(ITemplateHelpers, inherit=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Garde de ré-entrance : évite qu'une ressource déclenche sa propre
        # ré-harmonisation en boucle pendant la création des sorties.
        self._harmonizing = set()

    # --- IConfigurer -------------------------------------------------------- #
    def update_config(self, config_):
        toolkit.add_template_directory(config_, 'templates')

    # --- IBlueprint --------------------------------------------------------- #
    def get_blueprint(self):
        return _views.get_blueprint()

    # --- IActions ----------------------------------------------------------- #
    def get_actions(self):
        return {
            'dolfin_model_list': _views.action_dolfin_model_list,
            'dolfin_model_save': _views.action_dolfin_model_save,
            'dolfin_model_delete': _views.action_dolfin_model_delete,
            'dolfin_model_usage': _views.action_dolfin_model_usage,
            'dolfin_gpu_status': _views.action_dolfin_gpu_status,
            'dolfin_regenerate': _views.action_dolfin_regenerate,
        }

    # --- IAuthFunctions ----------------------------------------------------- #
    def get_auth_functions(self):
        def _model_manage_auth(context, data_dict=None):
            user = context.get('auth_user_obj')
            if user and getattr(user, 'sysadmin', False):
                return {'success': True}
            return {'success': False, 'msg': 'Reserve aux administrateurs.'}

        def _logged_in_auth(context, data_dict=None):
            return {'success': bool(context.get('user'))}

        def _regen_auth(context, data_dict=None):
            # éditeur d'org / sysadmin : réutilise la sémantique package_update
            try:
                toolkit.check_access('package_update', context,
                                     {'id': (data_dict or {}).get('id')})
                return {'success': True}
            except toolkit.NotAuthorized:
                return {'success': False, 'msg': 'Reserve aux editeurs du jeu.'}

        return {
            'dolfin_model_list': _model_manage_auth,
            'dolfin_model_save': _model_manage_auth,
            'dolfin_model_delete': _model_manage_auth,
            'dolfin_model_usage': _model_manage_auth,
            'dolfin_gpu_status': _logged_in_auth,
            'dolfin_regenerate': _regen_auth,
        }

    # --- ITemplateHelpers --------------------------------------------------- #
    def get_helpers(self):
        return {'dolfin_stale': dolfin_stale, 'dolfin_schema': dolfin_schema}

    # --- IResourceController ------------------------------------------------ #
    def after_resource_create(self, context, resource):
        # Ne jamais retraiter une sortie harmonisée (évite les boucles).
        if resource.get('harmonized_from'):
            return
        try:
            self._harmonize_if_mapped(dict(context, ignore_auth=True), resource)
        except Exception as e:
            log.error(f"[dolfin] harmonisation après create échec: {e}")

    def after_resource_update(self, context, resource):
        if resource.get('harmonized_from'):
            return
        try:
            self._harmonize_if_mapped(dict(context, ignore_auth=True), resource)
        except Exception as e:
            log.error(f"[dolfin] harmonisation après update échec: {e}")

    # --- Cœur de l'harmonisation ------------------------------------------- #
    def _harmonize_if_mapped(self, context, resource):
        """Si le jeu porte un mapping DOLFIN (extra `dolfin_mapping`), génère les
        sorties harmonisées NGSI-LD / CSV / GeoJSON depuis les données datastore
        de cette ressource, et tamponne la révision du modèle sur le jeu.
        Idempotent (régénère les sorties précédentes)."""
        if (resource.get('format') or '').lower() != 'csv' or not resource.get('datastore_active'):
            return
        rid = resource['id']
        if rid in self._harmonizing:
            return
        try:
            pkg = toolkit.get_action('package_show')(dict(context, ignore_auth=True),
                                                     {'id': resource['package_id']})
        except Exception as e:
            log.error(f"[dolfin] package_show impossible: {e}")
            return
        mapping_raw = next((e.get('value') for e in pkg.get('extras', [])
                            if e.get('key') == 'dolfin_mapping'), None)
        if not mapping_raw:
            return
        try:
            mapping = json.loads(mapping_raw)
        except Exception as e:
            log.error(f"[dolfin] mapping JSON invalide: {e}")
            return
        self._harmonizing.add(rid)
        try:
            from ckanext.dolfin import harmonize_generic
            recs = toolkit.get_action('datastore_search')(
                dict(context, ignore_auth=True),
                {'resource_id': rid, 'limit': 100000}).get('records', [])
            entities, warnings = harmonize_generic.harmonize(recs, mapping)
            for w in warnings:
                log.info(f"[dolfin] {w}")
            base = resource.get('name') or 'données'
            dtype = mapping.get('type', '?')

            def _create_out(suffix, fmt, filename, content, label):
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                try:
                    tmp.write(content)
                    tmp.close()
                    with open(tmp.name, 'rb') as fh:
                        toolkit.get_action('resource_create')(dict(context, ignore_auth=True), {
                            'package_id': resource['package_id'],
                            'name': f"{base} — harmonisé ({label})",
                            'format': fmt,
                            'harmonized_from': rid,
                            'xloader_skip': True,
                            'description': f"Ressource harmonisée (profil DOLFIN « {dtype} ») "
                                           f"générée automatiquement depuis « {base} ».",
                            'upload': FileStorage(fh, filename),
                        })
                finally:
                    try:
                        os.remove(tmp.name)
                    except OSError:
                        pass

            # Régénération : supprimer les anciennes sorties harmonisées de cette ressource
            # (sinon un changement de mapping/de données ne serait pas répercuté).
            for r in pkg.get('resources', []):
                if r.get('harmonized_from') == rid:
                    try:
                        toolkit.get_action('resource_delete')(
                            dict(context, ignore_auth=True), {'id': r['id']})
                    except Exception as e:
                        log.warning(f"[dolfin] suppression ancienne sortie {r.get('id')}: {e}")

            # Writers : NGSI-LD (Smart Data Models), CSV harmonisé, et GeoJSON si géolocalisé.
            doc = {'@context': mapping['context'], 'entities': entities} if mapping.get('context') else entities
            _create_out('.jsonld', 'JSON-LD', 'harmonized.jsonld',
                        json.dumps(doc, ensure_ascii=False, indent=2).encode('utf-8'),
                        'NGSI-LD / Smart Data Models')
            _create_out('.csv', 'CSV', 'harmonized.csv',
                        harmonize_generic.to_harmonized_csv(recs, mapping).encode('utf-8'),
                        'CSV harmonisé')
            if mapping.get('location'):
                _create_out('.geojson', 'GeoJSON', 'harmonized.geojson',
                            json.dumps(harmonize_generic.to_geojson(recs, mapping),
                                       ensure_ascii=False).encode('utf-8'),
                            'GeoJSON harmonisé')
            log.info(f"[dolfin] {rid}: {len(entities)} entités -> JSON-LD + CSV"
                     + (" + GeoJSON" if mapping.get('location') else ""))
            # Suivi de dérive : tamponner la révision du modèle harmonisé sur le jeu,
            # pour que la fiche détecte quand le modèle changera par la suite.
            try:
                self._stamp_model_rev(context, pkg, mapping.get('type'))
            except Exception as e:
                log.warning(f"[dolfin] tampon de révision échec pour {pkg.get('name')}: {e}")
        except Exception as e:
            log.error(f"[dolfin] harmonisation échec pour {rid}: {e}")
        finally:
            self._harmonizing.discard(rid)

    def _stamp_model_rev(self, context, pkg, type_):
        """Pose sur le jeu la révision du modèle au moment de l'harmonisation :
        `dolfin_model_rev` == `dolfin_model_latest_rev` (donc non périmé), plus
        l'auteur. Pattern merge-preserving (relire tous les extras puis réécrire)
        pour ne pas écraser `dolfin_mapping` ni les extras OGC."""
        rev, changed_by = _views._model_rev_for_type(type_)
        # Relire le paquet (le hook a créé des ressources depuis le package_show initial).
        fresh = toolkit.get_action('package_show')(dict(context, ignore_auth=True),
                                                   {'id': pkg['id']})
        extras = {e['key']: e['value'] for e in fresh.get('extras', [])}
        extras['dolfin_model_rev'] = rev
        extras['dolfin_model_latest_rev'] = rev
        if changed_by:
            extras['dolfin_model_changed_by'] = changed_by
        updated = [{'key': k, 'value': v} for k, v in extras.items()]
        toolkit.get_action('package_patch')(dict(context, ignore_auth=True),
                                            {'id': pkg['id'], 'extras': updated})


def dolfin_schema(pkg):
    """Helper de template (affichage PUBLIC sur la fiche) : schéma DOLFIN d'un jeu.
    Renvoie None si le jeu ne porte pas de mapping, sinon un dict
    {type, titre, desc, context, id_field, geo, rows} où rows = [(concept, colonne)].
    Documente comment le jeu est harmonisé (modèle pivot Smart Data Models + colonnes).
    Lecture pure des extras (aucun appel privilégié)."""
    try:
        extras = pkg.get('extras') or []
        ex = extras if isinstance(extras, dict) else {e.get('key'): e.get('value') for e in extras}
        raw = ex.get('dolfin_mapping')
        if not raw:
            return None
        mapping = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(mapping, dict):
        return None
    fields = mapping.get('fields') if isinstance(mapping.get('fields'), dict) else {}
    rows = [(k, v) for k, v in fields.items() if v]
    loc = mapping.get('location') or {}
    geo = {'lon': loc.get('lon'), 'lat': loc.get('lat')} if (loc.get('lon') and loc.get('lat')) else None
    dtype = mapping.get('type')
    info = _views.model_by_type(dtype) or {}
    return {
        'type': dtype,
        'titre': info.get('titre'),
        'desc': info.get('desc'),
        'context': mapping.get('context'),
        'id_field': mapping.get('id_field'),
        'geo': geo,
        'rows': rows,
    }


def dolfin_stale(pkg):
    """Helper de template : le jeu utilise-t-il un modèle DOLFIN dont la révision a
    changé depuis sa dernière harmonisation ? Lecture pure des extras (aucun appel
    privilégié). Renvoie None (à jour) ou un dict {type, changed_by} (périmé)."""
    try:
        extras = pkg.get('extras') or []
        if isinstance(extras, dict):
            ex = extras
        else:
            ex = {e.get('key'): e.get('value') for e in extras}
        latest = ex.get('dolfin_model_latest_rev')
        if not latest:
            return None
        rev = ex.get('dolfin_model_rev')
        if latest == rev:
            return None
        dtype = None
        raw = ex.get('dolfin_mapping')
        if raw:
            try:
                dtype = json.loads(raw).get('type')
            except Exception:
                dtype = None
        return {'type': dtype, 'changed_by': ex.get('dolfin_model_changed_by')}
    except Exception:
        return None

"""
ckanext-dataload-router plugin.

Routes resources uploaded through the CKAN API to the appropriate loader
(xloader for tabular data, datapusher otherwise) and prepares the
datastore. Performs CSV / GeoJSON conversion, adds geometry columns when
possible, and exposes a few custom auth functions used by the router.
"""
import os
import shutil
import uuid
import json
import csv
import datetime
import subprocess
import tempfile
import logging
import pandas as pd
import requests
from werkzeug.datastructures import FileStorage
from .geometry_utils import create_geo_view_if_possible
from flask import has_request_context, request

from ckan.common import config

from ckan.plugins import toolkit, SingletonPlugin, implements
from ckan.plugins.interfaces import IResourceController, IAuthFunctions
from ckan import model
from ckan.plugins.interfaces import IActions
from ckanext.datastore.logic.action import datastore_search_sql



log = logging.getLogger(__name__)

from ckanext.datastore.backend import postgres

csv.field_size_limit(800 * 1024 * 1024)

def patch_backend_allowed_sql_functions():
    def get_allowed_sql_functions():
        path = '/srv/app/allowed_functions.txt'
        try:
            with open(path, 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[DLR] Impossible de lire allowed_functions.txt : {e}")
            return []

    if not hasattr(postgres.DatastorePostgresqlBackend, 'allowed_sql_functions'):
        setattr(postgres.DatastorePostgresqlBackend, 'allowed_sql_functions', get_allowed_sql_functions())

patch_backend_allowed_sql_functions()

def get_allowed_sql_functions():
    path = os.environ.get('CKAN_SQL_ALLOWED_FUNCTIONS_FILE', '/srv/app/allowed_functions.txt')
    try:
        with open(path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        log.warning(f"[DLR] Impossible de lire allowed_functions.txt : {e}")
        return []




# --- Garde-fou mémoire ------------------------------------------------------- #
# Certaines préparations chargent le fichier ENTIER en mémoire (conversion
# GeoJSON/JSON -> CSV, pandas the_geom, éclatement xlsx). Sur un gros fichier c'est
# une bombe mémoire : incident du 09/09/2026, un GeoJSON de 5,5 Go déposé a fait
# grimper CKAN à ~20 Go RSS et menacé la plateforme d'OOM. Au-delà du seuil, on SAUTE
# ce traitement synchrone : la ressource reste téléchargeable (url_type=upload), le
# chargement datastore lourd n'est simplement pas fait automatiquement.
# TODO (backlog) : désynchroniser dataload_router (worker/passes) plutôt que borner.
DATALOAD_MAX_SYNC_BYTES = int(os.environ.get('DATALOAD_MAX_SYNC_MB', '300')) * 1024 * 1024


def _too_big_for_sync(resource):
    """True si le fichier local de la ressource dépasse le seuil de traitement synchrone."""
    try:
        if (resource or {}).get('url_type') != 'upload':
            return False
        from ckan.lib.uploader import get_resource_uploader
        p = get_resource_uploader(resource).get_path(resource['id'])
        if p and os.path.exists(p) and os.path.getsize(p) > DATALOAD_MAX_SYNC_BYTES:
            log.warning(
                f"[DLR] Fichier volumineux ({os.path.getsize(p)} o > {DATALOAD_MAX_SYNC_BYTES} o) : "
                f"traitement synchrone (conversion/pandas/xlsx) SAUTE pour {resource.get('id')}. "
                f"Ressource telechargeable ; datastore non charge automatiquement.")
            return True
    except Exception as e:
        log.warning(f"[DLR] _too_big_for_sync indeterminable pour {(resource or {}).get('id')} : {e}")
    return False


class DataloadRouter(SingletonPlugin):
    implements(IResourceController, inherit=True)
    implements(IActions, inherit=True)
    implements(IAuthFunctions, inherit=True)

    # NB : toute la chaîne DOLFIN (modèles, harmonisation, blueprint, templates) a été
    # extraite dans l'extension dédiée `ckanext-dolfin`. Ce routeur ne gère plus que la
    # préparation/le routage des ressources (xloader, the_geom, éclatement xlsx, CSV).

    def __init__(self, *args, **kwargs):
        super(DataloadRouter, self).__init__(*args, **kwargs)
        # Flag pour éviter la récursion infinie lors de _add_the_geom_column_if_possible
        self._processing_geom_column = set()
        # Flag anti-récursion pour l'éclatement des classeurs xlsx en ressources CSV
        self._expanding_xlsx = set()
        # Flag anti-récursion pour la déduplication des colonnes CSV en double
        self._deduping_columns = set()
    
    def _add_the_geom_column_if_possible(self, context, resource):
        # Protection contre la récursion infinie
        resource_id = resource.get('id')
        if resource_id in self._processing_geom_column:
            log.debug(f"[DLR] _add_the_geom_column_if_possible déjà en cours pour {resource_id}, skip pour éviter la récursion")
            return
        if _too_big_for_sync(resource):
            return

        if (resource.get("format") or "").lower() == "csv" and resource.get("url_type") == "upload":
            # Marquer cette ressource comme en cours de traitement
            self._processing_geom_column.add(resource_id)
            try:
                from ckan.lib.uploader import get_resource_uploader
                uploader = get_resource_uploader(resource)
                local_path = uploader.get_path(resource['id'])

                # Lecture robuste du CSV avec gestion des erreurs de parsing
                df = None
                # Vérifier la version de pandas pour utiliser la bonne syntaxe
                pandas_version = pd.__version__
                use_on_bad_lines = tuple(map(int, pandas_version.split('.')[:2])) >= (1, 3)
                
                try:
                    # Essayer d'abord avec les paramètres par défaut (plus rapide)
                    if use_on_bad_lines:
                        df = pd.read_csv(local_path, on_bad_lines='skip', engine='python', encoding='utf-8')
                    else:
                        # Pour pandas < 1.3, utiliser error_bad_lines
                        df = pd.read_csv(local_path, error_bad_lines=False, warn_bad_lines=False, engine='python', encoding='utf-8')
                except Exception as e1:
                    log.warning(f"[DLR] Erreur lors de la lecture CSV avec paramètres par défaut: {e1}, tentative avec options alternatives...")
                    try:
                        # Essayer avec auto-détection du séparateur et options plus permissives
                        read_params = {
                            'sep': None,  # Auto-détection du séparateur
                            'engine': 'python',
                            'encoding': 'utf-8',
                            'quoting': 1,  # QUOTE_ALL
                            'skipinitialspace': True
                        }
                        if use_on_bad_lines:
                            read_params['on_bad_lines'] = 'skip'
                        else:
                            read_params['error_bad_lines'] = False
                            read_params['warn_bad_lines'] = False
                        df = pd.read_csv(local_path, **read_params)
                    except Exception as e2:
                        log.warning(f"[DLR] Erreur avec séparateur auto-détecté: {e2}, tentative avec point-virgule...")
                        try:
                            # Essayer avec point-virgule comme séparateur
                            read_params = {
                                'sep': ';',
                                'engine': 'python',
                                'encoding': 'utf-8',
                                'quoting': 1,
                                'skipinitialspace': True
                            }
                            if use_on_bad_lines:
                                read_params['on_bad_lines'] = 'skip'
                            else:
                                read_params['error_bad_lines'] = False
                                read_params['warn_bad_lines'] = False
                            df = pd.read_csv(local_path, **read_params)
                        except Exception as e3:
                            log.error(f"[DLR] Impossible de lire le CSV {local_path} avec toutes les méthodes tentées: {e3}")
                            raise

                if df is None or df.empty:
                    log.warning(f"[DLR] Le CSV {local_path} est vide ou n'a pas pu être lu")
                    return

                lat_col_candidates = ['latitude', 'lat']
                lon_col_candidates = ['longitude', 'lon', 'lng']
                lat_col = next((col for col in lat_col_candidates if col in df.columns), None)
                lon_col = next((col for col in lon_col_candidates if col in df.columns), None)

                if lat_col and lon_col:
                    log.debug(f"[DLR] Latitude/longitude détectées : {lat_col}, {lon_col}")
                    
                    # Vérifier que les colonnes contiennent des valeurs numériques valides
                    try:
                        # Convertir en numérique, en gérant les erreurs
                        df[lat_col] = pd.to_numeric(df[lat_col], errors='coerce')
                        df[lon_col] = pd.to_numeric(df[lon_col], errors='coerce')
                        
                        # Filtrer les valeurs invalides (NaN, None, hors limites géographiques)
                        valid_mask = (
                            pd.notnull(df[lat_col]) & 
                            pd.notnull(df[lon_col]) &
                            (df[lat_col] >= -90) & (df[lat_col] <= 90) &
                            (df[lon_col] >= -180) & (df[lon_col] <= 180)
                        )
                        
                        # Créer la colonne the_geom seulement pour les lignes valides
                        df['the_geom'] = None
                        if valid_mask.any():
                            df.loc[valid_mask, 'the_geom'] = df.loc[valid_mask].apply(
                                lambda r: f"POINT({r[lon_col]} {r[lat_col]})",
                                axis=1
                            )
                            log.info(f"[DLR] Colonne the_geom créée pour {valid_mask.sum()} lignes valides sur {len(df)}")
                        else:
                            log.warning(f"[DLR] Aucune ligne avec des coordonnées valides trouvée")
                            return
                    except Exception as e_geom:
                        log.error(f"[DLR] Erreur lors de la création de the_geom: {e_geom}", exc_info=True)
                        return

                    # Sauvegarder le CSV modifié
                    tmp_path = None
                    try:
                        tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix='.csv').name
                        df.to_csv(tmp_path, index=False, encoding='utf-8')
                        
                        with open(tmp_path, 'rb') as f:
                            fs = FileStorage(stream=f, filename="cleaned.csv", content_type='text/csv')
                            # Utiliser un contexte avec un flag pour éviter la récursion
                            patch_context = dict(context)
                            patch_context['_dataload_router_skip_geom'] = True
                            try:
                                toolkit.get_action("resource_patch")(patch_context, {
                                    "id": resource["id"],
                                    "upload": fs,
                                    "url_type": "upload",
                                    "format": "CSV"
                                })
                                log.info(f"[DLR] Colonne the_geom ajoutée à la ressource {resource['id']}")
                            except Exception as e_patch:
                                log.error(f"[DLR] Erreur lors du resource_patch: {e_patch}", exc_info=True)
                                # Rollback explicite de la session SQLAlchemy en cas d'erreur
                                try:
                                    import ckan.model as model
                                    if hasattr(model, 'Session'):
                                        model.Session.rollback()
                                        log.info(f"[DLR] Session SQLAlchemy rollback effectué après erreur")
                                except Exception as e_rollback:
                                    log.error(f"[DLR] Erreur lors du rollback: {e_rollback}")
                                raise
                        
                    except Exception as e_save:
                        log.error(f"[DLR] Erreur lors de la sauvegarde du CSV modifié: {e_save}", exc_info=True)
                        # Rollback explicite de la session SQLAlchemy en cas d'erreur
                        try:
                            import ckan.model as model
                            if hasattr(model, 'Session'):
                                model.Session.rollback()
                                log.info(f"[DLR] Session SQLAlchemy rollback effectué après erreur de sauvegarde")
                        except Exception as e_rollback:
                            log.error(f"[DLR] Erreur lors du rollback: {e_rollback}")
                    finally:
                        # Retirer le flag de traitement et nettoyer le fichier temporaire
                        self._processing_geom_column.discard(resource_id)
                        if tmp_path and os.path.exists(tmp_path):
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass

                else:
                    log.debug("[DLR] Aucune colonne lat/lon ou latitude/longitude détectée.")
                    # Retirer le flag même si on ne traite pas
                    self._processing_geom_column.discard(resource_id)

            except Exception as e:
                # Retirer le flag en cas d'erreur
                self._processing_geom_column.discard(resource_id)
                # Rollback explicite de la session SQLAlchemy en cas d'erreur
                try:
                    import ckan.model as model
                    if hasattr(model, 'Session'):
                        model.Session.rollback()
                        log.info(f"[DLR] Session SQLAlchemy rollback effectué après erreur générale")
                except Exception as e_rollback:
                    log.error(f"[DLR] Erreur lors du rollback: {e_rollback}")
                log.error(f"[DLR] Erreur dans _add_the_geom_column_if_possible: {e}", exc_info=True)
                log.error(f"[DLR] Erreur lors de l'ajout automatique de the_geom depuis lat/lon : {e}", exc_info=True)

    @staticmethod
    def _sniff_delimiter(header_line):
        """Devine le séparateur d'une ligne d'en-tête : celui qui produit le plus
        de colonnes parmi virgule, point-virgule, tabulation, barre verticale."""
        best, best_n = ',', -1
        for cand in (',', ';', '\t', '|'):
            try:
                n = len(next(csv.reader([header_line], delimiter=cand)))
            except Exception:
                n = 0
            if n > best_n:
                best, best_n = cand, n
        return best

    @staticmethod
    def _dedupe_names(names):
        """Rend les noms de colonnes uniques : la 1re occurrence est conservée
        telle quelle, les suivantes reçoivent un suffixe (col, col_2, col_3...).
        Un nom vide dupliqué devient colonne_2, colonne_3... Renvoie (noms, modifié)."""
        seen, out, changed = set(), [], False
        for raw in names:
            cand = raw if raw is not None else ''
            if cand in seen:
                base = cand if cand != '' else 'colonne'
                k = 1
                while True:
                    k += 1
                    nc = f"{base}_{k}"
                    if nc not in seen:
                        cand = nc
                        break
                changed = True
            seen.add(cand)
            out.append(cand)
        return out, changed

    def _dedupe_csv_columns_if_needed(self, context, resource):
        """Renomme les colonnes en double d'un CSV uploadé AVANT que xloader ne le
        charge (xloader refuse les en-têtes dupliqués : « Duplicate column names are
        not supported »). Réécriture légère de la SEULE ligne d'en-tête : le corps du
        fichier est recopié octet pour octet (sûr pour les gros fichiers, données
        préservées à l'identique). No-op s'il n'y a pas de doublon. Idempotent et
        anti-récursion (le patch interne repasse mais ne trouve plus de doublon)."""
        # ne pas retraiter pendant un patch interne (dedup ou the_geom)
        if context.get('_dataload_router_skip_dedup') or context.get('_dataload_router_skip_geom'):
            return
        if (resource.get('format') or '').lower() != 'csv' or resource.get('url_type') != 'upload':
            return
        rid = resource.get('id')
        if not rid or rid in self._deduping_columns:
            return
        try:
            from ckan.lib.uploader import get_resource_uploader
            local_path = get_resource_uploader(resource).get_path(rid)
        except Exception:
            return
        if not local_path or not os.path.exists(local_path):
            return
        # lire uniquement la 1re ligne (en-tête), en binaire, jusqu'au 1er saut de ligne
        try:
            first = b''
            with open(local_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    first += chunk
                    if first.find(b'\n') != -1 or len(first) > 1_048_576:
                        break
        except Exception as e:
            log.error(f"[DLR][dedup] lecture en-tête impossible {rid}: {e}")
            return
        nl = first.find(b'\n')
        header_bytes = first if nl == -1 else first[:nl]
        body_offset = len(first) if nl == -1 else nl + 1
        header_line = header_bytes.decode('utf-8', errors='replace').lstrip('﻿').rstrip('\r')
        if not header_line.strip():
            return
        delim = self._sniff_delimiter(header_line)
        try:
            names = next(csv.reader([header_line], delimiter=delim))
        except Exception:
            return
        new_names, changed = self._dedupe_names(names)
        if not changed:
            return
        renommees = [f"{a} -> {b}" for a, b in zip(names, new_names) if a != b]
        log.info(f"[DLR][dedup] {rid} : colonnes en double renommées ({delim!r}) : {', '.join(renommees)}")
        self._deduping_columns.add(rid)
        tmp_path = None
        try:
            import io
            buf = io.StringIO()
            csv.writer(buf, delimiter=delim).writerow(new_names)
            header_out = buf.getvalue().encode('utf-8')
            tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix='.csv').name
            with open(tmp_path, 'wb') as out, open(local_path, 'rb') as src:
                out.write(header_out)
                src.seek(body_offset)
                shutil.copyfileobj(src, out)
            with open(tmp_path, 'rb') as f:
                fs = FileStorage(stream=f, filename="dedup.csv", content_type='text/csv')
                patch_context = dict(context)
                patch_context['_dataload_router_skip_dedup'] = True
                toolkit.get_action("resource_patch")(patch_context, {
                    "id": rid, "upload": fs, "url_type": "upload", "format": "CSV",
                })
            log.info(f"[DLR][dedup] en-tête réécrit pour {rid} ({len(renommees)} colonne(s))")
        except Exception as e:
            log.error(f"[DLR][dedup] échec réécriture {rid}: {e}", exc_info=True)
            try:
                if hasattr(model, 'Session'):
                    model.Session.rollback()
            except Exception:
                pass
        finally:
            self._deduping_columns.discard(rid)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _expand_xlsx_to_csv(self, context, resource):
        """Éclate un classeur xlsx/xls déposé en une ressource CSV par onglet.
        Chaque CSV suit ensuite la chaîne CSV normale (datastore -> aperçu, API,
        the_geom si colonnes lat/lon -> carte). Le xlsx d'origine reste en pièce
        jointe téléchargeable. Idempotent (ne ré-éclate pas) et anti-récursion."""
        name = (resource.get('name') or '')
        url = (resource.get('url') or '')
        fmt = (resource.get('format') or '').lower()
        is_x = fmt in ('xlsx', 'xls') or name.lower().endswith(('.xlsx', '.xls')) \
            or url.lower().endswith(('.xlsx', '.xls'))
        if not is_x or resource.get('url_type') != 'upload':
            return
        if _too_big_for_sync(resource):
            return
        rid = resource['id']
        if rid in self._expanding_xlsx:
            return
        try:
            pkg = toolkit.get_action('package_show')(dict(context, ignore_auth=True),
                                                     {'id': resource['package_id']})
        except Exception as e:
            log.error(f"[DLR][xlsx] package_show impossible: {e}")
            return
        # idempotence : ne pas ré-éclater si des ressources filles existent déjà
        if any(r.get('xlsx_source') == rid for r in pkg.get('resources', [])):
            log.info(f"[DLR][xlsx] {rid} déjà éclaté, skip")
            return
        self._expanding_xlsx.add(rid)
        try:
            from ckan.lib.uploader import get_resource_uploader
            local_path = get_resource_uploader(resource).get_path(rid)
            is_xls = name.lower().endswith('.xls') or url.lower().endswith('.xls')
            engine = 'xlrd' if is_xls else 'openpyxl'
            try:
                sheets = pd.read_excel(local_path, sheet_name=None, dtype=str, engine=engine)
            except Exception as e:
                log.error(f"[DLR][xlsx] lecture impossible ({engine}) pour {rid}: {e}")
                return
            base = name
            for suf in ('.xlsx', '.xls'):
                if base.lower().endswith(suf):
                    base = base[:-len(suf)]
            base = base.strip() or 'donnees'
            multi = len([1 for df in sheets.values() if df is not None and not df.dropna(how='all').empty]) > 1
            created = 0
            for sheet_name, df in sheets.items():
                if df is None:
                    continue
                df = df.dropna(how='all').dropna(axis=1, how='all')
                if df.empty or df.shape[1] == 0:
                    continue
                tmp = tempfile.NamedTemporaryFile(suffix='.csv', delete=False)
                try:
                    df.to_csv(tmp.name, index=False, encoding='utf-8')
                    tmp.close()
                    libelle = f"{base} — {sheet_name}" if multi else base
                    slug = ''.join(c if c.isalnum() else '_' for c in str(sheet_name)).strip('_') or 'feuille'
                    with open(tmp.name, 'rb') as fh:
                        toolkit.get_action('resource_create')(dict(context, ignore_auth=True), {
                            'package_id': resource['package_id'],
                            'name': libelle,
                            'format': 'CSV',
                            'xlsx_source': rid,
                            'description': f"Onglet « {sheet_name} » du classeur {name}.",
                            'upload': FileStorage(fh, f"{slug}.csv"),
                        })
                    created += 1
                except Exception as e:
                    log.error(f"[DLR][xlsx] création CSV onglet {sheet_name} échec: {e}")
                finally:
                    try:
                        os.remove(tmp.name)
                    except OSError:
                        pass
            log.info(f"[DLR][xlsx] {rid}: {created} onglet(s) convertis en ressources CSV")
        finally:
            self._expanding_xlsx.discard(rid)

    def get_actions(self):
        original_action = datastore_search_sql

        def secure_sql_action(context, data_dict):
            log.info(f"[DLR] datastore_search_sql: data_dict = {data_dict}")

            # Support GET avec ?sql=... dans l'URL si 'sql' absent de data_dict
            if not data_dict.get('sql') and has_request_context():
                query_param = request.args.get('sql')
                if query_param:
                    log.debug(f"[DLR] Récupéré 'sql' depuis request.args : {query_param}")
                    data_dict['sql'] = query_param

            if not data_dict.get('sql'):
                raise toolkit.ValidationError('Missing value')

            query = data_dict['sql']
            allowed = get_allowed_sql_functions()

            # Vérif de sécurité basique
            if ';' in query:
                raise toolkit.ValidationError("Usage de SQL potentiellement dangereux.")
            
            # Vérification : détecter les identifiants vides (tables/colonnes vides)
            # Les requêtes avec "" ou des identifiants vides causent des erreurs SQL
            import re
            # Détecter les patterns comme from "", select "" as, etc.
            empty_identifier_patterns = [
                r'from\s+""',           # from ""
                r'from\s+\'\'',         # from ''
                r'select\s+""',          # select ""
                r'select\s+\'\'',       # select ''
                r'select\s+\s+as\s+',  # select   as (colonne vide avant as)
                r'from\s+""\s+where',   # from "" where
                r'from\s+""\s+limit',   # from "" limit
            ]
            for pattern in empty_identifier_patterns:
                if re.search(pattern, query, re.IGNORECASE):
                    log.warning(f"[DLR] Requête SQL rejetée : identifiant vide détecté dans '{query[:100]}...'")
                    raise toolkit.ValidationError(
                        "Requête SQL invalide : identifiant vide détecté. "
                        "Vérifiez que la ressource a un datastore actif."
                    )
            
             # Vérification : au moins une fonction autorisée utilisée
            for func in allowed:
                if func in query.lower():
                    break
            else:
                raise toolkit.ValidationError(
                    f"Aucune fonction SQL autorisée utilisée. "
                    f"Fonctions permises : {', '.join(allowed)}"
                )

            return original_action(context, data_dict)

        def wrapped_package_update(context, data_dict):
            """Wrapper pour diagnostiquer les uploads lors de package_update (édition dataset)."""
            from ckan.logic.action.update import package_update as _original_package_update
            resources = data_dict.get('resources') or []
            orig = context.get('original_package')
            if not orig and data_dict.get('id'):
                try:
                    orig = toolkit.get_action('package_show')(
                        dict(context, ignore_auth=True, for_update=True),
                        {'id': data_dict['id']}
                    )
                except Exception:
                    pass
            if orig:
                oids = {r['id']: r for r in orig.get('resources', []) if r.get('id')}
                for res in resources:
                    rid = res.get('id')
                    if not rid:
                        continue
                    old = oids.get(rid)
                    if not old:
                        continue
                    has_upload = 'upload' in res
                    fmt_changed = (res.get('format') or '').upper() != (old.get('format') or '').upper()
                    if fmt_changed and old.get('url_type') == 'upload' and not has_upload:
                        log.warning(
                            f"[DLR] package_update: ressource {rid} a le format changé "
                            f"de {old.get('format')} -> {res.get('format')} mais aucun upload fourni. "
                            f"L'ancien fichier ne sera pas remplacé. Pour remplacer, sélectionnez un nouveau fichier "
                            f"AVANT de cliquer Sauvegarder (ne pas cliquer 'Supprimer' ou 'Effacer' après)."
                        )
                    elif has_upload and fmt_changed:
                        log.info(f"[DLR] package_update: ressource {rid} reçoit un nouveau fichier (format {old.get('format')} -> {res.get('format')})")
            return _original_package_update(context, data_dict)

        return {
            'datastore_search_sql': secure_sql_action,
            'package_update': wrapped_package_update,
        }

    def get_auth_functions(self):
        """Retourne les fonctions d'autorisation pour permettre resource_download avec ignore_auth"""
        # Récupérer la fonction d'autorisation originale depuis le registre CKAN
        # On doit le faire de manière lazy pour éviter les problèmes d'import circulaire
        original_resource_download = None
        
        def _get_original_auth():
            """Récupère la fonction d'autorisation originale de manière lazy"""
            nonlocal original_resource_download
            if original_resource_download is None:
                try:
                    # Essayer d'importer depuis ckan.logic.auth.get
                    from ckan.logic.auth.get import resource_download
                    original_resource_download = resource_download
                except (ImportError, AttributeError):
                    # Fallback: utiliser le registre d'autorisations
                    try:
                        from ckan.logic.auth import get_auth_function
                        original_resource_download = get_auth_function('resource_download')
                    except Exception:
                        # Dernier fallback: fonction qui autorise par défaut
                        def default_auth(context, data_dict):
                            return {'success': True}
                        original_resource_download = default_auth
            return original_resource_download
        
        def allow_internal_resource_download(context, data_dict):
            """
            Autorise resource_download pour les appels internes avec ignore_auth=True,
            les ressources publiques, ou pour les sysadmins.
            """
            resource_id = data_dict.get('id')
            
            # Log pour diagnostiquer si la fonction est appelée
            log.debug(f"[DLR] get_auth_functions.resource_download appelé pour ressource {resource_id}, ignore_auth={context.get('ignore_auth')}, user={context.get('user')}")
            
            # Si ignore_auth est True, autoriser (appel interne comme xloader)
            if context.get('ignore_auth'):
                log.debug(f"[DLR] resource_download autorisé: ignore_auth=True (appel interne) pour ressource {resource_id}")
                return {'success': True}
            
            # Si l'utilisateur est sysadmin, autoriser
            try:
                toolkit.check_access('sysadmin', context, data_dict)
                log.info(f"[DLR] resource_download autorisé: utilisateur sysadmin pour ressource {resource_id}")
                return {'success': True}
            except toolkit.NotAuthorized:
                pass
            
            # Utiliser l'autorisation par défaut de CKAN qui gère correctement les ressources publiques/privées
            # Mais d'abord, vérifier rapidement si la ressource est publique pour l'autoriser directement
            try:
                original_auth = _get_original_auth()
                result = original_auth(context, data_dict)
                
                # Si l'autorisation par défaut autorise, on accepte
                if result.get('success'):
                    log.debug(f"[DLR] resource_download autorisé par l'autorisation par défaut pour ressource {resource_id}")
                    return result
                
                # Si l'autorisation par défaut refuse, vérifier si c'est une ressource publique
                # Si oui, autoriser quand même (pour contourner d'éventuelles restrictions)
                log.debug(f"[DLR] resource_download refusé par défaut pour ressource {resource_id}, vérification si publique...")
                try:
                    resource = toolkit.get_action('resource_show')({'ignore_auth': True}, {'id': resource_id})
                    package_id = resource.get('package_id')
                    
                    if package_id:
                        dataset = toolkit.get_action('package_show')({'ignore_auth': True}, {'id': package_id})
                        is_private = dataset.get('private', False)
                        
                        if not is_private:
                            log.info(f"[DLR] resource_download autorisé: ressource publique (dataset {package_id}, ressource {resource_id}) malgré refus par défaut")
                            return {'success': True}
                except Exception as e:
                    log.debug(f"[DLR] Erreur lors de la vérification publique pour ressource {resource_id}: {e}")
                
                # Si privée ou erreur, retourner le résultat de l'autorisation par défaut
                log.warning(f"[DLR] resource_download refusé pour ressource {resource_id}: {result.get('msg', 'Unknown')}")
                return result
            except Exception as e:
                log.warning(f"[DLR] Erreur lors de l'appel à l'autorisation par défaut pour ressource {resource_id}: {e}")
                # En cas d'erreur, autoriser par défaut (fallback) pour éviter de bloquer
                log.info(f"[DLR] resource_download autorisé par fallback (erreur) pour ressource {resource_id}")
                return {'success': True}

        return {
            'resource_download': allow_internal_resource_download,
        }

    def after_resource_update(self, context, resource):
        log.info(f"[DLR] FUNCTION CALLED: after_resource_update: {resource}")
        # Ressource DÉRIVÉE d'une harmonisation (NGSI-LD / CSV / GeoJSON) : ne pas la
        # retraiter (ni conversion JSON->CSV, ni ré-harmonisation) sous peine de boucle.
        if resource.get("harmonized_from"):
            return
        if (resource.get("format") or "").lower() == "csv":
            try:
                create_geo_view_if_possible(resource["id"])
            except Exception as e:
                log.error(f"[DLR] Erreur création geo_view après update : {e}")
        # Ajout the_geom pour CSV upload : fait ici car le fichier est désormais sur disque
        # (dans before_resource_update le fichier n'était pas encore écrit, ce qui provoquait
        # une lecture de l'ancien fichier, une erreur et un rollback)
        if (resource.get("format") or "").lower() == "csv" and resource.get("url_type") == "upload":
            try:
                self._dedupe_csv_columns_if_needed(dict(context, ignore_auth=True), resource)
            except Exception as e:
                log.error(f"[DLR][dedup] échec après update : {e}")
            try:
                self._add_the_geom_column_if_possible(dict(context, ignore_auth=True), resource)
            except Exception as e:
                log.error(f"[DLR] Erreur _add_the_geom_column_if_possible après update : {e}")
        # classeur xlsx/xls déposé/mis à jour : éclatement en CSV par onglet (idempotent)
        try:
            self._expand_xlsx_to_csv(dict(context, ignore_auth=True), resource)
        except Exception as e:
            log.error(f"[DLR][xlsx] éclatement après update échec: {e}")
        # geojson/json uploadé : conversion en CSV via un JOB ASYNCHRONE. C'est ICI (after
        # update) que le fichier est réellement posé dans le flux de dépôt tus (le hook pose
        # le fichier PUIS patch url_type=upload) : d'où la fiabilité. Job idempotent.
        if (resource.get('format') or '').lower() in ['json', 'geojson'] and resource.get('url_type') == 'upload':
            try:
                if not resource.get('xloader_skip'):
                    toolkit.get_action('resource_patch')(dict(context, ignore_auth=True), {
                        'id': resource['id'], 'xloader_skip': True, 'datastore_active': False})
                toolkit.enqueue_job(dlr_geojson_job, [resource['id']], title=f"dlr geojson {resource['id']}")
                log.info(f"[DLR] conversion geojson enfilée (job async, after update) pour {resource['id']}")
            except Exception as e:
                log.error(f"[DLR][geojson] enqueue après update échec: {e}")
        # harmonisation DOLFIN : gérée par le plugin dédié ckanext-dolfin (son propre hook).
        if resource.get("datastore_active") is True:
            self.inject_records_count(context, resource)
    
    

    def before_resource_create(self, context, resource):
        log.info(f"[DLR] FUNCTION CALLED: before_resource_create: {resource}")
        
        # Définir resource_type='file' si c'est un upload et que resource_type n'est pas défini
        # Cela garantit que XLoader traitera correctement les ressources uploadées
        # Détection d'upload : 
        # - 'upload' présent dans le dict (objet FileStorage pour API ou UI)
        # - url_type='upload' (défini par CKAN après traitement)
        url_type = resource.get('url_type', '')
        has_upload_param = 'upload' in resource  # Présent pour API et UI
        has_upload_url_type = url_type == 'upload'  # Défini par CKAN
        has_upload = has_upload_param or has_upload_url_type
        
        if has_upload and not resource.get('resource_type'):
            resource['resource_type'] = 'file'
            log.info(f"[DLR] resource_type='file' défini automatiquement pour ressource uploadée (upload param: {has_upload_param}, url_type: {url_type})")
        
        # Détecter le format AVANT de définir xloader_skip
        # Gérer le cas où format, mimetype ou url peuvent être None
        format_ = (resource.get('format') or '').lower()
        mimetype = (resource.get('mimetype') or '').lower()
        url = resource.get('url') or ''
        name = resource.get('name') or ''
        
        # Détecter le format depuis le mimetype, l'URL ou le nom de fichier si non défini
        if not format_ or format_ == '':
            # Détecter CSV en premier (pour permettre le chargement dans le datastore)
            if 'csv' in mimetype or (url and url.lower().endswith(('.csv', '.csv.csv'))) or (name and name.lower().endswith('.csv')):
                resource['format'] = 'CSV'
                format_ = 'csv'
                log.debug(f"[DLR] Format 'CSV' détecté automatiquement depuis mimetype/URL/nom: mimetype={mimetype}, url={url}, name={name}")
            # Détecter les autres formats géospatiaux
            elif 'zip' in mimetype or (url and url.lower().endswith('.zip')):
                # Vérifier si c'est probablement un shapefile ZIP
                # (on ne peut pas vérifier le contenu ici, donc on met ZIP par défaut)
                resource['format'] = 'ZIP'
                format_ = 'zip'
                log.info(f"[DLR] Format 'ZIP' défini automatiquement pour ressource uploadée")
            elif url and url.lower().endswith('.shp'):
                resource['format'] = 'SHP'
                format_ = 'shp'
                log.info(f"[DLR] Format 'SHP' défini automatiquement pour ressource uploadée")
            elif url and url.lower().endswith('.geojson'):
                resource['format'] = 'GeoJSON'
                format_ = 'geojson'
                log.info(f"[DLR] Format 'GeoJSON' défini automatiquement pour ressource uploadée")
        
        # Ne pas marquer les ressources CSV comme xloader_skip
        # Les formats géospatiaux (ZIP, SHP, GeoJSON) ne doivent pas être traités par XLoader
        # Utiliser le format détecté (format_) pour la vérification
        if format_ not in ['csv']:
            resource.setdefault('xloader_skip', True)
            resource.setdefault('datastore_active', False)
            resource['xloader_skip'] = True
            resource['dlr_needs_patch'] = True  # flag custom pour le patch
        else:
            # Pour les CSV, définir xloader_skip=False uniquement pour les uploads
            # Pour les URLs externes, définir xloader_skip=True car le xloader ne peut pas les charger
            # Ne pas écraser si xloader_skip est déjà défini explicitement par l'utilisateur
            if 'xloader_skip' not in resource:
                # Seulement pour les uploads (url_type='upload' ou présence du paramètre 'upload')
                if has_upload or url_type == 'upload':
                    resource['xloader_skip'] = False
                    log.debug(f"[DLR] Format CSV détecté pour upload, xloader_skip=False pour permettre le chargement dans le datastore")
                else:
                    # Pour les URLs externes, définir xloader_skip=True car le xloader ne peut pas les charger depuis le conteneur
                    resource['xloader_skip'] = True
                    log.debug(f"[DLR] Format CSV détecté pour URL externe, xloader_skip=True (xloader ne peut pas charger les URLs externes)")
        
        return resource

    def after_resource_create(self, context, resource):
        log.info(f"[DLR] FUNCTION CALLED: after_resource_create: {resource}")
        # Ressource DÉRIVÉE d'une harmonisation : ne pas la retraiter (évite la
        # conversion JSON->CSV de la sortie GeoJSON et toute ré-harmonisation en boucle).
        if resource.get("harmonized_from"):
            return
        log.info(f"[DLR] Resource details: {json.dumps(resource, indent=2)}")

        # Créer un contexte avec ignore_auth pour éviter les problèmes de permissions
        # tout en préservant l'utilisateur original pour l'audit
        admin_context = dict(context)
        admin_context['ignore_auth'] = True
        
        # IMPORTANT: S'assurer que xloader_skip est défini IMMÉDIATEMENT pour les ressources JSON
        # avant que xloader ne soit déclenché (xloader peut être appelé avant ce hook)
        if (resource.get('format') or '').lower() in ['json', 'geojson']:
            try:
                toolkit.get_action('resource_patch')(admin_context, {
                    'id': resource['id'],
                    'xloader_skip': True,
                    'datastore_active': False
                })
                log.info(f"[DLR] xloader_skip défini pour la ressource JSON {resource['id']}")
            except Exception as e:
                log.error(f"[DLR] Erreur lors de la définition de xloader_skip: {e}")
        
        # CSV uploadé : normaliser les colonnes en double avant xloader (idempotent)
        if (resource.get('format') or '').lower() == 'csv' and resource.get('url_type') == 'upload':
            try:
                self._dedupe_csv_columns_if_needed(admin_context, resource)
            except Exception as e:
                log.error(f"[DLR][dedup] échec après create : {e}")
        self._add_the_geom_column_if_possible(admin_context, resource)
        # classeur xlsx/xls : éclater en une ressource CSV par onglet (aperçu, API, carte)
        try:
            self._expand_xlsx_to_csv(admin_context, resource)
        except Exception as e:
            log.error(f"[DLR][xlsx] éclatement après create échec: {e}")
        # harmonisation DOLFIN : gérée par le plugin dédié ckanext-dolfin (son propre hook).

        try:
            # Récupérer le package pour vérifier son type via les extras
            package = toolkit.get_action('package_show')(admin_context, {'id': resource['package_id']})
            
            # Vérifier le type via les extras : clé Dataizen, avec repli sur
            # l'ancienne clé databfc-type pour les jeux déjà en base.
            package_type = None
            for extra in package.get('extras', []):
                if extra['key'] in ('dataizen-type', 'databfc-type'):
                    package_type = extra['value']
                    break
            
            log.info(f"[DLR] Package type from extras: {package_type}")
            
            # Gestion des ressources JSON/GeoJSON (logique existante)
            if (resource.get('format') or '').lower() in ['json', 'geojson']:
                try:
                    # xloader ne sait pas lire un geojson : on le marque skip, la conversion
                    # (ogr2ogr) et le chargement se font dans un JOB ASYNCHRONE (worker), hors
                    # requête web. Le job re-lit la ressource, donc il tourne quand le fichier
                    # est réellement posé (cas du dépôt tus : fichier posé après le create).
                    toolkit.get_action('resource_patch')(admin_context, {
                        'id': resource['id'], 'xloader_skip': True, 'datastore_active': False})
                    toolkit.enqueue_job(dlr_geojson_job, [resource['id']],
                                        title=f"dlr geojson {resource['id']}")
                    log.info(f"[DLR] conversion geojson enfilée (job async) pour {resource['id']}")
                except Exception as e:
                    log.error(f"[DLR] Error enqueue JSON/GeoJSON processing: {e}")
            
            # Gestion des ressources CSV avec URL externe
            # Télécharger le CSV et créer une ressource uploadée pour permettre le chargement dans le datastore
            if (resource.get('format') or '').lower() == 'csv' and resource.get('xloader_skip'):
                # Vérifier que c'est bien une URL externe (pas un upload)
                url = resource.get('url', '')
                url_type = resource.get('url_type', '')
                # C'est une URL externe si : url_type n'est pas 'upload' ET url existe ET url est externe
                is_external_url = (url_type != 'upload' and url and 
                                  not url.startswith('/') and 
                                  'localhost' not in url.lower() and
                                  ('http://' in url.lower() or 'https://' in url.lower()))
                
                if is_external_url:
                    try:
                        log.debug(f"[DLR] CSV avec URL externe détecté, création d'une nouvelle ressource uploadée dérivée")
                        derived_res_id = _process_external_csv_resource(resource)
                        log.info(f"[DLR] Nouvelle ressource uploadée créée: {derived_res_id} (ressource originale externe conservée: {resource.get('id')})")
                    except Exception as e:
                        log.error(f"[DLR] Error during external CSV processing: {e}")
                        import traceback
                        log.error(f"[DLR] Full error details: {traceback.format_exc()}")
            
            # Gestion des datasets de type API
            if package_type == 'api':
                log.info(f"[DLR] Processing API dataset: {package['name']}")
                
                # Récupérer le modèle API depuis les extras
                api_model = None
                for extra in package.get('extras', []):
                    if extra['key'] == 'dataset-model':
                        try:
                            api_model = json.loads(extra['value'])
                            log.info(f"[DLR] Found API model: {json.dumps(api_model, indent=2)}")
                            break
                        except json.JSONDecodeError as e:
                            log.error(f"[DLR] Error parsing API model: {e}")
                            return
                
                if not api_model:
                    log.error("[DLR] No API model found in dataset extras")
                    return
                if (resource.get('format') or '').lower() == 'csv':
                    log.debug("[DLR] Ressource CSV détectée : pas de transformation en ressource API.")
                else:    
                    # Transformer la ressource en ressource API
                    try:
                        # Garder les métadonnées existantes mais ajouter les propriétés API
                        patch_data = {
                            'id': resource['id'],
                        # 'url': api_model['item'],
                        # 'format': api_model['format'].upper(),
                            'resource_type': 'api',
                            'xloader_skip': True,
                            'datastore_active': False
                        }
                        
                        # Ne pas écraser le nom et la description s'ils existent déjà
                        if not resource.get('name'):
                            patch_data['name'] = f"API {package['name']}"
                        if not resource.get('description'):
                            patch_data['description'] = f"API resource for {package['name']}"
                        
                        toolkit.get_action('resource_patch')(admin_context, patch_data)
                        log.info(f"[DLR] Resource transformed to API resource: {resource['id']}")
                        
                        # Si c'est JSON/GeoJSON, on crée aussi une ressource CSV
                        if api_model['format'].lower() in ['json', 'geojson']:
                            try:
                                derived_res_id = _process_json_resource(resource)
                                log.info(f"[DLR] Created derived CSV resource from API: {derived_res_id}")
                            except Exception as e:
                                log.error(f"[DLR] Error creating derived CSV resource from API: {e}")
                        
                    except Exception as e:
                        log.error(f"[DLR] Error transforming resource to API: {e}")
                        log.error(f"[DLR] Full error details: {traceback.format_exc()}")
                
        except Exception as e:
            log.error(f"[DLR] Error in after_resource_create: {e}")
            log.error(f"[DLR] Full error details: {traceback.format_exc()}")


    def before_resource_update(self, context, current, resource):
        # Diagnostic: vérifier si un nouveau fichier est fourni lors du remplacement
        has_upload = 'upload' in resource
        upload_type = type(resource.get('upload')).__name__ if resource.get('upload') is not None else 'none'
        log.info(
            f"[DLR] before_resource_update: resource_id={resource.get('id')} "
            f"format={resource.get('format')} url_type={resource.get('url_type')} "
            f"has_upload={has_upload} upload_type={upload_type} "
            f"current_format={current.get('format')} current_url={str(current.get('url', ''))[:60]}"
        )
        if not has_upload and current.get('url_type') == 'upload' and resource.get('format', '').upper() != current.get('format', '').upper():
            log.warning(
                f"[DLR] REMPLACEMENT FORMAT SANS UPLOAD: la ressource {resource.get('id')} "
                f"a son format changé de {current.get('format')} -> {resource.get('format')} "
                f"mais aucun nouveau fichier (upload) n'est fourni. L'ancien fichier ne sera pas remplacé."
            )
        self._handle_resource(context, resource)
        if resource.get("datastore_active") and resource.get("format") == "CSV":
            try:
                create_geo_view_if_possible(resource["id"])
            except Exception as e:
                log.error(f"[DLR] Erreur création geo_view : {e}")
        # Ne jamais appeler _add_the_geom_column_if_possible dans before : le fichier n'est pas
        # encore sur disque lors d'un nouvel upload (écrit plus tard par CKAN), ce qui provoquerait
        # une lecture de l'ancien fichier, une erreur et un rollback. Tout est fait dans after.
        if not context.get('_dataload_router_skip_geom', False):
            log.debug(f"[DLR] Skip _add_the_geom_column_if_possible (sera fait dans after_resource_update)")
        else:
            log.debug(f"[DLR] Skip _add_the_geom_column_if_possible pour éviter la récursion (resource {resource.get('id')})")
        return resource

    def _handle_resource(self, context, resource_dict):
        log.info(f"[DLR] FUNCTION CALLED: _handle_resource: {resource_dict}")
        format_ = (resource_dict.get('format') or '').lower()
        if format_ in ['json', 'geojson']:
            # Ne pas laisser XLoader traiter ce fichier brut
            resource_dict['xloader_skip'] = True

            try:
                derived_res_id = _process_json_resource(resource_dict)
                log.info(f"[DLR] CSV resource created from JSON: {derived_res_id}")
            except Exception as e:
                log.error(f"[DLR] Failed to process JSON/GeoJSON: {e}")
    @staticmethod
    def inject_records_count(context, resource):
        if not resource.get("datastore_active"):
            return

        try:
            context = {"ignore_auth": True}
            res = toolkit.get_action("datastore_search")(context, {
                "resource_id": resource["id"],
                "limit": 0
            })
            count = res["total"]

            log.info(f"[DLR] Injecting records_count={count} for dataset {resource['package_id']}")

            # Récupérer le package actuel
            package = toolkit.get_action("package_show")(context, {
                "id": resource["package_id"]
            })

            # Transformer la liste d'extras en dict
            extras = {e['key']: e['value'] for e in package.get("extras", [])}

            # Ajouter ou mettre à jour records_count
            extras["records_count"] = str(count).zfill(10)

            # Reconstruire la liste d'extras
            updated_extras = [{"key": k, "value": v} for k, v in extras.items()]

            toolkit.get_action("package_patch")(context, {
                "id": resource["package_id"],
                "extras": updated_extras
            })

        except Exception as e:
            log.error(f"[DLR] Failed to inject records_count: {e}")



def _process_external_csv_resource(resource_dict):
    """Télécharge un CSV depuis une URL externe et crée une nouvelle ressource uploadée dérivée.
    
    L'originale externe reste intacte (avec xloader_skip=True), et une nouvelle ressource
    uploadée est créée pour permettre le chargement dans le datastore.
    """
    log.info(f"[DLR] FUNCTION CALLED: _process_external_csv_resource: {resource_dict}")
    original_url = resource_dict['url']
    original_resource_id = resource_dict['id']
    package_id = resource_dict['package_id']
    original_name = resource_dict.get('name', 'file.csv')
    original_description = resource_dict.get('description', '')
    
    # 1. Télécharger le CSV depuis l'URL externe
    # Corriger l'URL pour accès depuis le container (localhost:8080 -> localhost:5000)
    download_url = original_url.replace('localhost:8080', 'localhost:5000')
    log.info(f"[DLR] Téléchargement CSV depuis : {download_url} (original: {original_url})")
    
    try:
        response = requests.get(download_url, timeout=60)
        response.raise_for_status()
    except Exception as e:
        log.error(f"[DLR] Erreur lors du téléchargement du CSV depuis {download_url}: {e}")
        raise
    
    # 2. Sauvegarder le CSV dans un fichier temporaire
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp_csv:
        tmp_csv.write(response.content)
        tmp_csv_path = tmp_csv.name
    
    try:
        context = {'ignore_auth': True}
        
        # 3. Créer une nouvelle ressource uploadée dérivée (au lieu de modifier l'originale)
        # Cela évite les problèmes d'incohérence de statut et garde l'URL externe originale intacte
        log.info(f"[DLR] Création d'une nouvelle ressource uploadée dérivée pour le package {package_id}")
        
        # Générer un nom unique pour la nouvelle ressource
        # Format: {original_name}_uploaded ou {original_name}_uploaded_{timestamp}
        new_name = f"{original_name}_uploaded"
        
        # Vérifier si ce nom existe déjà dans le package
        existing_resources = toolkit.get_action('package_show')(context, {'id': package_id})['resources']
        name_exists = any(r['name'] == new_name for r in existing_resources)
        if name_exists:
            import time
            new_name = f"{original_name}_uploaded_{int(time.time())}"
            log.info(f"[DLR] Nom '{original_name}_uploaded' existe déjà, utilisation de '{new_name}'")
        
        # Créer la nouvelle ressource avec le fichier uploadé
        with open(tmp_csv_path, 'rb') as f:
            fs = FileStorage(
                stream=f,
                filename=os.path.basename(tmp_csv_path) or original_name,
                content_type='text/csv'
            )
            new_resource = toolkit.get_action('resource_create')(context, {
                'package_id': package_id,
                'upload': fs,  # FileStorage : CKAN va copier le fichier dans le filestore
                'name': new_name,
                'format': 'CSV',
                'mimetype': 'text/csv',
                'resource_type': 'file',
                'description': f'Ressource uploadée dérivée de : {original_url}' + (f' ({original_description})' if original_description else ''),
                'url_type': 'upload',  # Défini explicitement
                'xloader_skip': False  # Activer le xloader pour cette nouvelle ressource
            })
        
        new_resource_id = new_resource['id']
        log.info(f"[DLR] Nouvelle ressource uploadée créée: id={new_resource_id}, name={new_name}")
        log.info(f"[DLR] Ressource originale externe conservée: id={original_resource_id}, url={original_url}, xloader_skip=True")
        
        # 4. Vérifier que la nouvelle ressource est bien configurée
        final_resource = toolkit.get_action('resource_show')(context, {'id': new_resource_id})
        log.info(f"[DLR] État de la nouvelle ressource: url_type={final_resource.get('url_type')}, "
                 f"xloader_skip={final_resource.get('xloader_skip')}, "
                 f"datastore_active={final_resource.get('datastore_active')}")
        
        # Le xloader est déjà soumis automatiquement lors de la création de la ressource avec xloader_skip=False
        # Attendre un peu pour que le xloader démarre le traitement
        import time
        log.debug(f"[DLR] Attente de 2 secondes pour laisser le xloader démarrer le traitement")
        time.sleep(2)
        
        return new_resource_id
    
    finally:
        # Nettoyer le fichier temporaire
        if os.path.exists(tmp_csv_path):
            os.remove(tmp_csv_path)


def _ogr_to_csv(src_path):
    """Convertit un GeoJSON/JSON en CSV via ogr2ogr (GDAL), en STREAMING sur disque
    (mémoire bornée, pas de chargement du fichier en RAM). La géométrie est exportée en
    WKT (colonne « WKT »), chargeable ensuite en datastore. Utilisé pour les GROS fichiers,
    où la conversion en mémoire ferait un OOM (incident GeoJSON 5,5 Go du 09/09/2026)."""
    fd, out_path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    os.remove(out_path)  # ogr2ogr crée le fichier lui-même
    subprocess.run(
        ['ogr2ogr', '-f', 'CSV', '-lco', 'GEOMETRY=AS_WKT', '-lco', 'SEPARATOR=COMMA',
         '-lco', 'STRING_QUOTING=IF_NEEDED', out_path, src_path],
        check=True, capture_output=True, timeout=7200)
    return out_path


def dlr_geojson_job(resource_id):
    """Job ASYNCHRONE (RQ, worker CKAN) : convertit un GeoJSON/JSON déposé en CSV (ogr2ogr
    en streaming pour les gros) puis charge le datastore. Hors requête web : ne bloque pas
    le dépôt, et re-lit la ressource -> tourne quand le fichier est réellement posé (fiabilité,
    contrairement à un appel synchrone dans after_resource_create). Idempotent."""
    ctx = {'ignore_auth': True}
    try:
        try:
            res = toolkit.get_action('resource_show')(ctx, {'id': resource_id})
        except Exception as e:
            log.warning(f"[DLR] job geojson: ressource {resource_id} introuvable: {e}")
            return
        if (res.get('format') or '').lower() not in ('json', 'geojson'):
            return
        if res.get('url_type') != 'upload':
            log.info(f"[DLR] job geojson: fichier pas encore posé pour {resource_id}, on saute")
            return
        # Idempotence : ne pas recréer un CSV dérivé s'il en existe déjà un pour ce jeu.
        try:
            pkg = toolkit.get_action('package_show')(ctx, {'id': res['package_id']})
            if any((r.get('name') or '').startswith('converted_from_json') and r.get('url_type') == 'upload'
                   for r in pkg.get('resources', [])):
                log.info(f"[DLR] job geojson: CSV dérivé déjà présent, skip {resource_id}")
                return
        except Exception:
            pass
        log.info(f"[DLR] job geojson: conversion + chargement de {resource_id}")
        _process_json_resource(res)
        _reindex_package(res.get('package_id'))  # fraîcheur Solr : la fiche voit le CSV dérivé
    finally:
        # Hygiène session : ce job tourne dans le worker RQ (hors requête web). Sans ce
        # remove(), les package_show/search_index_rebuild ci-dessus laissent une connexion
        # « idle in transaction » (fuite qui peut bloquer une purge et épuiser le pool).
        try:
            model.Session.remove()
        except Exception:
            pass


def _reindex_package(package_id):
    """Réindexe un jeu dans Solr : `package_show` sert la version indexée, donc sans
    réindexation une ressource dérivée (CSV issu d'un geojson) ou un datastore fraîchement
    chargé n'apparaît sur la fiche qu'au prochain update. Best-effort."""
    try:
        toolkit.get_action('search_index_rebuild')({'ignore_auth': True}, {'id': package_id})
    except Exception as e:
        log.warning(f"[DLR] réindexation Solr échouée pour {package_id} : {e}")


# NB : the_geom (pandas) et l'éclatement xlsx ne sont PAS désynchronisés en jobs. Leur
# anti-récursion repose sur un flag de CONTEXTE (_dataload_router_skip_geom/_skip_dedup) posé
# lors du re-upload qu'ils déclenchent ; ce flag ne survit pas à une frontière de job, donc
# une version asynchrone re-enfilerait un job à chaque re-upload (boucle). Ils restent donc
# synchrones ET bornés par le garde-fou mémoire (gros fichiers : sautés). Les désynchroniser
# proprement demande de rendre l'anti-récursion persistante (marqueur en base sur la ressource),
# cf. backlog.


def _process_json_resource(resource_dict):
    """Convertit et soumet un JSON/GeoJSON comme CSV à XLoader. Gros fichiers : conversion
    STREAMING (ogr2ogr) et lecture directe du filestore, jamais de chargement en mémoire."""
    log.info(f"[DLR] FUNCTION CALLED: _process_json_resource: {resource_dict}")
    if (resource_dict.get('format') or '').upper() == 'CSV':
        log.debug('[DLR] Resource is already CSV format, skipping conversion')
        return resource_dict
    original_url = resource_dict['url']
    package_id = resource_dict['package_id']
    url_type = resource_dict.get('url_type', '')

    # 1. Chemin source : fichier local du filestore pour un upload (évite un 403 sur
    # dataset privé ET surtout évite de le charger en mémoire), sinon téléchargement
    # en streaming vers un temporaire.
    src_path = None
    tmp_download = None
    if url_type == 'upload':
        try:
            from ckan.lib.uploader import get_resource_uploader
            local_path = get_resource_uploader(resource_dict).get_path(resource_dict['id'])
            if local_path and os.path.exists(local_path):
                src_path = local_path
            else:
                log.warning(f"[DLR] Fichier upload introuvable: {local_path}, fallback GET")
        except Exception as e:
            log.warning(f"[DLR] Impossible de localiser le fichier upload: {e}, fallback GET")
    if src_path is None:
        download_url = original_url.replace('localhost:8080', 'localhost:5000')
        log.info(f"[DLR] Téléchargement (streaming) depuis : {download_url}")
        with requests.get(download_url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='wb') as tmp_json:
                for chunk in response.iter_content(8 * 1024 * 1024):
                    tmp_json.write(chunk)
                src_path = tmp_json.name
                tmp_download = src_path

    csv_path = None
    try:
        # 2. Conversion en CSV. Au-delà du seuil : ogr2ogr STREAMING (mémoire bornée) pour
        # les gros GeoJSON sans OOM. En dessous : conversion existante (schéma inchangé).
        if os.path.getsize(src_path) > DATALOAD_MAX_SYNC_BYTES:
            log.info(f"[DLR] Conversion streaming ogr2ogr ({os.path.getsize(src_path)} o) : {resource_dict.get('id')}")
            csv_path = _ogr_to_csv(src_path)
        else:
            csv_path = _convert_json_to_csv(src_path)

        # 3. Ressource CSV dérivée + activation XLoader (chargement datastore en streaming)
        uploaded = _create_uploaded_resource(package_id, csv_path, name='converted_from_json.csv',
                                             description=f'Ressource dérivée du JSON original : {original_url}')
        context = {'ignore_auth': True}
        toolkit.get_action('resource_patch')(context, {
            'id': uploaded['id'], 'format': 'CSV', 'mimetype': 'text/csv',
            'resource_type': 'file', 'xloader_skip': False})
        toolkit.get_action('xloader_submit')(context, {'resource_id': uploaded['id']})
        return uploaded['id']
    finally:
        # Ne supprime QUE le temporaire de téléchargement, JAMAIS le fichier du filestore.
        if tmp_download and os.path.exists(tmp_download):
            os.remove(tmp_download)
        if csv_path and os.path.exists(csv_path):
            os.remove(csv_path)


def _convert_json_to_csv(json_file_path):
    """Convertit un fichier JSON/GeoJSON/EsriJSON en CSV et retourne le chemin du fichier CSV temporaire."""
    log.info(f"[DLR] FUNCTION CALLED: _convert_json_to_csv: {json_file_path}")
    #with open(json_file_path, 'r', encoding='utf-8') as f:
     #   data = json.load(f)
    with open(json_file_path, 'rb') as f:
        content = f.read()

    try:
        data = json.loads(content.decode('utf-8'))
    except json.JSONDecodeError as e:
        log.error(f"[DLR] JSON parsing error: {e}")
        log.debug(f"[DLR] Raw content start:\n{content[:500]}")
        raise

    records = []
    
    # Détection du type de JSON
    if isinstance(data, dict):
        if 'features' in data:
            # ArcGIS ou GeoJSON
            if 'features' in data and 'geometryType' in data:
                # Format ArcGIS
                log.debug("[DLR] Format ArcGIS détecté")
                fields = {f['name']: f.get('type') for f in data.get('fields', [])} if 'fields' in data else {}
                
                for feat in data['features']:
                    record = {}
                    # Gestion des attributs
                    attrs = feat.get('attributes', {})
                    for key, value in attrs.items():
                        # Conversion des types ArcGIS en types Python
                        if fields.get(key) == 'esriFieldTypeDate':
                            try:
                                value = pd.to_datetime(value, unit='ms').isoformat()
                            except (ValueError, TypeError):
                                pass
                        record[key] = value
                    
                    # Gestion de la géométrie
                    geometry = feat.get('geometry', {})
                    if geometry:
                        # Conversion en GeoJSON et échappement correct pour CSV
                        geom_type = data.get('geometryType', '').replace('esriGeometry', '')
                        try:
                            if geom_type == 'Point':
                                x, y = geometry.get('x', 0), geometry.get('y', 0)
                                geom_json = {
                                    'type': 'Point',
                                    'coordinates': [x, y]
                                }
                            elif geom_type == 'Polyline':
                                paths = geometry.get('paths', [])
                                if paths:
                                    geom_json = {
                                        'type': 'LineString',
                                        'coordinates': paths[0]
                                    }
                            elif geom_type == 'Polygon':
                                rings = geometry.get('rings', [])
                                if rings:
                                    geom_json = {
                                        'type': 'Polygon',
                                        'coordinates': rings
                                    }
                            elif geom_type == 'Multipoint':
                                points = geometry.get('points', [])
                                if points:
                                    geom_json = {
                                        'type': 'MultiPoint',
                                        'coordinates': points
                                    }
                            else:
                                geom_json = None
                                
                            if geom_json:
                                # Store geometry directly as JSON string without extra escaping
                                record['geom'] = json.dumps(geom_json)
                        except Exception as e:
                            log.error(f"[DLR] Error processing geometry: {e}")
                            record['geom'] = None
                    
                    records.append(record)
            else:
                # GeoJSON standard
                log.debug("[DLR] Format GeoJSON standard détecté")
                for feat in data['features']:
                    props = feat.get('properties', {})
                    geometry = feat.get('geometry', {})
                    record = {**props}
                    if geometry:
                        # Store geometry directly as JSON string without extra escaping
                        record['geom'] = json.dumps(geometry)
                    records.append(record)
        else:
            # JSON simple
            records = [data]
    elif isinstance(data, list):
        # Liste de features ou d'objets
        if data and isinstance(data[0], dict) and 'attributes' in data[0]:
            # Liste de features ArcGIS
            log.debug("[DLR] Liste de features ArcGIS détectée")
            for feat in data:
                record = feat.get('attributes', {})
                geometry = feat.get('geometry', {})
                if geometry:
                    # Store geometry directly as JSON string without extra escaping
                    record['geom'] = json.dumps(geometry)
                records.append(record)
        else:
            # Liste d'objets simples
            records = data
    else:
        raise ValueError("Structure JSON non reconnue")

    # Conversion en DataFrame
    df = pd.json_normalize(records)
    
    # Nettoyage des colonnes
    df.dropna(axis=1, how='all', inplace=True)
    
    # Vérification des données
    if df.empty or df.columns.empty:
        raise ValueError("CSV converti est vide ou sans colonnes valides")
    
    # Logging des informations de debug
    log.info(f"[DLR] DataFrame créé avec {len(df)} lignes")
    log.info(f"[DLR] Colonnes: {df.columns.tolist()}")
    log.info(f"[DLR] Types des colonnes: {df.dtypes.to_dict()}")
    
    # Before writing CSV, validate the data
    if df.empty:
        raise ValueError("DataFrame is empty")
    
    # Ensure all columns are strings to avoid any type issues
    for col in df.columns:
        df[col] = df[col].astype(str)

    # Log the first few rows for debugging
    log.info(f"[DLR] First few rows of DataFrame:\n{df.head().to_string()}")
    
    # Création du fichier CSV avec pandas
    tmp_csv = tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='w', encoding='utf-8', newline='')
    try:
        # Write CSV using pandas with proper settings
        df.to_csv(
            tmp_csv.name,
            index=False,
            quoting=csv.QUOTE_ALL,  # Quote all fields to ensure proper escaping
            escapechar='\\',
            doublequote=True,
            encoding='utf-8',
            lineterminator='\n',
            sep=',',  # Explicitly set separator
            na_rep=''  # Replace NaN with empty string
        )
    finally:
        tmp_csv.close()
    
    return tmp_csv.name



def _create_uploaded_resource(package_id, csv_path, name, description='', replace_external_url=False):
    """Crée une ressource CSV uploadée avec xloader_skip=True.
    
    Args:
        package_id: ID du package
        csv_path: Chemin vers le fichier CSV à uploader
        name: Nom de la ressource
        description: Description de la ressource
        replace_external_url: Si True, remplace la ressource existante si elle a une URL externe
    """
    context = {'model': model, 'session': model.Session, 'ignore_auth': True}
    log.info(f"[DLR] FUNCTION CALLED: _create_uploaded_resource: {csv_path}")

    # Vérification d'une ressource déjà existante avec le même nom
    existing_resources = toolkit.get_action('package_show')(context, {'id': package_id})['resources']
    for res in existing_resources:
        if res['name'] == name:
            # Si on doit remplacer une URL externe, créer une nouvelle ressource avec un nom différent
            # au lieu de mettre à jour l'existante, pour éviter les problèmes de tâches en attente "fantômes"
            if replace_external_url and res.get('url') and res.get('url_type') != 'upload':
                log.info(f"[DLR] Ressource '{name}' existe avec URL externe, création d'une nouvelle ressource uploadée")
                # Créer une nouvelle ressource avec un nom légèrement différent pour éviter les conflits
                new_name = f"{name}_uploaded"
                # Vérifier si ce nom existe déjà
                name_exists = any(r['name'] == new_name for r in existing_resources)
                if name_exists:
                    # Ajouter un timestamp pour garantir l'unicité
                    import time
                    new_name = f"{name}_uploaded_{int(time.time())}"
                
                with open(csv_path, 'rb') as f:
                    fs = FileStorage(
                        stream=f,
                        filename=os.path.basename(csv_path),
                        content_type='text/csv'
                    )
                    # Créer une nouvelle ressource avec xloader_skip=False
                    result = toolkit.get_action('resource_create')(context, {
                        'package_id': package_id,
                        'upload': fs,
                        'name': new_name,
                        'format': 'CSV',
                        'mimetype': 'text/csv',
                        'resource_type': 'file',
                        'description': description or f'Ressource uploadée dérivée de : {res.get("description", "")}',
                        'url_type': 'upload',
                        'xloader_skip': False  # Permettre le chargement dans le datastore
                    })
                log.info(f"[DLR] Nouvelle ressource créée avec fichier uploadé (xloader_skip=False): {result['id']}, nom={new_name}")
                
                # Marquer l'ancienne ressource comme xloader_skip=True pour éviter qu'elle ne soit traitée
                try:
                    toolkit.get_action('resource_patch')(context, {
                        'id': res['id'],
                        'xloader_skip': True,
                        'description': f"{res.get('description', '')} (remplacée par ressource uploadée)"
                    })
                except Exception as e:
                    log.warning(f"[DLR] Impossible de marquer l'ancienne ressource comme xloader_skip: {e}")
                
                return result
            else:
                log.info(f"[DLR] Une ressource '{name}' existe déjà dans le dataset {package_id}, retour de l'existante.")
                return res

    with open(csv_path, 'rb') as f:
        fs = FileStorage(
            stream=f,
            filename=os.path.basename(csv_path),
            content_type='text/csv'
        )

        result = toolkit.get_action('resource_create')(context, {
            'package_id': package_id,
            'upload': fs,
            'name': name,
            'format': 'CSV',
            'mimetype': 'text/csv',
            'resource_type': 'file',
            'description': description,
            'url_type': 'upload',
            'xloader_skip': True
        })

    log.info(f"[DLR] Uploaded CSV resource created: {result['id']}")
    return result


"""
Geometry utilities for ckanext-dataload-router.

Helpers to detect and add geometry columns (``the_geom``) on CKAN
datastore tables after CSV / GeoJSON ingestion.
"""
import logging
from ckan.plugins import toolkit
from ckan import model
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy import text

log = logging.getLogger(__name__)

def create_geo_view_if_possible(resource_id):
    """Ajoute une vue geo_view si les champs requis sont présents dans le DataStore."""
    log.debug(f"[DLR] Champ 'geom' détecté pour {resource_id}, tentative de création d'une vue geo_view")
    context = {'ignore_auth': True}
    resource = toolkit.get_action('resource_show')(context, {'id': resource_id})

    if not resource.get('datastore_active'):
        log.info(f"[DLR] Pas de DataStore actif pour {resource_id}")
        return

    fields = toolkit.get_action('datastore_search')(
        context, {'resource_id': resource_id, 'limit': 0})['fields']
    field_names = [f['id'] for f in fields]

    if 'geometry_type' in field_names and 'geometry_coordinates' in field_names:
        log.debug(f"[DLR] Champs géométriques trouvés pour {resource_id}, tentative de création du champ virtuel 'geom'")

        try:
            add_virtual_geom_field(resource_id, context)
        except Exception as e:
            log.error(f"[DLR] Erreur lors de l'ajout du champ virtuel geom : {e}")

        try:
            toolkit.get_action('resource_view_create')(context, {
                'resource_id': resource_id,
                'title': 'Carte',
                'view_type': 'geo_view'
            })
            log.info(f"[DLR] Vue geo_view créée pour {resource_id}")
        except Exception as e:
            log.error(f"[DLR] Erreur lors de la création de la vue geo_view : {e}")
    else:
        log.info(f"[DLR] Champs géométriques manquants pour {resource_id}, pas de vue créée")

def add_virtual_geom_field(resource_id, context):
    """Ajoute un champ virtuel geom WKT basé sur geometry_type et geometry_coordinates."""
    engine = model.meta.engine
    quoted_table = f'"public"."{resource_id}"'

    # Vérifie que la table existe avant de faire ALTER/UPDATE
    check_sql = f"SELECT to_regclass('{quoted_table}')"

    with engine.connect() as conn:
        table_exists = conn.execute(text(check_sql)).scalar()

    if not table_exists:
        log.warning(f"[DLR] Table {quoted_table} introuvable, pas d'ajout du champ geom.")
        return

    sql = f"""
    ALTER TABLE {quoted_table} ADD COLUMN IF NOT EXISTS geom text;
    UPDATE {quoted_table} SET geom = 
        'SRID=4326;' || 
        CASE 
            WHEN geometry_type = 'Point' THEN 'POINT(' || geometry_coordinates[0] || ' ' || geometry_coordinates[1] || ')'
            WHEN geometry_type = 'Polygon' THEN 
                'POLYGON((' || 
                array_to_string(
                    ARRAY(SELECT unnest(geometry_coordinates[0])::text[]), 
                    ','
                ) || '))'
            ELSE NULL
        END;
    """
    try:
        model.Session.execute(sql)
        model.Session.commit()
        log.info(f"[DLR] Champ virtuel geom ajouté pour {resource_id}")
    except (ProgrammingError, OperationalError) as e:
        model.Session.rollback()
        log.error(f"[DLR] Erreur SQL lors de l'ajout du champ geom pour {resource_id} : {e}")

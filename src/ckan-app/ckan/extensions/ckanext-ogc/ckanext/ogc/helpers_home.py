"""
Helpers for the OGC home page: statistics, popular tags and categories.

Registered through the plugin's ``get_helpers()`` and called from the
``home/*`` and ``snippets/home_*.html`` templates.
"""
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)


def get_home_statistics() -> Dict[str, int]:
    """Return statistics for the home page (optimized variant).

    Honours the current user's permissions, so private datasets are
    counted when the user is authorised.

    Returns:
        Mapping with the keys ``datasets``, ``organizations``,
        ``resources``, ``geo_datasets``, ``groups`` and ``tags``.
    """
    try:
        from ckan import model
        from ckan.plugins import toolkit

        # Utiliser le contexte de l'utilisateur actuel pour respecter les permissions
        try:
            context = {'user': toolkit.c.user, 'auth_user_obj': toolkit.c.userobj}
        except Exception:
            context = {}

        # Compter les datasets (une seule requête avec rows=0 pour juste le count)
        try:
            datasets_result = toolkit.get_action('package_search')(context, {
                'rows': 0,
                'fq': 'state:active'
            })
            datasets_count = datasets_result.get('count', 0)
        except Exception:
            datasets_count = 0

        # Compter les organisations (requête directe DB pour performance)
        try:
            organizations_count = model.Session.query(model.Group).filter(
                model.Group.type == 'organization',
                model.Group.state == 'active'
            ).count()
        except Exception:
            organizations_count = 0

        # Compter les ressources (requête directe DB)
        try:
            resources_count = model.Session.query(model.Resource).join(
                model.Package
            ).filter(
                model.Resource.state == 'active',
                model.Package.state == 'active'
            ).count()
        except Exception:
            try:
                resources_count = model.Session.query(model.Resource).filter(
                    model.Resource.state == 'active'
                ).count()
            except Exception:
                resources_count = 0

        # Compter les datasets géospatiaux (requête Solr optimisée)
        # Utiliser directement la recherche avec filtres sur les formats géo et extras spatial
        try:
            geo_result = toolkit.get_action('package_search')(context, {
                'rows': 0,
                'fq': 'state:active AND (extras_spatial:* OR extras_spatial_text:* OR extras_spatial_uri:* OR format:(SHP OR GeoJSON OR KML OR KMZ OR GPKG OR GeoTIFF OR "Shapefile" OR "GeoPackage"))'
            })
            geo_datasets_count = geo_result.get('count', 0)
        except Exception:
            geo_datasets_count = 0

        # Compter les groupes (requête directe DB)
        try:
            groups_count = model.Session.query(model.Group).filter(
                model.Group.type == 'group',
                model.Group.state == 'active'
            ).count()
        except Exception:
            groups_count = 0

        # Compter les tags uniques (requête directe DB)
        try:
            tags_count = model.Session.query(model.Tag).count()
        except Exception:
            tags_count = 0

        return {
            'datasets': datasets_count,
            'organizations': organizations_count,
            'resources': resources_count,
            'geo_datasets': geo_datasets_count,
            'groups': groups_count,
            'tags': tags_count
        }
    except Exception as e:
        log.error(f"Erreur lors de la récupération des statistiques: {e}")
        return {
            'datasets': 0,
            'organizations': 0,
            'resources': 0,
            'geo_datasets': 0,
            'groups': 0,
            'tags': 0
        }


def get_popular_tags(limit: int = 10) -> List[Dict[str, Any]]:
    """Return the most popular tags (optimized variant).

    Honours the current user's permissions for private datasets.

    Args:
        limit: Maximum number of tags to return (10 by default, kept low
            for performance).

    Returns:
        List of ``{"name": str, "count": int}`` entries sorted by count.
    """
    try:
        from ckan import model
        from ckan.plugins import toolkit

        # Utiliser le contexte de l'utilisateur actuel
        try:
            context = {'user': toolkit.c.user, 'auth_user_obj': toolkit.c.userobj}
        except Exception:
            context = {}

        # Utiliser une requête directe à la base pour les tags les plus utilisés
        try:
            # Compter les tags par nombre d'occurrences dans les packages actifs
            from sqlalchemy import func
            tags_query = model.Session.query(
                model.Tag.name,
                func.count(model.PackageTag.package_id).label('count')
            ).join(
                model.PackageTag
            ).join(
                model.Package
            ).filter(
                model.Package.state == 'active'
            ).group_by(
                model.Tag.name
            ).order_by(
                func.count(model.PackageTag.package_id).desc()
            ).limit(limit * 2).all()  # Récupérer 2x pour avoir un buffer

            # Formater les résultats
            tag_counts = {}
            for tag_name, count in tags_query:
                if tag_name and count > 0:
                    tag_counts[tag_name] = count

            # Trier et limiter
            sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
            return [{'name': name, 'count': count} for name, count in sorted_tags]
        except Exception as e:
            log.debug(f"Erreur requête SQL tags, fallback: {e}")
            # Fallback: utiliser tag_list mais limiter drastiquement
            try:
                tags_result = toolkit.get_action('tag_list')(context, {
                    'all_fields': False,
                    'limit': limit * 2  # Limiter à 2x le nombre demandé
                })

                if not isinstance(tags_result, list):
                    return []

                # Pour chaque tag, faire une seule requête (limité)
                tag_counts = {}
                for tag in tags_result[:limit * 2]:
                    tag_name = tag.get('name', '') if isinstance(tag, dict) else str(tag)
                    if tag_name:
                        try:
                            search_result = toolkit.get_action('package_search')(context, {
                                'rows': 0,
                                'fq': f'state:active AND tags:"{tag_name}"'
                            })
                            count = search_result.get('count', 0)
                            if count > 0:
                                tag_counts[tag_name] = count
                        except Exception:
                            continue

                sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
                return [{'name': name, 'count': count} for name, count in sorted_tags]
            except Exception:
                return []
    except Exception as e:
        log.error(f"Erreur lors de la récupération des tags populaires: {e}")
        return []


def get_categories() -> List[Dict[str, Any]]:
    """Return the categories (groups) shown on the home page (optimized).

    Honours the current user's permissions for private datasets. The
    result is capped to 8 entries for performance.
    """
    try:
        from ckan.plugins import toolkit

        # Utiliser le contexte de l'utilisateur actuel
        try:
            context = {'user': toolkit.c.user, 'auth_user_obj': toolkit.c.userobj}
        except Exception:
            context = {}

        # Récupérer seulement les groupes les plus populaires (limité à 8)
        groups_result = toolkit.get_action('group_list')(context, {
            'all_fields': True,
            'limit': 8,  # Réduit de 100 à 8 pour performance
            'sort': 'package_count desc'
        })

        if not isinstance(groups_result, list):
            return []

        # Formater les groupes pour l'affichage
        categories = []
        for group in groups_result:
            if isinstance(group, dict):
                categories.append({
                    'name': group.get('name', ''),
                    'title': group.get('title', group.get('name', '')),
                    'description': group.get('description', ''),
                    'package_count': group.get('package_count', 0),
                    'image_url': group.get('image_display_url', '') or group.get('image_url', '')
                })

        return categories
    except Exception as e:
        log.error(f"Erreur lors de la récupération des catégories: {e}")
        return []

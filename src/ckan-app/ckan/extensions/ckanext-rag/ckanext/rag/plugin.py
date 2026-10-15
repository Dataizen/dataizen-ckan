# -*- coding: utf-8 -*-
"""Plugin CKAN `ckanext-rag` : fraîcheur de l'index RAG.

À chaque création/mise à jour/suppression d'un jeu (métadonnées) ou d'une de ses
ressources (contenu), demande au service dtz-rag de ré-indexer ce jeu. Le service
gère lui-même le retrait de l'index si le jeu devient privé ou est supprimé
(v1 : contenu public uniquement).

Deux garde-fous évitent de saturer le worker :
  - file dédiée « rag » (worker séparé) : la réindexation, non critique, ne retarde
    plus l'import datastore (xloader) ni la géométrisation (geo) ;
  - débounce Redis : l'ingestion d'un jeu émet en rafale de nombreux événements
    (création ressource, sortie dérivée, bascule datastore, géométrisation…). On
    n'enfile qu'un seul job par jeu dans une courte fenêtre, au lieu d'une dizaine.
Les deux hooks ressource normalisent l'identifiant en slug du jeu, pour partager
la clé de débounce avec les hooks jeu (sinon UUID d'un côté, slug de l'autre)."""
import logging

from ckan.plugins import SingletonPlugin, implements
from ckan.plugins.interfaces import IPackageController, IResourceController
from ckan.plugins.toolkit import enqueue_job

from ckanext.rag.jobs import reindex_dataset_job

log = logging.getLogger(__name__)

# file dédiée (worker ragworker) et fenêtre de débounce (secondes)
RAG_QUEUE = "rag"
DEBOUNCE_TTL = 120
PENDING_KEY = "ckanext-rag:pending:{}"


def _redis():
    from ckan.lib.redis import connect_to_redis
    return connect_to_redis()


def _pkg_name(resource):
    """Résout le slug du jeu depuis une ressource (dont package_id est un UUID),
    afin de partager la clé de débounce avec les hooks jeu."""
    pid = resource.get("package_id")
    if not pid:
        return None
    try:
        import ckan.model as model
        pkg = model.Package.get(pid)
        if pkg and pkg.name:
            return pkg.name
    except Exception:
        pass
    return pid


def _enqueue(name):
    if not name:
        return
    # débounce : si un job est déjà programmé récemment pour ce jeu, on ne ré-enfile pas.
    try:
        if not _redis().set(PENDING_KEY.format(name), b"1", nx=True, ex=DEBOUNCE_TTL):
            return
    except Exception as e:
        log.warning("[rag] débounce indisponible pour %s (on enfile quand même) : %s", name, e)
    try:
        enqueue_job(reindex_dataset_job, [name], queue=RAG_QUEUE,
                    title=f"RAG reindex {name}")
    except Exception as e:
        log.warning("[rag] enqueue échec pour %s : %s", name, e)


class RagPlugin(SingletonPlugin):
    implements(IPackageController, inherit=True)
    implements(IResourceController, inherit=True)

    # --- métadonnées du jeu (CKAN 2.11 : noms after_dataset_*) ---
    def after_dataset_create(self, context, pkg_dict):
        _enqueue(pkg_dict.get("name") or pkg_dict.get("id"))

    def after_dataset_update(self, context, pkg_dict):
        # couvre aussi le passage en privé / la suppression (state=deleted) :
        # dtz-rag purge l'index dans ces cas.
        _enqueue(pkg_dict.get("name") or pkg_dict.get("id"))

    def after_dataset_delete(self, context, pkg_dict):
        _enqueue(pkg_dict.get("name") or pkg_dict.get("id"))

    # --- contenu (ressources) ---
    def after_resource_create(self, context, resource):
        if resource.get("harmonized_from"):
            return  # sortie dérivée : la source déclenche déjà la réindexation
        _enqueue(_pkg_name(resource))

    def after_resource_update(self, context, resource):
        if resource.get("harmonized_from"):
            return
        _enqueue(_pkg_name(resource))

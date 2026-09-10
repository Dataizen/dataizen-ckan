# -*- coding: utf-8 -*-
"""Plugin CKAN `ckanext-rag` : fraîcheur de l'index RAG.

À chaque création/mise à jour/suppression d'un jeu (métadonnées) ou d'une de ses
ressources (contenu), enqueue un job qui demande au service dtz-rag de ré-indexer
ce jeu. Le service gère lui-même le retrait de l'index si le jeu devient privé ou
est supprimé (v1 : contenu public uniquement)."""
import logging

from ckan.plugins import SingletonPlugin, implements
from ckan.plugins.interfaces import IPackageController, IResourceController
from ckan.plugins.toolkit import enqueue_job

from ckanext.rag.jobs import reindex_dataset_job

log = logging.getLogger(__name__)


def _enqueue(name):
    if not name:
        return
    try:
        enqueue_job(reindex_dataset_job, [name], queue="default",
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
        _enqueue(resource.get("package_id"))

    def after_resource_update(self, context, resource):
        if resource.get("harmonized_from"):
            return
        _enqueue(resource.get("package_id"))

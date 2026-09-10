"""Profil DCAT Dataizen : ajoute la validité d'un jeu de données au graphe RDF.

Complète le profil de base (euro_dcat_ap_3) : à partir des extras `validite_debut`
et `validite_fin`, on émet une période de validité via dct:valid (dct:PeriodOfTime
avec dcat:startDate / dcat:endDate). Utile au moissonnage (péremption des données).
"""
from rdflib import BNode, Literal
from rdflib.namespace import Namespace, RDF, XSD

import ckan.plugins as plugins
from ckanext.dcat.profiles import RDFProfile

DCT = Namespace('http://purl.org/dc/terms/')
DCAT = Namespace('http://www.w3.org/ns/dcat#')


class DataizenDCATConfigPlugin(plugins.SingletonPlugin):
    """Ajoute le profil « dataizen_valid » à la liste des profils DCAT, de façon
    fiable : l'IConfigurer s'exécute après envvars (le mapping par variable
    d'environnement des clés dcat n'est pas fiable ici). À charger APRÈS dcat."""
    plugins.implements(plugins.IConfigurer)

    def update_config(self, config):
        profs = config.get('ckanext.dcat.rdf.profiles') or 'euro_dcat_ap_3'
        if isinstance(profs, (list, tuple)):
            profs = ' '.join(profs)
        if 'dataizen_valid' not in profs.split():
            config['ckanext.dcat.rdf.profiles'] = (profs + ' dataizen_valid').strip()


class DataizenValidityProfile(RDFProfile):
    def graph_from_dataset(self, dataset_dict, dataset_ref):
        debut = self._get_dataset_value(dataset_dict, 'validite_debut')
        fin = self._get_dataset_value(dataset_dict, 'validite_fin')
        if not (debut or fin):
            return
        periode = BNode()
        self.g.add((periode, RDF.type, DCT.PeriodOfTime))
        if debut:
            self.g.add((periode, DCAT.startDate, Literal(debut, datatype=XSD.date)))
        if fin:
            self.g.add((periode, DCAT.endDate, Literal(fin, datatype=XSD.date)))
        self.g.add((dataset_ref, DCT.valid, periode))

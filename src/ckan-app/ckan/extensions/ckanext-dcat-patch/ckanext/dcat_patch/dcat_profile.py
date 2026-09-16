"""Profil DCAT-AP Dataizen : complète euro_dcat_ap_3 en émettant, dans le graphe RDF,
les métadonnées territoriales/temporelles que le portail collecte au dépôt mais que le
profil de base ignore (nos clés d'extras sont en français, cf. portail lib/metadata.js).

Champs ajoutés (mappés vers DCAT-AP FR + vocabulaires EU) :
- dcat:contactPoint       <- point_de_contact (vCard : nom + courriel)
- dct:creator             <- auteur
- dct:license             <- licence du jeu (URL)
- dct:accrualPeriodicity  <- frequence   (autorité EU frequency)
- dct:language            <- langue      (autorité EU language)
- dct:accessRights        <- PUBLIC      (le flux ne contient que des jeux publics)
- dct:spatial             <- couverture_spatiale (label) + territoires (codes départements INSEE)
- dct:temporal            <- couverture_temporelle (années) ou validite_debut/fin
- dcat:landingPage        <- fiche du jeu sur le portail catalogue
- dct:conformsTo          <- dolfin_model_url
- dct:valid               <- validite_debut / validite_fin (période de validité)

Objectif : conformité DCAT-AP renforcée et moissonnabilité par data.gouv.fr.
"""
import re

from rdflib import BNode, Literal, URIRef
from rdflib.namespace import Namespace, RDF, RDFS, XSD

import ckan.plugins as plugins
from ckan.plugins.toolkit import config
from ckanext.dcat.profiles import RDFProfile

DCT = Namespace('http://purl.org/dc/terms/')
DCAT = Namespace('http://www.w3.org/ns/dcat#')
FOAF = Namespace('http://xmlns.com/foaf/0.1/')
VCARD = Namespace('http://www.w3.org/2006/vcard/ns#')
SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
LOCN = Namespace('http://www.w3.org/ns/locn#')

AUTH = 'http://publications.europa.eu/resource/authority/'
FREQ = AUTH + 'frequency/'
LANG = AUTH + 'language/'
ACCESS = AUTH + 'access-right/'
THEME = AUTH + 'data-theme/'
INSEE_DEP = 'http://id.insee.fr/geo/departement/'

# Le thème du catalogue est désormais stocké comme CODE EU data-theme (ex. ENVI) dans
# l'extra `theme`. On émet l'URI directement. THEME_MAP reste pour la rétro-compatibilité
# des jeux non encore migrés (extra `theme` = ancien libellé français).
EU_THEME_CODES = {'AGRI', 'ECON', 'EDUC', 'ENER', 'ENVI', 'GOVE', 'HEAL',
                  'INTR', 'JUST', 'REGI', 'SOCI', 'TECH', 'TRAN'}
THEME_MAP = {
    'Administration et action publique': ['GOVE'],
    'Agriculture': ['AGRI'],
    'Aménagement du territoire et urbanisme': ['REGI'],
    'Biodiversité et eau': ['ENVI'],
    'Citoyenneté et démocratie': ['GOVE'],
    'Climat, air et énergie': ['ENVI', 'ENER'],
    'Culture, patrimoine et tourisme': ['EDUC'],
    'Economie et entreprises': ['ECON'],
    'Equipements, bâtiments et logements': ['REGI'],
    'Formation, éducation et emploi': ['EDUC'],
    'Mobilité et transports': ['TRAN'],
    'Nuisances, déchets et risques': ['ENVI'],
    'Occupation des sols': ['ENVI'],
    'Social, santé et sports': ['HEAL'],
}


def theme_codes(value):
    """Codes EU data-theme d'un jeu : le code lui-même s'il est déjà EU, sinon la
    conversion de l'ancien libellé français (rétro-compatibilité migration)."""
    v = (value or '').strip()
    if v in EU_THEME_CODES:
        return [v]
    return THEME_MAP.get(v, [])
# Formats considérés comme des fichiers téléchargeables (pour dcat:downloadURL)
FILE_FORMATS = {'CSV', 'TSV', 'JSON', 'GEOJSON', 'XLSX', 'XLS', 'ODS', 'SHP', 'GPKG',
                'PDF', 'ZIP', 'XML', 'PARQUET', 'GEOTIFF', 'TIFF', 'KML', 'GML',
                'PMTILES', 'MBTILES', 'TXT', 'RDF', 'TTL', 'N3'}

# Libellés du portail (metadata.js) -> codes des autorités EU
FREQ_MAP = {
    'Ponctuelle': 'IRREG', 'Temps réel': 'CONT', 'Quotidienne': 'DAILY',
    'Hebdomadaire': 'WEEKLY', 'Mensuelle': 'MONTHLY', 'Trimestrielle': 'QUARTERLY',
    'Semestrielle': 'ANNUAL_2', 'Annuelle': 'ANNUAL', 'Pluriannuelle': 'OTHER',
    'Inconnue': 'UNKNOWN',
}
LANG_MAP = {'Français': 'FRA', 'Anglais': 'ENG', 'Multilingue': 'MUL'}
# Licences courantes sans license_url exploitable -> URL canonique
LICENSE_MAP = {
    'cc-by': 'https://creativecommons.org/licenses/by/4.0/',
    'cc-by-sa': 'https://creativecommons.org/licenses/by-sa/4.0/',
    'cc-zero': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'odc-odbl': 'https://opendatacommons.org/licenses/odbl/1-0/',
    'lov2': 'https://spdx.org/licenses/etalab-2.0.html',
    'fr-lo': 'https://spdx.org/licenses/etalab-2.0.html',
}


class DataizenDCATConfigPlugin(plugins.SingletonPlugin):
    """Ajoute le profil « dataizen_valid » à la liste des profils DCAT, de façon
    fiable (IConfigurer s'exécute après envvars). À charger APRÈS dcat."""
    plugins.implements(plugins.IConfigurer)

    def update_config(self, config):
        profs = config.get('ckanext.dcat.rdf.profiles') or 'euro_dcat_ap_3'
        if isinstance(profs, (list, tuple)):
            profs = ' '.join(profs)
        if 'dataizen_valid' not in profs.split():
            config['ckanext.dcat.rdf.profiles'] = (profs + ' dataizen_valid').strip()


class DataizenValidityProfile(RDFProfile):
    def graph_from_dataset(self, dataset_dict, dataset_ref):
        g = self.g
        val = lambda k: (self._get_dataset_value(dataset_dict, k) or '').strip()

        # -- Point de contact (vCard) : "Nom — courriel" ou "courriel" ou "Nom"
        contact = val('point_de_contact')
        if contact:
            m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', contact)
            email = m.group(0) if m else None
            nom = contact
            if email:
                nom = contact.replace(email, '')
            nom = nom.strip(' \t-–—:;,').strip()
            cp = BNode()
            g.add((cp, RDF.type, VCARD.Kind))
            if nom:
                g.add((cp, VCARD.fn, Literal(nom)))
            if email:
                g.add((cp, VCARD.hasEmail, URIRef('mailto:' + email)))
            g.add((dataset_ref, DCAT.contactPoint, cp))

        # -- Auteur -> dct:creator (Agent)
        auteur = val('auteur')
        if auteur:
            ag = BNode()
            g.add((ag, RDF.type, FOAF.Agent))
            g.add((ag, FOAF.name, Literal(auteur)))
            g.add((dataset_ref, DCT.creator, ag))

        # -- Licence (niveau jeu)
        lic_url = (dataset_dict.get('license_url') or '').strip()
        lic_id = (dataset_dict.get('license_id') or '').strip()
        lic = lic_url or LICENSE_MAP.get(lic_id)
        if lic and lic.startswith('http'):
            g.add((dataset_ref, DCT.license, URIRef(lic)))

        # -- Fréquence de mise à jour (autorité EU)
        code = FREQ_MAP.get(val('frequence'))
        if code:
            g.add((dataset_ref, DCT.accrualPeriodicity, URIRef(FREQ + code)))

        # -- Langue (autorité EU)
        lg = LANG_MAP.get(val('langue'))
        if lg:
            g.add((dataset_ref, DCT.language, URIRef(LANG + lg)))

        # -- Droits d'accès : le flux ne contient que des jeux publics
        g.add((dataset_ref, DCT.accessRights, URIRef(ACCESS + 'PUBLIC')))

        # -- Thématique -> URIs du vocabulaire EU data-theme (code EU stocké, ou ancien libellé)
        for code in theme_codes(val('theme')):
            g.add((dataset_ref, DCAT.theme, URIRef(THEME + code)))

        # -- downloadURL sur les distributions de fichiers (en plus de accessURL) : les
        # distributions ont été ajoutées par le profil de base. On copie accessURL ->
        # downloadURL pour les ressources téléchargeables (fichier déposé ou format fichier).
        res_by_url = {}
        for r in dataset_dict.get('resources', []):
            u = (r.get('url') or '').strip()
            if u:
                res_by_url.setdefault(u, r)
        for dist in list(g.objects(dataset_ref, DCAT.distribution)):
            if list(g.objects(dist, DCAT.downloadURL)):
                continue
            for acc in g.objects(dist, DCAT.accessURL):
                r = res_by_url.get(str(acc))
                if r and (r.get('url_type') == 'upload'
                          or (r.get('format') or '').upper() in FILE_FORMATS):
                    g.add((dist, DCAT.downloadURL, acc))
                break

        # -- Couverture spatiale : label libre + départements INSEE
        loc_label = val('couverture_spatiale')
        if loc_label:
            loc = BNode()
            g.add((loc, RDF.type, DCT.Location))
            g.add((loc, LOCN.geographicName, Literal(loc_label)))
            g.add((loc, SKOS.prefLabel, Literal(loc_label)))
            g.add((dataset_ref, DCT.spatial, loc))
        for code_dep in re.findall(r'\b(\d{2,3}[A-B]?)\b', val('territoires')):
            g.add((dataset_ref, DCT.spatial, URIRef(INSEE_DEP + code_dep)))

        # -- Couverture temporelle : "2020-2025" / "millésime 2025" -> PeriodOfTime
        annees = re.findall(r'\b(\d{4})\b', val('couverture_temporelle'))
        if annees:
            debut, fin = min(annees), max(annees)
            per = BNode()
            g.add((per, RDF.type, DCT.PeriodOfTime))
            g.add((per, DCAT.startDate, Literal('%s-01-01' % debut, datatype=XSD.date)))
            g.add((per, DCAT.endDate, Literal('%s-12-31' % fin, datatype=XSD.date)))
            g.add((dataset_ref, DCT.temporal, per))

        # -- Modèle DOLFIN de référence -> dct:conformsTo
        dolfin = val('dolfin_model_url')
        if dolfin.startswith('http'):
            g.add((dataset_ref, DCT.conformsTo, URIRef(dolfin)))

        # -- Landing page : fiche du jeu sur le portail catalogue
        site = (config.get('ckan.site_url') or '').rstrip('/')
        name = dataset_dict.get('name')
        if site and name:
            g.add((dataset_ref, DCAT.landingPage, URIRef('%s/dataset/%s' % (site, name))))

        # -- Période de validité (péremption) -> dct:valid
        debut, fin = val('validite_debut'), val('validite_fin')
        if debut or fin:
            periode = BNode()
            g.add((periode, RDF.type, DCT.PeriodOfTime))
            if debut:
                g.add((periode, DCAT.startDate, Literal(debut, datatype=XSD.date)))
            if fin:
                g.add((periode, DCAT.endDate, Literal(fin, datatype=XSD.date)))
            g.add((dataset_ref, DCT.valid, periode))

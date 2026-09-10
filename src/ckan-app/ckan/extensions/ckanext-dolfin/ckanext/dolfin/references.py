# -*- coding: utf-8 -*-
"""Adaptateurs de RÉFÉRENCE par domaine (MIMaThon, Dataizen).

Enrichit les entités harmonisées en alignant certains champs sur des référentiels
externes ouverts :
- **GBIF** (backbone taxonomique) : un nom d'espèce -> clé/URL GBIF + nom canonique.
- **Wikidata** : un libellé (catégorie, type...) -> identifiant + URL Wikidata.

Best-effort : stdlib seule, cache en mémoire (dédoublonne les appels au sein d'un
jeu), timeout court, dégradation silencieuse si le réseau/API est indisponible (aucune
erreur bloquante). Bornage du nombre de valeurs distinctes résolues pour éviter de
marteler les APIs sur de très gros jeux.
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request

_UA = "Dataizen-harmonizer/1.0 (+https://dataizen.eu; contact@dataizen.eu)"
_MAX_DISTINCT = 800          # plafond de valeurs distinctes résolues par champ
_TIMEOUT = 8

GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
GBIF_SPECIES_PAGE = "https://www.gbif.org/species/{key}"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/entity/{id}"

_gbif_cache: dict = {}
_wikidata_cache: dict = {}


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def gbif_resolve(name):
    """Nom scientifique -> {url, usageKey, canonicalName, rank} ou None."""
    key = (name or "").strip()
    if not key:
        return None
    if key in _gbif_cache:
        v = _gbif_cache[key]
        return None if v is False else v
    try:
        p = _get_json(GBIF_MATCH_URL + "?" + urllib.parse.urlencode({"name": key, "verbose": "false"}))
    except Exception:
        return None  # erreur réseau : ne pas cacher (retry possible plus tard)
    uk = p.get("usageKey")
    if not uk or p.get("matchType") == "NONE":
        _gbif_cache[key] = False
        return None
    entry = {"url": GBIF_SPECIES_PAGE.format(key=uk), "usageKey": uk,
             "canonicalName": p.get("canonicalName") or p.get("scientificName"),
             "rank": p.get("rank"), "matchType": p.get("matchType")}
    _gbif_cache[key] = entry
    return entry


def wikidata_resolve(label, lang="fr"):
    """Libellé -> {url, id, label} Wikidata (wbsearchentities) ou None."""
    key = (label or "").strip()
    if not key:
        return None
    ck = lang + "|" + key
    if ck in _wikidata_cache:
        v = _wikidata_cache[ck]
        return None if v is False else v
    try:
        p = _get_json(WIKIDATA_API + "?" + urllib.parse.urlencode({
            "action": "wbsearchentities", "search": key, "language": lang,
            "uselang": lang, "format": "json", "limit": 1, "type": "item"}))
    except Exception:
        return None
    hits = p.get("search") or []
    if not hits:
        _wikidata_cache[ck] = False
        return None
    h = hits[0]
    entry = {"url": WIKIDATA_ENTITY.format(id=h.get("id")), "id": h.get("id"),
             "label": h.get("label")}
    _wikidata_cache[ck] = entry
    return entry


ENRICHERS = {"gbif": gbif_resolve, "wikidata": wikidata_resolve}

# Références appliquées par défaut selon le modèle DOLFIN (surchargeables via
# mapping["references"] = {champ: nom_référentiel}).
DEFAULT_REFERENCES = {
    "ClassifiedTree": {"species": "gbif"},
    "PointOfInterest": {"category": "wikidata"},
}


def references_for(mapping):
    return mapping.get("references") or DEFAULT_REFERENCES.get(mapping.get("type"), {})


def cached_result(refname, value):
    """Résultat DÉJÀ résolu (lecture du cache uniquement, aucun appel réseau). Sert aux
    writers CSV/GeoJSON qui réutilisent l'enrichissement calculé par harmonize()."""
    v = (value or "").strip()
    if not v:
        return None
    if refname == "gbif":
        e = _gbif_cache.get(v)
    elif refname == "wikidata":
        e = _wikidata_cache.get("fr|" + v)
    else:
        return None
    return e if isinstance(e, dict) else None


def ref_url_and_canonical(refname, value):
    """(url, canonical) depuis le cache, ou (None, None)."""
    r = cached_result(refname, value)
    if not r:
        return None, None
    return r.get("url"), (r.get("canonicalName") or r.get("label"))


def enrich(entities, mapping, warnings=None):
    """Ajoute à chaque entité, pour chaque champ référencé, une propriété `<champ>Ref`
    (URL du référentiel) et `<champ>Canonical` (libellé normalisé). Idempotent, borné."""
    refs = references_for(mapping)
    if not refs:
        return entities
    for field, refname in refs.items():
        fn = ENRICHERS.get(refname)
        if not fn:
            if warnings is not None:
                warnings.append(f"référentiel inconnu ignoré : {refname}")
            continue
        distinct = 0
        seen = set()
        n_ok = 0
        for ent in entities:
            prop = ent.get(field)
            val = prop.get("value") if isinstance(prop, dict) else None
            if not val:
                continue
            sval = str(val)
            if sval not in seen:
                if distinct >= _MAX_DISTINCT:
                    continue
                seen.add(sval)
                distinct += 1
            res = fn(sval)
            if res:
                ent[field + "Ref"] = {"type": "Property", "value": res["url"]}
                canon = res.get("canonicalName") or res.get("label")
                if canon:
                    ent[field + "Canonical"] = {"type": "Property", "value": canon}
                n_ok += 1
        if warnings is not None:
            warnings.append(f"référentiel {refname} sur « {field} » : {n_ok} entité(s) enrichie(s), "
                            f"{distinct} valeur(s) distincte(s)")
    return entities

#!/usr/bin/env python3
"""Adaptateur tabulaire GÉNÉRIQUE (MIMaThon Phase 2, Dataizen).

Harmonise n'importe quel CSV/tableau vers du NGSI-LD / JSON-LD (cible Smart Data
Models) à partir d'un simple **mapping** colonne -> champ, SANS écrire un adaptateur
Python par domaine. Le mapping est aligné sur un modèle DOLFIN (les noms de champs
sont ceux des `concept` du `.dolfin`).

Zéro dépendance (stdlib). Utilisable en CLI ou importable (fonction `harmonize`).

Mapping (JSON), exemple minimal :
{
  "type": "PointOfInterest",                # entité cible (Smart Data Model)
  "context": "https://smartdatamodels.org/context.jsonld",
  "id_prefix": "urn:ngsi-ld:PointOfInterest:porto:",
  "id_field": "id",                         # colonne servant d'identifiant (sinon index)
  "fields": {                               # champ NGSI-LD : colonne source
     "name": "nom",
     "category": "categorie"
  },
  "location": {"lon": "longitude", "lat": "latitude"},   # optionnel -> GeoProperty Point
  "dolfin": "examples/pois.dolfin"          # optionnel : validation des champs
}

Sortie : un tableau JSON-LD d'entités NGSI-LD (Property/GeoProperty), prêt pour SDM.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from pathlib import Path


def _load_references():
    """Charge le module `references` en contexte package (CKAN) OU standalone (toolkit).
    Renvoie None si absent (l'enrichissement est alors simplement ignoré)."""
    try:
        from . import references  # package (ckanext.dataload_router)
        return references
    except Exception:
        try:
            import references  # standalone (dossier du toolkit)
            return references
        except Exception:
            return None


def _dolfin_fields(dolfin_path):
    """Champs déclarés dans un .dolfin (pour valider le mapping). Best-effort."""
    fields = set()
    try:
        for line in Path(dolfin_path).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*has\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
            if m:
                fields.add(m.group(1))
    except Exception:
        pass
    return fields


def _num(v):
    """Convertit en nombre si possible (sinon renvoie la chaîne nettoyée)."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return s


def _row_to_entity(row, mapping, index):
    etype = mapping["type"]
    idf = mapping.get("id_field")
    raw_id = (row.get(idf) if idf else None) or str(index)
    eid = mapping.get("id_prefix", f"urn:ngsi-ld:{etype}:") + re.sub(r"\s+", "-", str(raw_id).strip())
    ent = {"id": eid, "type": etype}
    for field, col in (mapping.get("fields") or {}).items():
        val = _num(row.get(col))
        if val is not None and val != "":
            ent[field] = {"type": "Property", "value": val}
    loc = mapping.get("location")
    if loc:
        lon, lat = _num(row.get(loc.get("lon"))), _num(row.get(loc.get("lat")))
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            ent["location"] = {"type": "GeoProperty",
                               "value": {"type": "Point", "coordinates": [lon, lat]}}
    return ent


def harmonize(rows, mapping):
    """rows : itérable de dict (lignes) ; mapping : dict. Retourne (entities, warnings)."""
    warnings = []
    if mapping.get("dolfin"):
        declared = _dolfin_fields(mapping["dolfin"])
        if declared:
            unknown = [f for f in (mapping.get("fields") or {}) if f not in declared]
            if unknown:
                warnings.append(f"champs hors du modèle DOLFIN : {', '.join(unknown)}")
    entities = [_row_to_entity(r, mapping, i) for i, r in enumerate(rows)]
    # Adaptateurs de référence par domaine (GBIF, Wikidata...) : enrichissement
    # best-effort, sans échec bloquant.
    references = _load_references()
    if references is not None:
        try:
            references.enrich(entities, mapping, warnings)
        except Exception as e:  # pragma: no cover
            warnings.append(f"enrichissement référentiel ignoré : {str(e)[:120]}")
    return entities, warnings


def _ref_fields(mapping):
    """[(champ, refname)] applicables (references du mapping/modèle) limités aux champs
    mappés. Renvoie aussi le module references (ou None si absent)."""
    references = _load_references()
    if references is None:
        return [], None
    refs = references.references_for(mapping)
    fields = mapping.get("fields") or {}
    return [(f, rn) for f, rn in refs.items() if f in fields], references


def to_geojson(rows, mapping):
    """Writer GeoJSON : FeatureCollection avec géométrie Point (depuis location) et
    propriétés = champs mappés (+ id, + enrichissements référentiels <champ>Ref/Canonical).
    Les lignes sans coordonnées valides ont une géométrie null (Feature conservé)."""
    loc = mapping.get("location") or {}
    lonc, latc = loc.get("lon"), loc.get("lat")
    fields = mapping.get("fields") or {}
    idf = mapping.get("id_field")
    ref_fields, references = _ref_fields(mapping)
    feats = []
    for row in rows:
        props = {}
        if idf and row.get(idf) not in (None, ""):
            props["id"] = row.get(idf)
        for field, col in fields.items():
            val = _num(row.get(col))
            if val is not None and val != "":
                props[field] = val
        for field, refname in ref_fields:
            url, canon = references.ref_url_and_canonical(refname, row.get(fields[field]))
            if url:
                props[field + "Ref"] = url
            if canon:
                props[field + "Canonical"] = canon
        geom = None
        if lonc and latc:
            lon, lat = _num(row.get(lonc)), _num(row.get(latc))
            if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                geom = {"type": "Point", "coordinates": [lon, lat]}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def to_harmonized_csv(rows, mapping):
    """Writer CSV harmonisé : colonnes renommées aux noms canoniques du modèle
    (id, champs du modèle, longitude/latitude), plus les enrichissements référentiels
    (<champ>_ref / <champ>_canonical), au lieu des noms de colonnes source."""
    import io
    fields = mapping.get("fields") or {}
    idf = mapping.get("id_field")
    loc = mapping.get("location") or {}
    has_geo = bool(loc.get("lon") and loc.get("lat"))
    ref_fields, references = _ref_fields(mapping)
    ref_cols = []
    for field, _rn in ref_fields:
        ref_cols += [field + "_ref", field + "_canonical"]
    header = (["id"] if idf else []) + list(fields.keys()) \
        + (["longitude", "latitude"] if has_geo else []) + ref_cols
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        line = ([row.get(idf, "")] if idf else []) + [row.get(col, "") for col in fields.values()]
        if has_geo:
            line += [row.get(loc["lon"], ""), row.get(loc["lat"], "")]
        for field, refname in ref_fields:
            url, canon = references.ref_url_and_canonical(refname, row.get(fields[field]))
            line += [url or "", canon or ""]
        w.writerow(line)
    return buf.getvalue()


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Harmonisation tabulaire générique -> NGSI-LD/JSON-LD")
    ap.add_argument("--input", required=True, help="CSV source")
    ap.add_argument("--mapping", required=True, help="mapping JSON (colonne -> champ)")
    ap.add_argument("--output", help="sortie JSON-LD (sinon stdout)")
    args = ap.parse_args(argv)
    mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    rows = _read_csv(args.input)
    entities, warnings = harmonize(rows, mapping)
    for w in warnings:
        print(f"[avertissement] {w}", file=sys.stderr)
    doc = entities
    if mapping.get("context"):
        # enveloppe avec @context si demandé (NGSI-LD accepte les deux formes)
        doc = {"@context": mapping["context"], "entities": entities}
    out = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"{len(entities)} entités harmonisées -> {args.output} (type {mapping['type']})")
    else:
        print(out)


if __name__ == "__main__":
    main()

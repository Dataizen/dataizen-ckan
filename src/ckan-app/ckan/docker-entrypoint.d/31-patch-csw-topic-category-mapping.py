#!/usr/bin/env python3
"""
CSW / ISO19139 patcher (ckanext-spatial)

Objectif:
- Corriger AVANT validation les erreurs XML fréquentes (TopicCategoryCode FR -> ISO, Integer vides, nilReason sur Integer)
- Optionnel: convertir les codes ISO (anglais) en libellés FR dans CKAN (extras), après _transform_to_ckan

Contrainte:
- Idempotent: ne doit pas se dupliquer, et doit pouvoir se réappliquer via FORCE_CSW_PATCH_REAPPLY=true
"""

import os
import re
import sys
import tempfile
import subprocess
import py_compile
import textwrap


SPATIAL_BASE = "/srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py"

PATCH_BEGIN = "# Patch: correction des erreurs de validation XML AVANT validation"
PATCH_END = "# [CSW PATCH] FIN correction XML avant validation"

FRENCH_PATCH_MARKER = "# Patch: Conversion des TopicCategoryCode anglais -> français dans CKAN"

FORCE_REAPPLY = os.getenv("FORCE_CSW_PATCH_REAPPLY", "false").lower() == "true"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_atomic(path: str, content: str) -> None:
    # Validate syntax BEFORE writing
    fd, tmp_path = tempfile.mkstemp(prefix="base.py.", suffix=".tmp", dir="/tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        py_compile.compile(tmp_path, doraise=True)

        try:
            os.replace(tmp_path, path)
        except PermissionError:
            # Fallback with sudo mv (common in some images)
            subprocess.run(["sudo", "mv", tmp_path, path], check=True)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _remove_existing_patch(content: str) -> str:
    # Remove ALL occurrences between PATCH_BEGIN and PATCH_END (inclusive)
    pattern = re.compile(
        re.escape(PATCH_BEGIN) + r".*?" + re.escape(PATCH_END) + r"\n?",
        flags=re.DOTALL,
    )
    while True:
        new = pattern.sub("", content)
        if new == content:
            return content
        content = new


def _is_patch_present_once(content: str) -> bool:
    return content.count(PATCH_BEGIN) == 1 and content.count(PATCH_END) == 1


def _find_import_stage_block(content: str):
    """Return (start_idx, end_idx, block_text) for def import_stage(...): (top-level or in class)"""
    # Chercher def import_stage avec ou sans indentation (peut être dans une classe)
    # Pattern générique qui matche même avec indentation
    pattern = r"^(\s*)def\s+import_stage\s*\([^)]*\):\s*$"
    m = re.search(pattern, content, flags=re.MULTILINE)
    
    if not m:
        # Debug: chercher toutes les lignes qui contiennent "import_stage"
        debug_lines = [ln.strip()[:100] for ln in content.splitlines() if "import_stage" in ln.lower()]
        if debug_lines:
            print(f"[CSW PATCH] Debug: lignes contenant 'import_stage':")
            for i, ln in enumerate(debug_lines[:5], 1):
                print(f"   {i}. {ln}")
        else:
            # Chercher toutes les méthodes def pour debug
            all_defs = [ln.strip()[:100] for ln in content.splitlines() if re.match(r'^\s*def\s+\w+\s*\(', ln)]
            if all_defs:
                print(f"[CSW PATCH] Debug: méthodes trouvées (échantillon):")
                for i, ln in enumerate(all_defs[:10], 1):
                    print(f"   {i}. {ln}")
        return None
    
    start = m.start()
    indent_level = len(m.group(1))  # Indentation de la méthode import_stage
    
    # Trouver la fin du bloc: prochaine méthode def au même niveau ou moins indentée
    # ou fin du fichier
    after_start = content[m.end():]
    lines = after_start.splitlines(keepends=True)
    end_offset = 0
    
    for i, line in enumerate(lines):
        # Ignorer les lignes vides et les commentaires
        stripped = line.lstrip()
        if not stripped or stripped.startswith('#'):
            end_offset += len(line)
            continue
        
        # Si on trouve une def avec une indentation <= indent_level, on s'arrête
        match_def = re.match(r'^(\s*)def\s+\w+\s*\(', line)
        if match_def:
            if len(match_def.group(1)) <= indent_level:
                break
        end_offset += len(line)
    
    end = m.end() + end_offset
    return start, end, content[start:end]


def _indent_block(block: str, indent: str) -> str:
    block = textwrap.dedent(block).rstrip("\n")
    return "\n".join(indent + line if line.strip() else line for line in block.splitlines())


PATCH_XML_BLOCK = f"""
{PATCH_BEGIN}
# [CSW PATCH] Ce patch corrige des erreurs XML communes AVANT la validation ISO19139
print("[CSW PATCH] Début correction XML avant validation")

if harvest_object.content:
    try:
        from lxml import etree

        ns = {{
            "gmd": "http://www.isotc211.org/2005/gmd",
            "gco": "http://www.isotc211.org/2005/gco",
        }}

        topic_category_mapping = {{
            "limites-administratives": "boundaries",
            "occupation-du-sol": "imageryBaseMapsEarthCover",
            "services": "society",
            "transport": "transportation",
            "transports": "transportation",
            "urbanisme": "planningCadastre",
            "cadastre": "planningCadastre",
            "foncier": "planningCadastre",
            "eau": "inlandWaters",
            "environnement": "environment",
            "energie": "utilitiesCommunication",
            "electricite": "utilitiesCommunication",
            "reseaux": "utilitiesCommunication",
            "batiments": "structure",
            "culture": "society",
            "citoyennete": "society",
            "economie": "economy",
            "equipement": "structure",
            "localisation": "location",
            "patrimoine": "structure",
            "risques": "society",
            "contraintes": "planningCadastre",
        }}

        root = etree.fromstring(
            harvest_object.content.encode("utf-8")
            if isinstance(harvest_object.content, str)
            else harvest_object.content
        )

        modified = False

        # 1) TopicCategoryCode: mapper FR -> ISO (anglais), supprimer vides
        topic_codes = root.findall(".//gmd:MD_TopicCategoryCode", ns)
        for tc in list(topic_codes):
            txt = (tc.text or "").strip()
            if not txt:
                parent = tc.getparent()
                if parent is not None:
                    parent.remove(tc)
                    modified = True
                continue
            if txt in topic_category_mapping:
                tc.text = topic_category_mapping[txt]
                modified = True
                print(f"[CSW PATCH] TopicCategoryCode: '{{txt}}' -> '{{tc.text}}'")

        # 2) gco:Integer: supprimer nilReason + supprimer vides / invalides
        integers = root.findall(".//gco:Integer", ns)
        for ie in list(integers):
            parent = ie.getparent()
            if parent is None:
                continue

            # remove nilReason (not allowed on gco:Integer)
            for attr in list(ie.attrib.keys()):
                if attr.endswith("}}nilReason") or attr == "nilReason":
                    ie.attrib.pop(attr, None)
                    modified = True

            txt = (ie.text or "").strip()
            if not txt:
                parent.remove(ie)
                modified = True
                continue
            try:
                int(txt)
            except Exception:
                parent.remove(ie)
                modified = True

        # 3) gco:Date: supprimer les éléments vides (non valides)
        dates = root.findall(".//gco:Date", ns)
        for de in list(dates):
            parent = de.getparent()
            if parent is None:
                continue
            
            txt = (de.text or "").strip()
            if not txt:
                # Date vide: supprimer l'élément
                parent.remove(de)
                modified = True

        # 4) dateStamp: repositionner dans MD_Metadata (après hierarchyLevel, hierarchyLevelName, contact)
        md_metadata = root.find(".//gmd:MD_Metadata", ns)
        if md_metadata is not None:
            date_stamps = md_metadata.findall(".//gmd:dateStamp", ns)
            date_stamp_to_move = None
            
            # Trouver dateStamp et le supprimer temporairement
            for ds in date_stamps:
                parent = ds.getparent()
                # dateStamp doit être directement dans MD_Metadata
                if parent == md_metadata:
                    # Vérifier si dateStamp est mal placé (avant hierarchyLevel/hierarchyLevelName/contact)
                    hierarchy_level = md_metadata.find(".//gmd:hierarchyLevel", ns)
                    hierarchy_level_name = md_metadata.find(".//gmd:hierarchyLevelName", ns)
                    contact = md_metadata.find(".//gmd:contact", ns)
                    
                    # Trouver l'index de dateStamp
                    ds_index = list(md_metadata).index(ds) if ds in list(md_metadata) else -1
                    h_idx = list(md_metadata).index(hierarchy_level) if hierarchy_level is not None and hierarchy_level in list(md_metadata) else -1
                    hn_idx = list(md_metadata).index(hierarchy_level_name) if hierarchy_level_name is not None and hierarchy_level_name in list(md_metadata) else -1
                    c_idx = list(md_metadata).index(contact) if contact is not None and contact in list(md_metadata) else -1
                    
                    # Si dateStamp est avant un de ces éléments obligatoires, le repositionner
                    last_required_idx = max(h_idx, hn_idx, c_idx)
                    if last_required_idx >= 0 and ds_index >= 0 and ds_index < last_required_idx:
                        date_stamp_to_move = ds
                        md_metadata.remove(ds)
                        modified = True
                        break
            
            # Réinsérer dateStamp après les éléments obligatoires
            if date_stamp_to_move is not None:
                hierarchy_level = md_metadata.find(".//gmd:hierarchyLevel", ns)
                hierarchy_level_name = md_metadata.find(".//gmd:hierarchyLevelName", ns)
                contact = md_metadata.find(".//gmd:contact", ns)
                
                # Trouver la position d'insertion (après le dernier des trois)
                insert_after = None
                if contact is not None and contact in list(md_metadata):
                    insert_after = contact
                elif hierarchy_level_name is not None and hierarchy_level_name in list(md_metadata):
                    insert_after = hierarchy_level_name
                elif hierarchy_level is not None and hierarchy_level in list(md_metadata):
                    insert_after = hierarchy_level
                
                if insert_after is not None:
                    idx = list(md_metadata).index(insert_after)
                    md_metadata.insert(idx + 1, date_stamp_to_move)
                    print("[CSW PATCH] dateStamp repositionné dans MD_Metadata")
                else:
                    # Si aucun élément obligatoire trouvé, insérer au début (mais après les attributs)
                    md_metadata.insert(0, date_stamp_to_move)

        if modified:
            xml_bytes = etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=False)
            # Décoder en string et supprimer la déclaration XML pour éviter les erreurs
            # _validate_document() utilise re.sub() qui attend une string, pas des bytes
            import re as _re
            xml_str = xml_bytes.decode("utf-8")
            # Supprimer la déclaration XML car _validate_document() le fait aussi et ça évite les conflits
            xml_str = _re.sub(r'<\?xml[^>]*\?>', '', xml_str, count=1).strip()
            harvest_object.content = xml_str
            print(f"[CSW PATCH] XML corrigé (len={{len(harvest_object.content)}})")
        else:
            print("[CSW PATCH] Aucune correction nécessaire")

    except Exception as e:
        print(f"[CSW PATCH] Erreur correction XML (skip): {{e}}")
else:
    print("[CSW PATCH] harvest_object.content vide")
# [CSW PATCH] FIN correction XML avant validation
"""


FRENCH_MAPPING_BLOCK = f"""
{FRENCH_PATCH_MARKER}
import os
csw_language = os.getenv("CSW_METADATA_LANGUAGE", "fr").lower()
if csw_language in ["fr", "french", "français"] and isinstance(package_dict, dict):
    topic_category_french_labels = {{
        "boundaries": "Limites administratives",
        "imageryBaseMapsEarthCover": "Occupation du sol",
        "society": "Services",
        "transportation": "Transport",
        "structure": "Bâtiments",
        "economy": "Économie",
        "location": "Localisation",
        "environment": "Environnement",
        "inlandWaters": "Eaux intérieures",
        "planningCadastre": "Planification, cadastre",
        "utilitiesCommunication": "Réseaux, communication",
    }}

    extras = package_dict.get("extras")
    if isinstance(extras, list):
        for extra in extras:
            if isinstance(extra, dict) and extra.get("key") in ["spatial_topic_category", "topic_category", "iso_topic_category"]:
                v = extra.get("value")
                if isinstance(v, str) and v in topic_category_french_labels:
                    extra["value"] = topic_category_french_labels[v]
                elif isinstance(v, list):
                    extra["value"] = [topic_category_french_labels.get(x, x) for x in v]
"""


def main() -> int:
    print("[CSW PATCH] Script exécuté")

    if not os.path.exists(SPATIAL_BASE):
        print(f"Fichier non trouvé: {SPATIAL_BASE}")
        return 0

    content = _read(SPATIAL_BASE)

    if FORCE_REAPPLY:
        content = _remove_existing_patch(content)

    # If already present cleanly, skip
    if _is_patch_present_once(content) and "[CSW PATCH]" in content:
        print("[CSW PATCH] Patch déjà appliqué correctement, skip")
        return 0

    blk = _find_import_stage_block(content)
    if not blk:
        raise RuntimeError("[CSW PATCH] ERREUR FATALE: def import_stage(...) introuvable dans base.py")

    start, end, block = blk

    # Find FIRST validation call inside import_stage
    validate_line_re = re.compile(r"^(?P<indent>[ \t]+)(?P<call>.*?_validate_document\s*\([^\n]*\))\s*$", re.MULTILINE)
    m = validate_line_re.search(block)
    if not m:
        lines = [ln.strip() for ln in block.splitlines() if "_validate_document" in ln]
        msg = "[CSW PATCH] ERREUR FATALE: _validate_document(...) introuvable dans import_stage"
        if lines:
            msg += "\nExemples:\n- " + "\n- ".join(lines[:3])
        raise RuntimeError(msg)

    indent = m.group("indent")
    call = m.group("call").rstrip()

    patched_xml = _indent_block(PATCH_XML_BLOCK, indent)
    replacement = patched_xml + "\n" + indent + call

    new_block, n = validate_line_re.subn(replacement, block, count=1)
    if n != 1:
        raise RuntimeError(f"[CSW PATCH] ERREUR FATALE: substitution _validate_document inattendue (n={n})")

    # Optional French mapping right after package_dict assignment from _transform_to_ckan (once)
    if FRENCH_PATCH_MARKER not in new_block:
        assign_re = re.compile(r"^(?P<indent>[ \t]+)(package_dict\s*=\s*self\._transform_to_ckan\s*\([^\n]*\))\s*$", re.MULTILINE)
        m2 = assign_re.search(new_block)
        if m2:
            i2 = m2.group("indent")
            insertion = m2.group(0) + "\n" + _indent_block(FRENCH_MAPPING_BLOCK, i2)
            new_block = assign_re.sub(insertion, new_block, count=1)
            print("[CSW PATCH] Patch FR appliqué après package_dict = self._transform_to_ckan(...)")
        else:
            print("[CSW PATCH] Patch FR: _transform_to_ckan non trouvé dans import_stage, skip (OK)")

    new_content = content[:start] + new_block + content[end:]

    if new_content.count(PATCH_BEGIN) != 1 or new_content.count(PATCH_END) != 1:
        raise RuntimeError("[CSW PATCH] ERREUR FATALE: patch non unique après insertion")

    _write_atomic(SPATIAL_BASE, new_content)
    print("[CSW PATCH] Patch appliqué avec succès")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(str(e))
        sys.exit(2)
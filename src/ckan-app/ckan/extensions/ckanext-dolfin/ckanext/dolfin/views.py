# -*- coding: utf-8 -*-
"""Interface d'harmonisation DOLFIN dans le catalogue CKAN.

Page au niveau d'un dataset (/dataset/<id>/dolfin), reservee aux personnes pouvant
editer le dataset (admin/editeur de l'organisation, ou sysadmin) via l'auth
`package_update`. Reutilise l'extra `dolfin_mapping` et l'harmonisation deja gerees
par le plugin (hook _harmonize_if_mapped). Proposition de mapping par l'IA souveraine
(qwen3.5 via le proxy ollama, OLLAMA_URL), avec think=false.
"""
import datetime
import json
import os
import re

from flask import Blueprint, jsonify, request, redirect
from ckan.plugins import toolkit as tk
from ckan.common import g, _

try:
    import urllib.request
    import urllib.parse
except Exception:  # pragma: no cover
    urllib = None


# Modeles DOLFIN de reference (pivot-harmonizer / MIMaThon). Aligne sur la forge.
# champs = champs simples mappes a une colonne ; geo = a une localisation lon/lat.
DOLFIN_MODELES = [
    {"type": "PointOfInterest", "titre": "Points d'intérêt (POI)",
     "champs": ["name", "category", "address"], "geo": True,
     "desc": "Lieux, équipements, services, aligné schema.org / Smart Data Models."},
    {"type": "ClassifiedTree", "titre": "Arbres classés / patrimoniaux",
     "champs": ["localId", "species", "kind", "specimenCount"], "geo": True,
     "desc": "Arbres municipaux, aligné SDM ParksAndGardens / Darwin Core."},
    {"type": "TrafficObservation", "titre": "Observations de trafic",
     "champs": ["observedAt", "city", "intensity", "averageSpeed"], "geo": True,
     "desc": "Mesures de trafic, aligné SDM Transportation / DATEX II."},
]

def _models_dir():
    d = os.getenv('DOLFIN_MODELS_DIR', '/srv/app/dolfin-models')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return s or 'modele'


def _load_models():
    """Modeles disponibles : integres (builtin) + custom (.json du dossier modeles).
    Ordre deterministe (builtin d'abord, puis custom tries) pour un index stable."""
    models = []
    for m in DOLFIN_MODELES:
        mm = dict(m)
        mm['slug'] = m['type']
        mm['builtin'] = True
        models.append(mm)
    d = _models_dir()
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.json'):
                continue
            try:
                data = json.loads(open(os.path.join(d, fn), encoding='utf-8').read())
            except Exception:
                continue
            data['slug'] = fn[:-5]
            data['builtin'] = False
            data.setdefault('champs', [])
            data.setdefault('geo', False)
            models.append(data)
    except Exception:
        pass
    return models


def _get_model(slug):
    for m in _load_models():
        if m.get('slug') == slug:
            return m
    return None


def _model_rev_for_type(type_):
    """Révision courante du modèle d'un `type` donné, pour le suivi de dérive.
    Un modèle custom (éditable, avec `updated_at`) prime sur un built-in de même
    type. Built-in => révision stable `"builtin"` (jamais périmé). Renvoie
    (rev, changed_by)."""
    custom = None
    builtin = None
    for m in _load_models():
        if m.get('type') != type_:
            continue
        if m.get('builtin'):
            builtin = builtin or m
        else:
            custom = custom or m
    m = custom or builtin
    if not m:
        return ('', None)
    if m.get('builtin'):
        return ('builtin', None)
    return (m.get('updated_at') or 'builtin', m.get('updated_by'))


def model_by_type(type_):
    """Modèle DOLFIN correspondant à un `type` (custom prioritaire sur built-in),
    pour enrichir l'affichage de la fiche (titre, description). None si inconnu."""
    if not type_:
        return None
    custom = builtin = None
    for m in _load_models():
        if m.get('type') != type_ and m.get('slug') != type_:
            continue
        if m.get('builtin'):
            builtin = builtin or m
        else:
            custom = custom or m
    return custom or builtin


def _compile_dolfin(source):
    """Valide/compile un .dolfin (best-effort) via le compilateur vendorise, et
    extrait les champs 'has X:'. Renvoie (ok, message, champs, geo)."""
    champs = []
    for line in (source or '').splitlines():
        m = re.match(r'\s*has\s+([A-Za-z_][A-Za-z0-9_]*)\s*:', line)
        if m and m.group(1) not in champs:
            champs.append(m.group(1))
    geo = bool(re.search(r'\b(lat|lon|latitude|longitude|location|geo)\b', source or '', re.I))
    if not (source or '').strip():
        return True, '', champs, geo
    try:
        from ckanext.dolfin import dolfin2model
        concepts = dolfin2model.parse(source)
        ncpt = len(concepts) if concepts else 0
        ok, msg = True, 'Syntaxe .dolfin valide (%d concept(s), %d champ(s)).' % (ncpt, len(champs))
    except Exception as e:
        ok, msg = False, 'Erreur de compilation .dolfin : ' + str(e)[:200]
    return ok, msg, champs, geo


# --- Suivi des modifications (historique) des modèles ---------------------- #
def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _history_dir(slug):
    d = os.path.join(_models_dir(), '.history', slug)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _model_manage_allowed(context):
    """Gestion des modèles (globaux) : réservée à un sysadmin. Le portail appelle ces
    actions avec le token de service (sysadmin) après avoir vérifié lui-même que
    l'utilisateur est admin d'instance (session)."""
    user = context.get('auth_user_obj')
    return bool(user and getattr(user, 'sysadmin', False))


# --- Actions CKAN de gestion des modèles (consommées par le portail) ------- #
def action_dolfin_model_list(context, data_dict):
    if not _model_manage_allowed(context):
        raise tk.NotAuthorized(_('Reserve aux administrateurs.'))
    return {'models': _load_models()}


action_dolfin_model_list.side_effect_free = True


def action_dolfin_model_save(context, data_dict):
    if not _model_manage_allowed(context):
        raise tk.NotAuthorized(_('Reserve aux administrateurs.'))
    titre = (data_dict.get('titre') or '').strip()
    type_ = (data_dict.get('type') or '').strip()
    if not titre or not type_:
        raise tk.ValidationError({'type_titre': 'Le type et le titre sont obligatoires.'})
    slug = _slugify(data_dict.get('slug') or type_ or titre)
    source = data_dict.get('dolfin') or ''
    ok, msg, champs_parsed, geo_parsed = _compile_dolfin(source)
    if not ok:
        raise tk.ValidationError({'dolfin': msg})
    champs = data_dict.get('champs')
    if isinstance(champs, str):
        champs = [c.strip() for c in champs.split(',') if c.strip()]
    if not champs:
        champs = champs_parsed or []
    author = (data_dict.get('author') or context.get('user') or 'inconnu')
    path = os.path.join(_models_dir(), slug + '.json')
    old = None
    if os.path.exists(path):
        try:
            old = json.loads(open(path, encoding='utf-8').read())
        except Exception:
            old = None
    now = _now_iso()
    # snapshot de l'ancienne version (pour restauration/audit)
    if old:
        try:
            snap = os.path.join(_history_dir(slug), now.replace(':', '') + '.json')
            with open(snap, 'w', encoding='utf-8') as fh:
                json.dump(old, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
    history = ((old or {}).get('history') or []) + [
        {'at': now, 'by': author, 'action': 'update' if old else 'create'}]
    data = {
        'type': type_, 'titre': titre, 'desc': (data_dict.get('desc') or '').strip(),
        'champs': champs, 'geo': bool(data_dict.get('geo')) or geo_parsed,
        'dolfin': source,
        'created_at': (old or {}).get('created_at') or now,
        'created_by': (old or {}).get('created_by') or author,
        'updated_at': now, 'updated_by': author,
        'history': history[-50:],
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    out = dict(data, slug=slug, builtin=False)
    # Suivi de dérive : signaler les jeux qui utilisent ce modèle (via dolfin_mapping.type)
    # en avançant leur `dolfin_model_latest_rev`. Ceux dont l'harmonisation date d'une
    # révision antérieure apparaîtront « à régénérer » sur leur fiche (portail + CKAN).
    impacted = _mark_impacted_datasets(context, type_, now, author)
    return {'model': out, 'message': msg, 'impacted': impacted}


def _mark_impacted_datasets(context, type_, rev, author):
    """Pour chaque jeu dont le mapping DOLFIN cible `type_`, pose
    `dolfin_model_latest_rev = rev` (+ auteur) via un package_patch merge-preserving.
    Best-effort (ne bloque pas la sauvegarde du modèle). Renvoie la liste des jeux
    impactés (name, title, org, org_title) pour l'avertissement inter-organisations."""
    impacted = []
    try:
        res = tk.get_action('package_search')(
            dict(context, ignore_auth=True),
            {'q': '*:*', 'rows': 1000, 'include_private': True})
    except Exception:
        return impacted
    for pkg in res.get('results', []):
        raw = next((e.get('value') for e in (pkg.get('extras') or [])
                    if e.get('key') == 'dolfin_mapping'), None)
        if not raw:
            continue
        try:
            if json.loads(raw).get('type') != type_:
                continue
        except Exception:
            continue
        org = pkg.get('organization') or {}
        impacted.append({'name': pkg.get('name'), 'title': pkg.get('title'),
                         'org': org.get('name'), 'org_title': org.get('title')})
        try:
            extras = {e['key']: e['value'] for e in pkg.get('extras', [])}
            extras['dolfin_model_latest_rev'] = rev
            if author:
                extras['dolfin_model_changed_by'] = author
            updated = [{'key': k, 'value': v} for k, v in extras.items()]
            tk.get_action('package_patch')(
                dict(context, ignore_auth=True), {'id': pkg['id'], 'extras': updated})
        except Exception:
            pass
    return impacted


def action_dolfin_model_delete(context, data_dict):
    if not _model_manage_allowed(context):
        raise tk.NotAuthorized(_('Reserve aux administrateurs.'))
    slug = _slugify(data_dict.get('slug') or '')
    path = os.path.join(_models_dir(), slug + '.json')
    if not os.path.exists(path):
        raise tk.ObjectNotFound(_('Modele introuvable'))
    os.unlink(path)
    return {'deleted': slug}


def action_dolfin_model_usage(context, data_dict):
    """Pour chaque type de modèle DOLFIN, la liste des jeux qui l'utilisent (via l'extra
    dolfin_mapping). Réservé aux administrateurs (comme la gestion des modèles)."""
    if not _model_manage_allowed(context):
        raise tk.NotAuthorized(_('Reserve aux administrateurs.'))
    out = {}
    try:
        res = tk.get_action('package_search')(
            dict(context), {'q': '*:*', 'rows': 1000, 'include_private': True})
    except Exception:
        return {'usage': out}
    for pkg in res.get('results', []):
        raw = next((e.get('value') for e in (pkg.get('extras') or [])
                    if e.get('key') == 'dolfin_mapping'), None)
        if not raw:
            continue
        try:
            t = json.loads(raw).get('type')
        except Exception:
            t = None
        if t:
            org = pkg.get('organization') or {}
            out.setdefault(t, []).append({
                'name': pkg.get('name'), 'title': pkg.get('title'),
                'org': org.get('name'), 'org_title': org.get('title')})
    return {'usage': out}


action_dolfin_model_usage.side_effect_free = True


def _gpu_controller_base():
    """Base du gpu-controller déduite de OLLAMA_URL (…/gpu/proxy -> …)."""
    url = os.getenv('OLLAMA_URL') or tk.config.get('ckanext.dataload_router.ollama_url', '')
    if '/gpu/proxy' in url:
        return url.split('/gpu/proxy')[0]
    return ''


def action_dolfin_gpu_status(context, data_dict):
    """État du GPU souverain (indicateur pour l'interface). Tout utilisateur connecté."""
    if not context.get('user'):
        raise tk.NotAuthorized(_('Connexion requise.'))
    base = _gpu_controller_base()
    if not base:
        return {'available': False}
    try:
        import urllib.request
        with urllib.request.urlopen(base + '/gpu/status', timeout=6) as r:
            d = json.loads(r.read())
        return {
            'available': True,
            'instance_status': d.get('instance_status'),
            'ollama_ready': bool(d.get('ollama_ready')),
            'ollama_responding': bool(d.get('ollama_responding')),
            'wake_in_progress': bool(d.get('wake_in_progress')),
            'billing_remaining_minutes': d.get('billing_remaining_minutes'),
        }
    except Exception as e:
        return {'available': False, 'error': str(e)[:100]}


action_dolfin_gpu_status.side_effect_free = True


def _trigger_regenerate(context, pkg_id):
    """Relance l'harmonisation d'un jeu : touche chaque ressource CSV source
    (datastore actif, non dérivée) par un `resource_patch` neutre, ce qui déclenche
    le hook `after_resource_update` -> régénération des sorties + re-tampon de la
    révision du modèle. Renvoie le nombre de ressources retouchées."""
    pkg = tk.get_action('package_show')(context, {'id': pkg_id})
    n = 0
    for res in pkg.get('resources', []):
        if res.get('harmonized_from'):
            continue
        if (res.get('format') or '').lower() == 'csv' and res.get('datastore_active'):
            try:
                tk.get_action('resource_patch')(
                    context, {'id': res['id'], 'description': res.get('description') or ''})
                n += 1
            except Exception:
                pass
    return n


def action_dolfin_regenerate(context, data_dict):
    """Régénère les fichiers harmonisés d'un jeu (après changement du modèle DOLFIN).
    Auth `dolfin_regenerate` = éditeur du jeu (package_update) / sysadmin."""
    tk.check_access('dolfin_regenerate', context, data_dict)
    pkg_id = data_dict.get('id')
    if not pkg_id:
        raise tk.ValidationError({'id': 'Identifiant de jeu requis.'})
    n = _trigger_regenerate(dict(context, ignore_auth=True), pkg_id)
    return {'regenerated': n}


dolfin = Blueprint('dolfin', __name__)


def _context():
    return {'user': g.user, 'auth_user_obj': getattr(g, 'userobj', None)}


def _require_edit(pkg_id):
    """Verifie que l'utilisateur peut editer ce dataset (admin/editeur d'org)."""
    tk.check_access('package_update', _context(), {'id': pkg_id})


def _first_csv_resource(pkg):
    for res in pkg.get('resources', []):
        if (res.get('format') or '').lower() == 'csv' and res.get('datastore_active'):
            return res
    return None


def _columns(pkg):
    res = _first_csv_resource(pkg)
    if not res:
        return None, []
    info = tk.get_action('datastore_search')(
        _context(), {'resource_id': res['id'], 'limit': 0})
    cols = [f['id'] for f in (info.get('fields') or []) if f.get('id') != '_id']
    return res['id'], cols


def _sample(resource_id, n=3):
    try:
        r = tk.get_action('datastore_search')(
            _context(), {'resource_id': resource_id, 'limit': n})
        recs = r.get('records') or []
        for rec in recs:
            rec.pop('_id', None)
        return recs
    except Exception:
        return []


@dolfin.route('/dataset/<id>/dolfin', methods=['GET'])
def page(id):
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
    except tk.ObjectNotFound:
        tk.abort(404, _('Jeu de donnees introuvable'))
    try:
        _require_edit(pkg['id'])
    except tk.NotAuthorized:
        tk.abort(403, _("Vous devez etre administrateur ou editeur de l'organisation."))
    resource_id, cols = _columns(pkg)
    current = ''
    for e in (pkg.get('extras') or []):
        if e.get('key') == 'dolfin_mapping':
            current = e.get('value') or ''
    return tk.render('dolfin/dolfin.html', extra_vars={
        'pkg_dict': pkg,
        'models_json': json.dumps([
            {'type': m['type'], 'titre': m['titre'], 'champs': m.get('champs', []),
             'geo': bool(m.get('geo')), 'desc': m.get('desc', '')}
            for m in _load_models()]),
        'columns_json': json.dumps(cols),
        'resource_id': resource_id or '',
        'current_mapping': current,
    })


@dolfin.route('/dataset/<id>/dolfin/data', methods=['GET'])
def data(id):
    """JSON pour une UI externe (portail d'instance) : modeles, colonnes, mapping courant."""
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
    except tk.ObjectNotFound:
        return jsonify({'error': 'Jeu introuvable'}), 404
    can_edit = True
    try:
        _require_edit(pkg['id'])
    except tk.NotAuthorized:
        can_edit = False
    resource_id, cols = _columns(pkg)
    current = ''
    for e in (pkg.get('extras') or []):
        if e.get('key') == 'dolfin_mapping':
            current = e.get('value') or ''
    return jsonify({
        'dataset': pkg.get('name'),
        'title': pkg.get('title'),
        'can_edit': can_edit,
        'resource_id': resource_id or '',
        'columns': cols,
        'current_mapping': current,
        'models': [
            {'type': m['type'], 'titre': m['titre'], 'champs': m.get('champs', []),
             'geo': bool(m.get('geo')), 'desc': m.get('desc', '')}
            for m in _load_models()],
    })


@dolfin.route('/dataset/<id>/dolfin/propose', methods=['GET', 'POST'])
def propose(id):
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
        _require_edit(pkg['id'])
    except tk.ObjectNotFound:
        return jsonify({'error': 'Jeu introuvable'}), 404
    except tk.NotAuthorized:
        return jsonify({'error': 'Non autorise'}), 403

    url = os.getenv('OLLAMA_URL') or tk.config.get('ckanext.dataload_router.ollama_url', '')
    if not url:
        return jsonify({'error': "IA non configuree (OLLAMA_URL absent)."})
    try:
        mi = int(request.form.get('model') or request.args.get('model') or 0)
        modele = _load_models()[mi]
    except Exception:
        return jsonify({'error': 'Modele DOLFIN invalide.'})
    resource_id, columns = _columns(pkg)
    if not columns:
        return jsonify({'error': "Aucune ressource CSV chargee dans le datastore pour ce jeu."})
    sample = _sample(resource_id, 3)
    champs = list(modele['champs'])
    geo = bool(modele.get('geo'))
    schema = {'id_field': '<colonne ou null>',
              'fields': {c: '<colonne ou null>' for c in champs}}
    if geo:
        schema['location'] = {'lon': '<colonne ou null>', 'lat': '<colonne ou null>'}
    sysp = ("Tu es un expert de l'harmonisation de donnees (Smart Data Models / NGSI-LD). "
            "On te donne les colonnes d'un jeu tabulaire et un modele cible ; tu proposes le "
            "meilleur mapping colonne vers champ. Reponds UNIQUEMENT par un objet JSON valide, "
            "sans texte ni balise. N'utilise que des noms de colonnes fournis, sinon null.")
    usr = ("Modele cible : " + modele['type'] + " (" + modele['titre'] + "). "
           + modele.get('desc', '') + "\n"
           + "Champs a mapper : " + ", ".join(champs)
           + (" ; geolocalisation par longitude/latitude." if geo else ".") + "\n"
           + "Colonnes disponibles : " + json.dumps(columns, ensure_ascii=False) + "\n"
           + "Echantillon : " + json.dumps(sample, ensure_ascii=False)[:2000] + "\n"
           + "Renvoie exactement cette structure (null si aucune colonne ne convient) :\n"
           + json.dumps(schema, ensure_ascii=False))
    body = json.dumps({
        "model": "qwen3.5:35b", "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": 600},
        "messages": [{"role": "system", "content": sysp},
                     {"role": "user", "content": usr}]}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/api/chat', data=body,
                                 headers={'Content-Type': 'application/json'})
    # Un seul essai court : derriere Traefik, une requete longue serait coupee (502).
    # Le 1er appel reveille le GPU via le proxy ; s'il n'est pas encore pret, on rend
    # la main tout de suite avec un message warming (l'utilisateur reessaie).
    warming = jsonify({'warming': True,
                       'message': "L'IA demarre le GPU (premiere requete, 1 a 2 min). "
                                  "Reessaie dans une minute."})
    content = None
    try:
        with urllib.request.urlopen(req, timeout=50) as r:
            d = json.loads(r.read())
        content = ((d.get('message') or {}).get('content') or '').strip()
    except Exception:
        return warming
    if not content:
        return warming
    txt = content.strip()
    a, b = txt.find('{'), txt.rfind('}')
    if a >= 0 and b > a:
        txt = txt[a:b + 1]
    try:
        prop = json.loads(txt)
    except Exception:
        return jsonify({'error': 'Reponse IA non exploitable : ' + content[:200]})
    valid = set(columns)

    def keep(v):
        return v if (isinstance(v, str) and v in valid) else None

    out = {'fields': {}}
    if keep(prop.get('id_field')):
        out['id_field'] = prop['id_field']
    for c in champs:
        v = keep((prop.get('fields') or {}).get(c))
        if v:
            out['fields'][c] = v
    if geo and isinstance(prop.get('location'), dict):
        lo, la = keep(prop['location'].get('lon')), keep(prop['location'].get('lat'))
        if lo and la:
            out['location'] = {'lon': lo, 'lat': la}
    return jsonify({'mapping': out, 'columns': columns, 'model': mi})


@dolfin.route('/dataset/<id>/dolfin/save', methods=['POST'])
def save(id):
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
        _require_edit(pkg['id'])
    except tk.ObjectNotFound:
        tk.abort(404, _('Jeu introuvable'))
    except tk.NotAuthorized:
        tk.abort(403, _('Non autorise'))
    mapping_raw = request.form.get('mapping') or ''
    try:
        json.loads(mapping_raw)
    except Exception as e:
        tk.h.flash_error(_('Mapping JSON invalide : ') + str(e))
        return redirect(tk.h.url_for('dolfin.page', id=id))
    extras = [e for e in (pkg.get('extras') or []) if e.get('key') != 'dolfin_mapping']
    extras.append({'key': 'dolfin_mapping', 'value': mapping_raw.strip()})
    tk.get_action('package_patch')(context, {'id': pkg['id'], 'extras': extras})
    # declencher l'harmonisation : re-soumettre les CSV (le hook regenere le NGSI-LD)
    n = 0
    for res in pkg.get('resources', []):
        if (res.get('format') or '').lower() == 'csv' and res.get('datastore_active'):
            try:
                tk.get_action('xloader_submit')(
                    context, {'resource_id': res['id'], 'ignore_hash': True})
                n += 1
            except Exception:
                pass
    tk.h.flash_success(
        _("Mapping DOLFIN enregistre. Harmonisation declenchee sur %d ressource(s) ; "
          "la ressource NGSI-LD apparaitra dans une minute.") % n)
    return redirect(tk.h.url_for('dataset.read', id=id))


@dolfin.route('/dataset/<id>/dolfin/regenerate', methods=['POST'])
def regenerate(id):
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
        _require_edit(pkg['id'])
    except tk.ObjectNotFound:
        tk.abort(404, _('Jeu introuvable'))
    except tk.NotAuthorized:
        tk.abort(403, _('Non autorise'))
    n = _trigger_regenerate(dict(context, ignore_auth=True), pkg['id'])
    tk.h.flash_success(
        _("Regeneration lancee sur %d ressource(s) ; les fichiers harmonises "
          "seront a jour dans une minute.") % n)
    return redirect(tk.h.url_for('dataset.read', id=id))


def _rag_metadata(columns, sample, filename):
    """Appelle le service dtz-rag pour proposer des métadonnées. None si indispo."""
    url = os.getenv('RAG_INTERNAL_URL', 'http://dtz-rag:8000')
    token = os.getenv('RAG_WEBHOOK_TOKEN', '')
    body = json.dumps({'columns': columns, 'sample': sample, 'filename': filename}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/generate/metadata', data=body,
                                 headers={'Content-Type': 'application/json', 'X-Dtz-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=170) as r:
            return json.loads(r.read())
    except Exception:
        return None


@dolfin.route('/dataset/<id>/metadata/ai', methods=['POST'])
def metadata_ai(id):
    """Complète les métadonnées VIDES du jeu avec une proposition IA (post-dépôt, à la
    demande de l'éditeur), puis renvoie vers le formulaire d'édition pour vérification."""
    context = _context()
    try:
        pkg = tk.get_action('package_show')(context, {'id': id})
        _require_edit(pkg['id'])
    except tk.ObjectNotFound:
        tk.abort(404, _('Jeu introuvable'))
    except tk.NotAuthorized:
        tk.abort(403, _('Non autorise'))
    resource_id, cols = _columns(pkg)
    if not cols:
        tk.h.flash_error(_("Aucune ressource chargee dans le datastore ; patientez le chargement du fichier."))
        return redirect(tk.h.url_for('dataset.read', id=id))
    prop = _rag_metadata(cols, _sample(resource_id, 8), pkg.get('name'))
    if not prop:
        tk.h.flash_error(_("IA indisponible ou GPU en demarrage ; reessayez dans une minute."))
        return redirect(tk.h.url_for('dataset.read', id=id))
    patch = {}
    if prop.get('title') and not (pkg.get('title') or '').strip():
        patch['title'] = prop['title']
    if prop.get('notes') and not (pkg.get('notes') or '').strip():
        patch['notes'] = prop['notes']
    if prop.get('tags') and not pkg.get('tags'):
        patch['tags'] = [{'name': t} for t in prop['tags']]
    existing = {e['key']: e['value'] for e in pkg.get('extras', [])}
    changed = False
    for k, v in (prop.get('extras') or {}).items():
        if not (existing.get(k) or '').strip():
            existing[k] = v
            changed = True
    if changed:
        patch['extras'] = [{'key': k, 'value': v} for k, v in existing.items()]
    if patch:
        patch['id'] = pkg['id']
        tk.get_action('package_patch')(context, patch)
        tk.h.flash_success(_("Metadonnees completees par l'IA. Verifiez et ajustez si besoin."))
    else:
        tk.h.flash_success(_("Aucun champ vide a completer."))
    return redirect(tk.h.url_for('dataset.edit', id=id))


def _require_sysadmin():
    tk.check_access('sysadmin', _context(), {})


@dolfin.route('/dolfin/models', methods=['GET'])
def models_list():
    try:
        _require_sysadmin()
    except tk.NotAuthorized:
        tk.abort(403, _('Reserve aux administrateurs.'))
    return tk.render('dolfin/models_list.html', extra_vars={'models': _load_models()})


@dolfin.route('/dolfin/model/<type_>', methods=['GET'])
def model_view(type_):
    """Vue PUBLIQUE en lecture seule d'un modèle DOLFIN (documentation du schéma pivot :
    type, description, concepts). Sert de cible au lien « Voir le modèle » des fiches.
    N'expose que les champs publics (pas d'auteur ni d'historique)."""
    m = model_by_type(type_)
    if not m:
        tk.abort(404, _('Modèle DOLFIN inconnu.'))
    public = {
        'type': m.get('type'), 'titre': m.get('titre'), 'desc': m.get('desc'),
        'champs': m.get('champs') or [], 'geo': bool(m.get('geo')), 'builtin': bool(m.get('builtin')),
    }
    return tk.render('dolfin/model_view.html', extra_vars={'model': public})


@dolfin.route('/dolfin/models/new', methods=['GET'])
def model_new():
    try:
        _require_sysadmin()
    except tk.NotAuthorized:
        tk.abort(403, _('Reserve aux administrateurs.'))
    return tk.render('dolfin/model_edit.html', extra_vars={'model': None})


@dolfin.route('/dolfin/models/<slug>', methods=['GET'])
def model_edit(slug):
    try:
        _require_sysadmin()
    except tk.NotAuthorized:
        tk.abort(403, _('Reserve aux administrateurs.'))
    m = _get_model(slug)
    if not m:
        tk.abort(404, _('Modele introuvable'))
    return tk.render('dolfin/model_edit.html', extra_vars={'model': m})


@dolfin.route('/dolfin/models/save', methods=['POST'])
def model_save():
    try:
        _require_sysadmin()
    except tk.NotAuthorized:
        tk.abort(403, _('Reserve aux administrateurs.'))
    f = request.form
    titre = (f.get('titre') or '').strip()
    type_ = (f.get('type') or '').strip()
    slug = _slugify(f.get('slug') or type_ or titre)
    if not type_ or not titre:
        tk.h.flash_error(_('Le type et le titre sont obligatoires.'))
        return redirect(tk.h.url_for('dolfin.model_new'))
    source = f.get('dolfin') or ''
    ok, msg, champs_parsed, geo_parsed = _compile_dolfin(source)
    if not ok:
        tk.h.flash_error(msg)
        return redirect(tk.h.url_for('dolfin.model_edit', slug=slug))
    champs = [c.strip() for c in (f.get('champs') or '').split(',') if c.strip()]
    if not champs and champs_parsed:
        champs = champs_parsed
    data = {
        'type': type_, 'titre': titre, 'desc': (f.get('desc') or '').strip(),
        'champs': champs, 'geo': (f.get('geo') == 'on') or geo_parsed,
        'dolfin': source,
    }
    with open(os.path.join(_models_dir(), slug + '.json'), 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tk.h.flash_success(_('Modele DOLFIN « %s » enregistre. ') % titre + msg)
    return redirect(tk.h.url_for('dolfin.models_list'))


@dolfin.route('/dolfin/models/<slug>/delete', methods=['POST'])
def model_delete(slug):
    try:
        _require_sysadmin()
    except tk.NotAuthorized:
        tk.abort(403, _('Reserve aux administrateurs.'))
    p = os.path.join(_models_dir(), slug + '.json')
    if os.path.exists(p):
        os.unlink(p)
        tk.h.flash_success(_('Modele supprime.'))
    return redirect(tk.h.url_for('dolfin.models_list'))


def get_blueprint():
    return dolfin

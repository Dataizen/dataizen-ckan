"""
Vues pour les outils d'administration CKAN
"""
import logging
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, abort
from ckan.plugins.toolkit import (
    get_action, check_access, NotAuthorized, _, c, g, h
)
try:
    from flask_wtf.csrf import generate_csrf
except ImportError:
    generate_csrf = None

log = logging.getLogger(__name__)

admin_tools = Blueprint('admin_tools', __name__, url_prefix='/admin-tools')


@admin_tools.before_request
def before_request():
    """Vérifier que l'utilisateur est admin avant chaque requête"""
    try:
        check_access('sysadmin', {'user': g.user}, {})
    except NotAuthorized:
        abort(403, _('Need to be system administrator to administer'))


@admin_tools.route('/bulk-delete')
def bulk_delete():
    """Page de suppression en masse"""
    return render_template('admin/bulk_delete.html')


@admin_tools.route('/sync')
def sync():
    """Page de synchronisation (page_csrf_token évite d'écraser le callable csrf_token() du base template)"""
    page_csrf_token = generate_csrf() if generate_csrf else None
    return render_template('admin/sync.html', page_csrf_token=page_csrf_token)


@admin_tools.route('/harvest-status')
def harvest_status():
    """Page d'état des moissonnages"""
    return render_template('admin/harvest_status.html')


@admin_tools.route('/request-log')
def request_log():
    """Page de suivi des requêtes CKAN (path, IP, User-Agent, etc.)"""
    return render_template('admin/request_log.html')


def get_blueprint():
    """Retourne le blueprint pour enregistrement dans CKAN"""
    return admin_tools


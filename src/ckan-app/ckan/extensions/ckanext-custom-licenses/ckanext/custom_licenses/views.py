"""
Vues Flask pour l'extension custom_licenses.
Permet d'accéder à license_list via GET en plus de POST.
"""
import logging
from flask import Blueprint, jsonify, request
from ckan.plugins import toolkit as tk
from ckan import model

log = logging.getLogger(__name__)

custom_licenses = Blueprint('custom_licenses', __name__)


@custom_licenses.route('/api/3/action/license_list', methods=['GET', 'POST'])
def license_list_endpoint():
    """
    Endpoint personnalisé pour license_list qui accepte GET et POST.
    """
    try:
        # Importer ici pour éviter les imports circulaires
        from ckanext.custom_licenses.plugin import custom_license_list
        
        # Créer un contexte pour l'action
        context = {
            'model': model,
            'session': model.Session,
            'user': tk.c.user if hasattr(tk.c, 'user') else None,
        }
        
        # Si c'est une requête POST, essayer de récupérer les données du body
        data_dict = {}
        if request.method == 'POST':
            if request.is_json:
                data_dict = request.get_json() or {}
            elif request.form:
                data_dict = dict(request.form)
        
        # Appeler l'action personnalisée
        result = custom_license_list(context, data_dict)
        
        # Retourner le résultat au format JSON CKAN
        return jsonify({
            'help': 'Return the list of licenses available for datasets',
            'success': True,
            'result': result
        })
    except Exception as e:
        log.error(f"Erreur dans license_list_endpoint: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({
            'help': 'Return the list of licenses available for datasets',
            'success': False,
            'error': {
                'message': str(e),
                '__type': 'Validation Error'
            }
        }), 400


def get_blueprint():
    """
    Retourne le blueprint pour enregistrement dans CKAN.
    """
    return custom_licenses


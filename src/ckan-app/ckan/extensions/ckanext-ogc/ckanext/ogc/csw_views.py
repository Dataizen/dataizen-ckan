"""
CSW (Catalogue Service for the Web) endpoint using pycsw
"""
import logging
import os
from flask import Blueprint, request, Response

log = logging.getLogger(__name__)

csw = Blueprint('csw', __name__, url_prefix='/csw')


@csw.route('', methods=['GET', 'POST'])
def csw_endpoint():
    """
    CSW endpoint that proxies requests to pycsw
    """
    try:
        from pycsw import server
        from pycsw.core import config
        
        # Get pycsw configuration path
        pycsw_config_path = os.getenv('PYCSW_CONFIG', '/srv/app/pycsw/default.cfg')
        
        if not os.path.exists(pycsw_config_path):
            log.error(f"Configuration pycsw non trouvée: {pycsw_config_path}")
            return Response(
                'CSW service not configured. Please configure pycsw first.',
                status=503,
                mimetype='text/plain'
            )
        
        # Create WSGI environment from Flask request
        # pycsw reads parameters from QUERY_STRING for GET and from wsgi.input for POST
        environ = {
            'REQUEST_METHOD': request.method,
            'PATH_INFO': request.path,
            'QUERY_STRING': request.query_string.decode('utf-8'),
            'CONTENT_TYPE': request.content_type or '',
            'CONTENT_LENGTH': str(len(request.data)),
            'wsgi.input': request.stream,
            'wsgi.version': (1, 0),
            'wsgi.url_scheme': request.scheme,
            'wsgi.errors': None,
            'SERVER_NAME': request.host.split(':')[0],
            'SERVER_PORT': str(request.host.split(':')[1] if ':' in request.host else ('443' if request.scheme == 'https' else '80')),
            'HTTP_HOST': request.host,
        }
        
        # Add headers
        for key, value in request.headers:
            environ[f'HTTP_{key.upper().replace("-", "_")}'] = value
        
        # Create pycsw server instance
        # rtconfig can be a file path or a StaticContext object
        # Passing the file path directly is the simplest approach
        csw_server = server.Csw(rtconfig=pycsw_config_path, env=environ)
        
        # Process request using dispatch_wsgi() for WSGI requests
        # dispatch_wsgi() properly initializes requesttype and handles WSGI environ
        content = csw_server.dispatch_wsgi()
        
        # Return response
        return Response(
            content,
            mimetype='application/xml; charset=UTF-8',
            headers={'Content-Type': 'application/xml; charset=UTF-8'}
        )
        
    except ImportError:
        log.error("pycsw non installé. Installez-le avec: pip install pycsw")
        return Response(
            'CSW service not available. pycsw is not installed.',
            status=503,
            mimetype='text/plain'
        )
    except Exception as e:
        log.error(f"Erreur lors du traitement de la requête CSW: {e}")
        import traceback
        log.error(traceback.format_exc())
        return Response(
            f'CSW service error: {str(e)}',
            status=500,
            mimetype='text/plain'
        )


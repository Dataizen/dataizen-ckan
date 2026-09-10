"""
Filtre de logging personnalisé pour filtrer les logs 404 de datastore_search
tout en conservant les autres logs INFO utiles (import, mapfiles, etc.)
"""
import logging


class DatastoreSearch404Filter(logging.Filter):
    """
    Filtre qui supprime les logs INFO contenant "404" et "/api/action/datastore_search"
    mais conserve tous les autres logs (y compris les autres 404 et les erreurs)
    """
    
    def filter(self, record):
        """
        Retourne False pour filtrer le log, True pour le garder
        
        On filtre uniquement les logs INFO qui contiennent :
        - "404" ET
        - "/api/action/datastore_search"
        """
        # Ne filtrer que les logs INFO
        if record.levelno != logging.INFO:
            return True
        
        # Ne filtrer que les logs du logger flask_app
        if 'flask_app' not in record.name:
            return True
        
        # Vérifier si le message contient "404" et "datastore_search"
        message = record.getMessage()
        if '404' in message and '/api/action/datastore_search' in message:
            return False  # Filtrer ce log
        
        # Garder tous les autres logs
        return True



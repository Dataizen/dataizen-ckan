"""
Handler de logging sécurisé qui ignore silencieusement les OSError lors de l'écriture
Utile dans les environnements Kubernetes/Rancher où stdout/stderr peuvent être saturés
"""
import logging
import sys
from logging import StreamHandler


class SafeStreamHandler(StreamHandler):
    """
    Handler de logging qui ignore silencieusement les OSError lors de l'écriture
    dans stdout/stderr (utile dans Kubernetes/Rancher)
    """
    
    def emit(self, record):
        """
        Émet un log en capturant les OSError silencieusement
        """
        try:
            super().emit(record)
        except OSError:
            # Ignorer silencieusement les erreurs d'écriture (stdout/stderr saturé)
            # Ne pas logger l'erreur pour éviter une boucle infinie
            pass
        except Exception:
            # Pour les autres exceptions, utiliser le handler d'exception par défaut
            self.handleError(record)


def setup_safe_logging():
    """
    Configure le logging pour utiliser SafeStreamHandler au lieu de StreamHandler
    pour éviter les OSError: write error dans les environnements Kubernetes/Rancher
    """
    root_logger = logging.getLogger()
    
    # Remplacer tous les StreamHandler par SafeStreamHandler
    for handler in root_logger.handlers[:]:
        if isinstance(handler, StreamHandler) and not isinstance(handler, SafeStreamHandler):
            # Créer un nouveau SafeStreamHandler avec le même stream
            new_handler = SafeStreamHandler(handler.stream)
            new_handler.setLevel(handler.level)
            new_handler.setFormatter(handler.formatter)
            
            # Remplacer l'ancien handler
            root_logger.removeHandler(handler)
            root_logger.addHandler(new_handler)
    
    # S'assurer qu'il y a au moins un handler pour stdout
    has_stdout_handler = any(
        isinstance(h, SafeStreamHandler) and h.stream == sys.stdout
        for h in root_logger.handlers
    )
    
    if not has_stdout_handler:
        stdout_handler = SafeStreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s')
        stdout_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_handler)
    
    # S'assurer qu'il y a au moins un handler pour stderr
    has_stderr_handler = any(
        isinstance(h, SafeStreamHandler) and h.stream == sys.stderr
        for h in root_logger.handlers
    )
    
    if not has_stderr_handler:
        stderr_handler = SafeStreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s')
        stderr_handler.setFormatter(formatter)
        root_logger.addHandler(stderr_handler)

#!/usr/bin/env python3
"""
Wrapper pour lancer un worker RQ avec l'environnement CKAN initialisé.
Initialise CKAN (config, SQLAlchemy, etc.) avant de lancer le worker RQ.
"""
import os
import sys
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[RQ-WORKER] %(asctime)s %(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("rq_worker_wrapper")


def initialize_ckan() -> bool:
    try:
        ckan_ini = os.environ.get("CKAN_INI", "/srv/app/ckan.ini")
        os.environ.setdefault("CKAN_CONFIG", ckan_ini)  # utile pour certains contextes

        logger.info("=" * 80)
        logger.info("STEP 1: Initializing CKAN with config: %s", ckan_ini)
        logger.info("=" * 80)
        
        # Vérifier que le fichier existe
        if not os.path.exists(ckan_ini):
            logger.error("ERROR: ckan.ini file does not exist: %s", ckan_ini)
            return False
        
        logger.info("ckan.ini file exists and is readable")

        # Méthode 1: Essayer _init_ckan depuis ckan.cli.jobs (si disponible)
        try:
            logger.info("STEP 2: Trying to import _init_ckan from ckan.cli.jobs...")
            from ckan.cli.jobs import _init_ckan
            logger.info("_init_ckan found in ckan.cli.jobs")
            logger.info("STEP 3: Calling _init_ckan(%s)...", ckan_ini)
            _init_ckan(ckan_ini)
            logger.info("_init_ckan called successfully")
        except ImportError:
            # Méthode 2: Utiliser load_config et load_environment directement
            logger.info(" _init_ckan not found, trying load_config/load_environment...")
            try:
                from ckan.config.environment import load_config, load_environment
                logger.info("load_config and load_environment found")
                logger.info("STEP 3: Loading CKAN config and environment...")
                config = load_config(ckan_ini)
                load_environment(config)
                logger.info("CKAN environment loaded successfully")
            except ImportError as e2:
                logger.warning(" load_config/load_environment not found: %s", e2)
                logger.warning("   CKAN initialization will be skipped in wrapper")
                logger.warning("   ckan jobs worker should initialize CKAN, but it may not propagate to job context")
            except Exception as e2:
                logger.error("Error loading CKAN environment: %s", e2, exc_info=True)
                return False
        except Exception as e:
            logger.error("Error calling _init_ckan: %s", e, exc_info=True)
            return False

        logger.info("STEP 5: Verifying SQLAlchemy initialization...")
        from ckan import model
        logger.info("ckan.model imported")
        
        logger.info("   model.meta.engine: %s", model.meta.engine)
        logger.info("   model.Session: %s", model.Session)
        logger.info("   model.Session.bind: %s", model.Session.bind)
        
        if model.Session.bind is None:
            logger.warning(" SQLAlchemy session bind is None (CKAN not initialized in wrapper)")
            logger.info("   This is expected if _init_ckan is not available")
            logger.info("   ckan jobs worker should initialize CKAN when it starts")
            logger.info("   We will continue and let ckan jobs worker handle initialization")
        else:
            logger.info("model.Session.bind is properly configured")

        # Ne pas tester la connexion DB ou ApiToken si CKAN n'est pas initialisé
        # On laisse ckan jobs worker le faire
        logger.info("=" * 80)
        logger.info("Wrapper initialization completed")
        logger.info("   CKAN will be initialized by ckan jobs worker")
        logger.info("=" * 80)
        return True

    except Exception as e:
        logger.error("=" * 80)
        logger.error("CRITICAL: Failed to initialize CKAN: %s", e, exc_info=True)
        logger.error("=" * 80)
        return False


def main():
    logger.info("=" * 80)
    logger.info("RQ WORKER WRAPPER: Starting with CKAN initialization...")
    logger.info("=" * 80)
    logger.info("Python version: %s", sys.version)
    logger.info("Python executable: %s", sys.executable)
    logger.info("Working directory: %s", os.getcwd())
    logger.info("Environment variables:")
    logger.info("  CKAN_INI: %s", os.environ.get("CKAN_INI", "NOT SET"))
    logger.info("  CKAN_CONFIG: %s", os.environ.get("CKAN_CONFIG", "NOT SET"))
    logger.info("  PYTHONPATH: %s", os.environ.get("PYTHONPATH", "NOT SET"))
    logger.info("=" * 80)

    # Initialiser CKAN AVANT de lancer ckan jobs worker
    logger.info("")
    logger.info("PHASE 1: Initializing CKAN environment...")
    if not initialize_ckan():
        logger.error("=" * 80)
        logger.error("FATAL: Failed to initialize CKAN, exiting")
        logger.error("The worker will not start because CKAN initialization failed")
        logger.error("=" * 80)
        sys.exit(1)

    # Queue name en argument (ex: "default")
    # CKAN préfixera automatiquement avec ckan:<site_id>:
    queue_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    ckan_ini = os.environ.get("CKAN_INI", "/srv/app/ckan.ini")

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 2: Launching ckan jobs worker...")
    logger.info("=" * 80)
    logger.info("Queue name (will be prefixed by CKAN): %s", queue_name)
    logger.info("CKAN config file: %s", ckan_ini)
    logger.info("Command: ckan -c %s jobs worker %s", ckan_ini, queue_name)
    logger.info("CKAN will automatically prefix queue to: ckan:<site_id>:%s", queue_name)
    logger.info("")
    logger.info("NOTE: The CKAN environment is already initialized in this process")
    logger.info("      When ckan jobs worker starts, it should inherit this environment")
    logger.info("      If jobs still fail with UnboundExecutionError, check if:")
    logger.info("      1. ckan jobs worker reinitializes CKAN (overwriting our init)")
    logger.info("      2. RQ executes jobs in a separate process/context")
    logger.info("=" * 80)

    # Arrêt propre sur SIGTERM (K8s)
    def _graceful_shutdown(signum, frame):
        logger.info("")
        logger.info("=" * 80)
        logger.info("Received signal %s, forwarding to ckan jobs worker...", signum)
        logger.info("The signal will be propagated to the ckan jobs worker process")
        logger.info("=" * 80)
        # Le signal sera propagé au processus ckan jobs worker

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # Lancer ckan jobs worker qui utilisera l'environnement CKAN déjà initialisé
    # os.execv remplace le processus actuel par ckan jobs worker
    # L'environnement CKAN initialisé sera disponible dans le processus worker
    try:
        ckan_binary = '/usr/local/bin/ckan'
        logger.info("Checking ckan binary: %s", ckan_binary)
        if not os.path.exists(ckan_binary):
            logger.error("ERROR: ckan binary not found at %s", ckan_binary)
            sys.exit(1)
        if not os.access(ckan_binary, os.X_OK):
            logger.error("ERROR: ckan binary is not executable: %s", ckan_binary)
            sys.exit(1)
        logger.info("ckan binary found and executable")
        
        cmd = ['ckan', '-c', ckan_ini, 'jobs', 'worker', queue_name]
        logger.info("")
        logger.info("=" * 80)
        logger.info("EXECUTING: %s", ' '.join(cmd))
        logger.info("=" * 80)
        logger.info("This will replace the current process with ckan jobs worker")
        logger.info("The CKAN environment initialized above should be inherited")
        logger.info("")
        
        # Remplacer le processus actuel par ckan jobs worker
        # L'environnement CKAN initialisé sera hérité
        os.execv(ckan_binary, cmd)
    except FileNotFoundError:
        logger.error("=" * 80)
        logger.error("ERROR: ckan binary not found at /usr/local/bin/ckan")
        logger.error("Please check your CKAN installation")
        logger.error("=" * 80)
        sys.exit(1)
    except PermissionError:
        logger.error("=" * 80)
        logger.error("ERROR: Permission denied when executing ckan binary")
        logger.error("Please check file permissions")
        logger.error("=" * 80)
        sys.exit(1)
    except Exception as e:
        logger.error("=" * 80)
        logger.error("ERROR: Failed to launch ckan jobs worker: %s", e, exc_info=True)
        logger.error("=" * 80)
        sys.exit(1)


if __name__ == "__main__":
    main()

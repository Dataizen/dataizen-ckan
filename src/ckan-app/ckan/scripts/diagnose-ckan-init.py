#!/usr/bin/env python3
"""
Script de diagnostic pour vérifier l'initialisation CKAN dans le worker RQ.
À exécuter dans le contexte du worker pour diagnostiquer pourquoi l'environnement CKAN n'est pas initialisé.
"""
import sys
import os
import traceback

print("=" * 80)
print("DIAGNOSTIC: Vérification de l'initialisation CKAN dans le worker RQ")
print("=" * 80)
print()

# 1. Vérifier les variables d'environnement
print("1. Variables d'environnement:")
print(f"   CKAN_INI: {os.environ.get('CKAN_INI', 'NON DÉFINIE')}")
print(f"   CKAN_CONFIG: {os.environ.get('CKAN_CONFIG', 'NON DÉFINIE')}")
print(f"   PYTHONPATH: {os.environ.get('PYTHONPATH', 'NON DÉFINIE')}")
print()

# 2. Vérifier si le fichier ckan.ini existe
ckan_ini = os.environ.get('CKAN_INI', '/srv/app/ckan.ini')
print(f"2. Fichier ckan.ini:")
print(f"   Chemin: {ckan_ini}")
print(f"   Existe: {os.path.exists(ckan_ini)}")
if os.path.exists(ckan_ini):
    print(f"   Lisible: {os.access(ckan_ini, os.R_OK)}")
    print(f"   Taille: {os.path.getsize(ckan_ini)} bytes")
print()

# 3. Vérifier l'import de CKAN
print("3. Import de CKAN:")
try:
    import ckan
    print(f"   ckan importé: {ckan.__file__}")
except Exception as e:
    print(f"   Erreur import ckan: {e}")
    traceback.print_exc()
    sys.exit(1)
print()

# 4. Vérifier l'import de model
print("4. Import de ckan.model:")
try:
    from ckan import model
    print(f"   model importé: {model.__file__}")
except Exception as e:
    print(f"   Erreur import model: {e}")
    traceback.print_exc()
    sys.exit(1)
print()

# 5. Vérifier l'état de SQLAlchemy
print("5. État de SQLAlchemy:")
try:
    print(f"   model.meta.engine: {model.meta.engine}")
    print(f"   model.Session: {model.Session}")
    print(f"   model.Session.bind: {model.Session.bind}")
    
    if model.Session.bind is None:
        print("   PROBLÈME: model.Session.bind est None!")
        print("      L'environnement CKAN n'est pas initialisé")
    else:
        print("   model.Session.bind est configuré")
        
    # Vérifier si on peut accéder à la base
    if model.Session.bind is not None:
        try:
            from sqlalchemy import text
            with model.Session.bind.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                print(f"   Connexion à la base OK: {result.scalar()}")
        except Exception as e:
            print(f"    Erreur connexion base: {e}")
            
except Exception as e:
    print(f"   Erreur vérification SQLAlchemy: {e}")
    traceback.print_exc()
print()

# 6. Vérifier l'import de config
print("6. Import de ckan.config:")
try:
    from ckan.common import config
    print(f"   config importé")
    print(f"   ckan.site_url: {config.get('ckan.site_url', 'NON DÉFINI')}")
    print(f"   sqlalchemy.url: {config.get('sqlalchemy.url', 'NON DÉFINI')[:50]}...")
except Exception as e:
    print(f"   Erreur import config: {e}")
    traceback.print_exc()
print()

# 7. Vérifier l'import de get_user_from_token
print("7. Import de ckan.lib.api_token:")
try:
    from ckan.lib.api_token import get_user_from_token
    print(f"   get_user_from_token importé")
except Exception as e:
    print(f"   Erreur import get_user_from_token: {e}")
    traceback.print_exc()
print()

# 8. Test d'accès à ApiToken (le point de défaillance)
print("8. Test d'accès à model.ApiToken:")
try:
    if model.Session.bind is None:
        print("    Skipping (Session.bind est None)")
    else:
        # Essayer d'accéder à ApiToken
        try:
            token_count = model.Session.query(model.ApiToken).count()
            print(f"   Accès à ApiToken OK: {token_count} tokens trouvés")
        except Exception as e:
            print(f"   Erreur accès ApiToken: {e}")
            print(f"      Type: {type(e).__name__}")
            traceback.print_exc()
except Exception as e:
    print(f"   Erreur test ApiToken: {e}")
    traceback.print_exc()
print()

# 9. Vérifier comment initialiser CKAN correctement
print("9. Vérification de l'initialisation CKAN:")
try:
    # Méthode 1: Essayer _init_ckan depuis ckan.cli.jobs
    try:
        from ckan.cli.jobs import _init_ckan
        print(f"   _init_ckan trouvé dans ckan.cli.jobs")
        print(f"   Tentative d'initialisation avec {ckan_ini}...")
        _init_ckan(ckan_ini)
        print(f"   _init_ckan appelé avec succès")
    except ImportError:
        print(f"    _init_ckan non trouvé dans ckan.cli.jobs, essai méthode alternative...")
        
        # Méthode 2: Utiliser load_config et load_environment directement
        try:
            from ckan.config.environment import load_config, load_environment
            print(f"   load_config et load_environment importés")
            print(f"   Tentative d'initialisation avec {ckan_ini}...")
            
            config = load_config(ckan_ini)
            print(f"   Config chargée")
            print(f"      ckan.site_url: {config.get('ckan.site_url', 'NON DÉFINI')}")
            print(f"      sqlalchemy.url: {config.get('sqlalchemy.url', 'NON DÉFINI')[:50]}...")
            
            load_environment(config)
            print(f"   Environnement chargé")
        except Exception as e:
            print(f"   Erreur lors de load_config/load_environment: {e}")
            traceback.print_exc()
            raise
    
    # Vérifier si ça a changé quelque chose
    print(f"   model.Session.bind après initialisation: {model.Session.bind}")
    print(f"   model.meta.engine après initialisation: {model.meta.engine}")
    
    if model.Session.bind is not None:
        print(f"   L'initialisation a fonctionné!")
        # Test d'accès à ApiToken
        try:
            count = model.Session.query(model.ApiToken).count()
            print(f"   Accès à ApiToken OK: {count} tokens")
        except Exception as e:
            print(f"   Erreur accès ApiToken après init: {e}")
            traceback.print_exc()
    else:
        print(f"   L'initialisation n'a pas configuré Session.bind")
        print(f"      Il faut vérifier pourquoi load_environment n'a pas configuré SQLAlchemy")
        
except Exception as e:
    print(f"   Erreur lors de l'initialisation: {e}")
    traceback.print_exc()
print()

# 10. Vérifier le contexte RQ
print("10. Vérification du contexte RQ:")
try:
    from rq import get_current_job
    job = get_current_job()
    if job:
        print(f"   Job RQ actif: {job.id}")
        print(f"      Queue: {job.origin}")
        print(f"      Function: {job.func_name}")
    else:
        print(f"    Aucun job RQ actif (normal si exécuté en dehors d'un job)")
except Exception as e:
    print(f"    Erreur vérification RQ: {e}")
print()

print("=" * 80)
print("FIN DU DIAGNOSTIC")
print("=" * 80)


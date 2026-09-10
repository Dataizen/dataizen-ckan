#!/usr/bin/env bash
set -euo pipefail

echo "Prepare CKAN data dirs..."
#mkdir -p /var/lib/ckan/webassets /var/lib/ckan/storage /var/lib/ckan/resources

# Détermine un UID/GID fiables (numériques) sans sudo
CKAN_UID="$(id -u ckan 2>/dev/null || echo 503)"
CKAN_GID="$(id -g ckan 2>/dev/null || echo 502)"

echo "Fixing permissions for /var/lib/ckan..."
chown -R "${CKAN_UID}:${CKAN_GID}" /var/lib/ckan || true

echo "Initializing harvest database (idempotent)..."

# Vérifier et créer les tables harvest si elles n'existent pas
export PGUSER="${POSTGRES_USER:-ckan}"
export PGPASSWORD="${POSTGRES_PASSWORD:-ckan}"
export PGHOST="${POSTGRES_HOST:-db}"
export PGDATABASE="${POSTGRES_DB:-ckan}"

psql <<'SQL'
-- Créer harvest_source si elle n'existe pas
CREATE TABLE IF NOT EXISTS harvest_source (
    id VARCHAR(36) PRIMARY KEY,
    url VARCHAR(2000) NOT NULL,
    title VARCHAR(2000),
    description TEXT,
    config TEXT,
    created TIMESTAMP,
    type VARCHAR(50),
    active BOOLEAN DEFAULT TRUE,
    user_id VARCHAR(36),
    publisher_id VARCHAR(36),
    frequency VARCHAR(50),
    next_run TIMESTAMP
);

-- Créer harvest_job si elle n'existe pas
CREATE TABLE IF NOT EXISTS harvest_job (
    id VARCHAR(36) PRIMARY KEY,
    harvest_source_id VARCHAR(36) REFERENCES harvest_source(id),
    status VARCHAR(50),
    created TIMESTAMP,
    gather_started TIMESTAMP,
    gather_finished TIMESTAMP,
    finished TIMESTAMP
);
SQL

# Safe même si déjà à jour ; n'empêche pas le démarrage si un warning survient
ckan -c /srv/app/ckan.ini db upgrade -p harvest || true

echo "Dans init-harvest.sh, Préparation idempotente des FK Harvest (rename ou create)…"

export PGUSER="${POSTGRES_USER:-ckan}"
export PGPASSWORD="${POSTGRES_PASSWORD:-ckan}"
export PGHOST="${POSTGRES_HOST:-db}"
export PGDATABASE="${POSTGRES_DB:-ckan}"

psql <<'SQL'
DO $$
DECLARE
  job_exists bool;
  obj_exists bool;
  col_exists bool;
  found_name text;
  expected   text := 'harvest_object_harvest_job_id_fkey';
BEGIN
  -- Vérifie que les tables existent
  SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='harvest_job') INTO job_exists;
  SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='harvest_object') INTO obj_exists;

  IF NOT (job_exists AND obj_exists) THEN
    RAISE NOTICE 'Tables Harvest non présentes — FK ignorée pour le moment.';
    RETURN;
  END IF;

  -- Vérifie que la colonne harvest_job_id existe
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='harvest_object' AND column_name='harvest_job_id'
  ) INTO col_exists;

  IF NOT col_exists THEN
    RAISE NOTICE 'Colonne harvest_job_id absente — FK ignorée.';
    RETURN;
  END IF;

  -- Cherche s'il existe déjà une FK similaire
  SELECT c.conname INTO found_name
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_class r ON r.oid = c.confrelid
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
  JOIN pg_attribute ar ON ar.attrelid = c.confrelid AND ar.attnum = ANY (c.confkey)
  WHERE c.contype = 'f'
    AND t.relname = 'harvest_object'
    AND r.relname = 'harvest_job'
    AND a.attname = 'harvest_job_id'
    AND ar.attname = 'id'
  LIMIT 1;

  IF found_name IS NOT NULL THEN
    IF found_name <> expected THEN
      EXECUTE format('ALTER TABLE public.harvest_object RENAME CONSTRAINT %I TO %I', found_name, expected);
      RAISE NOTICE 'Contrainte renommée de % vers %', found_name, expected;
    ELSE
      RAISE NOTICE 'Contrainte % déjà en place', found_name;
    END IF;
  ELSE
    EXECUTE format($f$
      ALTER TABLE public.harvest_object
      ADD CONSTRAINT %I
      FOREIGN KEY (harvest_job_id)
      REFERENCES public.harvest_job(id)
      ON DELETE RESTRICT
    $f$, expected);
    RAISE NOTICE 'Contrainte % créée', expected;
  END IF;
END$$;
SQL

# Configuration Redis pour les consumers (si non définie, utiliser la valeur par défaut)
if [ -z "${CKAN_REDIS_URL:-}" ]; then
  export CKAN_REDIS_URL="redis://redis:6379/0"
  echo "CKAN_REDIS_URL non définie, utilisation de la valeur par défaut: ${CKAN_REDIS_URL}"
fi

# Configurer Redis dans ckan.ini pour les consumers de moissonnage
if ! sudo grep -q '^ckan.redis.url' /srv/app/ckan.ini; then
  echo "Configuration de Redis dans ckan.ini..."
  echo "ckan.redis.url = ${CKAN_REDIS_URL}" | sudo tee -a /srv/app/ckan.ini
else
  echo "Mise à jour de la configuration Redis dans ckan.ini..."
  sudo sed -i "s|^ckan.redis.url.*|ckan.redis.url = ${CKAN_REDIS_URL}|" /srv/app/ckan.ini
fi

# Configuration de la message queue pour le moissonnage (pour supprimer l'avertissement)
# CKAN utilise Redis par défaut, mais cette option peut être nécessaire pour certaines versions
if ! sudo grep -q '^ckan.harvest.mq.hostname' /srv/app/ckan.ini; then
  # Extraire le hostname de l'URL Redis (ex: redis://redis:6379/0 -> redis)
  REDIS_HOST=$(echo "${CKAN_REDIS_URL}" | sed -n 's|redis://\([^:/]*\).*|\1|p')
  if [ -z "$REDIS_HOST" ]; then
    REDIS_HOST="redis"
  fi
  echo "Configuration de ckan.harvest.mq.hostname: ${REDIS_HOST}"
  echo "ckan.harvest.mq.hostname = ${REDIS_HOST}" | sudo tee -a /srv/app/ckan.ini
fi

# Vérifier que les plugins de moissonnage sont bien configurés
echo "Vérification de la configuration des plugins de moissonnage..."
if sudo grep -q 'ckan_harvester' /srv/app/ckan.ini; then
  echo "Plugin ckan_harvester trouvé dans la configuration"
else
  echo "Plugin ckan_harvester non trouvé dans la configuration"
fi

# Les consumers de moissonnage sont gérés par supervisor (harvester.conf)
# Le worker xloader est géré par supervisor (xloader.conf)

echo "Configuration de la base de données harvest terminée"
echo "Configuration Redis terminée: ${CKAN_REDIS_URL}"

echo "Configuring API key settings (legacy + JWT)..."

# Activer l'usage des API keys classiques
sudo grep -q '^ckan.auth.create_default_api_keys' /srv/app/ckan.ini || \
  echo "ckan.auth.create_default_api_keys = true" | sudo tee -a /srv/app/ckan.ini

sudo grep -q '^ckan.auth.api_token.allow_legacy_keys' /srv/app/ckan.ini || \
  echo "ckan.auth.api_token.allow_legacy_keys = true" | sudo tee -a /srv/app/ckan.ini

sudo grep -q '^ckan.auth.api_key_expires_in' /srv/app/ckan.ini || \
  echo "ckan.auth.api_key_expires_in = 0" | sudo tee -a /srv/app/ckan.ini

sudo grep -q '^ckan.auth.api_key_header' /srv/app/ckan.ini || \
  echo "ckan.auth.api_key_header = Authorization" | sudo tee -a /srv/app/ckan.ini

# Autoriser la création de datasets hors orga si nécessaire
sudo grep -q '^ckan.auth.create_dataset_if_not_in_organization' /srv/app/ckan.ini || \
  echo "ckan.auth.create_dataset_if_not_in_organization = true" | sudo tee -a /srv/app/ckan.ini

# Nettoyage paramètre obsolète
sudo sed -i '/^[[:space:]]*apitoken_header_name[[:space:]]*=/d' /srv/app/ckan.ini

echo "Configuring CKAN to support both legacy API keys and JWT tokens..."

sudo sed -i '/^ckan.auth.create_default_api_keys/d' /srv/app/ckan.ini
echo "ckan.auth.create_default_api_keys = true" | sudo tee -a /srv/app/ckan.ini

# Activer les tokens JWT (les secrets sont générés dans start_ckan_with_logs.sh)
sudo sed -i '/^ckan.auth.api_token.jwt.enable/d' /srv/app/ckan.ini
echo "ckan.auth.api_token.jwt.enable = true" | sudo tee -a /srv/app/ckan.ini

# S'assurer que les secrets JWT sont présents (générés dans start_ckan_with_logs.sh)
if ! grep -q '^api_token.jwt.encode.secret' /srv/app/ckan.ini; then
    echo "JWT secrets not found, they will be generated by start_ckan_with_logs.sh"
fi

echo "API key configuration complete (legacy + JWT support)."

echo "Patching get_user_from_token() in api_token.py to support both legacy API keys and JWT tokens..."

# Supprime toutes les versions précédentes de get_user_from_token
sed -i '/^def get_user_from_token(token: str/,/^    return user/d' /srv/app/src/ckan/ckan/lib/api_token.py
sed -i '/^def get_user_from_token(token: str/,/^    return token_obj.owner if token_obj else None/d' /srv/app/src/ckan/ckan/lib/api_token.py
sed -i '/^def get_user_from_token(token: str/,/^    raise ValueError/d' /srv/app/src/ckan/ckan/lib/api_token.py

# Injecte la version hybride qui supporte legacy + JWT
cat <<'EOF' >> /srv/app/src/ckan/ckan/lib/api_token.py

def get_user_from_token(token: str, update_access_time: bool = True) -> Optional[model.User]:
    """
    Patch hybride pour supporter à la fois les tokens legacy et JWT modernes.
    Ordre de vérification :
    1. Token legacy (UUID dans table api_token)
    2. Token legacy (apikey dans table user)
    3. Token JWT moderne
    """
    import re
    
    # 1⃣ Essayer d'abord avec les tokens legacy (UUID dans table api_token)
    if re.match(r'^[a-f0-9\-]{36}$', token):
        token_obj = model.ApiToken.get(token)
        if token_obj:
            if update_access_time:
                token_obj.touch(True)
            return token_obj.owner if token_obj else None
    
    # 2⃣ Essayer avec les tokens legacy (apikey dans table user)
    user = model.Session.query(model.User).filter_by(apikey=token).first()
    if user:
        return user
    
    # 3⃣ Essayer avec les tokens JWT modernes (utilise le code CKAN standard)
    try:
        import jwt as pyjwt
        from ckan.common import config
        
        # Récupérer le secret JWT depuis la config
        jwt_secret = config.get('api_token.jwt.decode.secret', '')
        if not jwt_secret:
            # Si pas de secret configuré, les tokens JWT ne sont pas supportés
            raise ValueError("JWT secret not configured")
        
        # Nettoyer le secret (peut être préfixé par "string:")
        if jwt_secret.startswith('string:'):
            jwt_secret = jwt_secret[7:]
        
        # Décoder le token JWT
        algorithm = config.get('api_token.jwt.algorithm', 'HS256')
        payload = pyjwt.decode(token, jwt_secret, algorithms=[algorithm])
        
        if payload and 'jti' in payload:
            # jti (JWT ID) devrait correspondre à un token dans la table api_token
            token_obj = model.ApiToken.get(payload['jti'])
            if token_obj:
                if update_access_time:
                    token_obj.touch(True)
                return token_obj.owner if token_obj else None
    except ImportError:
        # Module jwt non disponible
        log.debug("PyJWT module not available, JWT tokens not supported")
        pass
    except ValueError as e:
        # Secret JWT non configuré
        log.debug(f"JWT secret not configured: {e}")
        pass
    except Exception as e:
        # Erreurs de décodage JWT (token invalide, signature incorrecte, etc.)
        # C'est normal si ce n'est pas un token JWT
        log.debug(f"Token is not a valid JWT or JWT decoding failed: {e}")
        pass
    
    # Aucun token valide trouvé
    log.warning("Clé API invalide ou utilisateur introuvable.")
    return None

EOF

echo "Patch hybride installé (legacy + JWT support)"
echo "Suppression des fichiers .pyc pour forcer rechargement Python..."
sudo find /srv/app/src/ckan/ckan/lib/ -name '*.pyc' -delete 2>/dev/null || true
sudo find /srv/app/ckanext-dataload-router/ckanext/dataload_router/ -name '*.pyc' -delete 2>/dev/null || true


echo "Ajout du log du user CKAN dans ckan/views/__init__.py..."

#sudo sed -i '/g.user = current_user.name/a \ \ \ \ log.info(f"Requête API authentifiée pour utilisateur : {g.user}")' /srv/app/src/ckan/ckan/views/__init__.py

echo "Log du user CKAN injecté dans views/__init__.py"



#sed -i '/def _get_user_for_apitoken()/a\
 #   import flask\n\
 #   from pprint import pformat\n\
 #   log.info("API call: %s %s", flask.request.method, flask.request.path)\n\
 #   log.info("Headers: %s", pformat(dict(flask.request.headers)))\n\
 #   try:\n\
 #   log.info("Body: %s", flask.request.get_data(as_text=True))\n\
 #   except Exception as e:\n\
 #   log.warning("Could not read request body: %s", e)
#' /srv/app/src/ckan/ckan/views/__init__.py


#sudo sed -i '/g.user = current_user.name/a\
#    import flask\n\
#    log.info("Body: %s", flask.request.get_data(as_text=True))
#' /srv/app/src/ckan/ckan/views/__init__.py

sudo sed -i '/def _get_user_for_apitoken()/a\
    import flask\n\
    from pprint import pformat\n\
    # log.info("Query Params: %s", pformat(dict(flask.request.args)))\n\
    # log.info("Form Params: %s", pformat(dict(flask.request.form)))\n\
    try:\n\
        # log.info("Body: %s", flask.request.get_data(as_text=True))\n\
        pass\n\
    except Exception as e:\n\
        log.warning("Could not read request body: %s", e)
' /srv/app/src/ckan/ckan/views/__init__.py





sudo sed -i "s/^ckan.datapusher.api_token.*/ckan.datapusher.api_token = ${CKAN_API_KEY:-}/" /srv/app/ckan.ini

# Définir le français comme langue par défaut
echo "Configuration de la langue française par défaut..."
if sudo grep -q '^ckan.locale_default' /srv/app/ckan.ini; then
  sudo sed -i "s/^ckan.locale_default.*/ckan.locale_default = fr/" /srv/app/ckan.ini
else
  if sudo grep -q '^ckan.site_id' /srv/app/ckan.ini; then
    sudo sed -i "/^ckan.site_id[[:space:]]*=/a ckan.locale_default = fr" /srv/app/ckan.ini
  else
    echo "ckan.locale_default = fr" | sudo tee -a /srv/app/ckan.ini
  fi
fi

# Définir les langues disponibles (français en premier, puis autres langues)
if sudo grep -q '^ckan.locales_offered' /srv/app/ckan.ini; then
  sudo sed -i "s/^ckan.locales_offered.*/ckan.locales_offered = fr en/" /srv/app/ckan.ini
else
  if sudo grep -q '^ckan.site_id' /srv/app/ckan.ini; then
    sudo sed -i "/^ckan.site_id[[:space:]]*=/a ckan.locales_offered = fr en" /srv/app/ckan.ini
  else
    echo "ckan.locales_offered = fr en" | sudo tee -a /srv/app/ckan.ini
  fi
fi

# Définir l'ordre des langues (français en premier)
if sudo grep -q '^ckan.locale_order' /srv/app/ckan.ini; then
  sudo sed -i "s/^ckan.locale_order.*/ckan.locale_order = fr en/" /srv/app/ckan.ini
else
  if sudo grep -q '^ckan.site_id' /srv/app/ckan.ini; then
    sudo sed -i "/^ckan.site_id[[:space:]]*=/a ckan.locale_order = fr en" /srv/app/ckan.ini
  else
    echo "ckan.locale_order = fr en" | sudo tee -a /srv/app/ckan.ini
  fi
fi

echo "Configuration de la langue française terminée"

# Configuration des licences disponibles
echo "Configuration des licences disponibles..."
LICENSES_FILE="/srv/app/config_files/common/licenses.json"
if [ -f "$LICENSES_FILE" ]; then
  # Configurer le chemin vers le fichier de licences
  if sudo grep -q '^ckan.licenses_group_url' /srv/app/ckan.ini; then
    sudo sed -i "s|^ckan.licenses_group_url.*|ckan.licenses_group_url = file://${LICENSES_FILE}|" /srv/app/ckan.ini
  else
    if sudo grep -q '^ckan.site_id' /srv/app/ckan.ini; then
      sudo sed -i "/^ckan.site_id[[:space:]]*=/a ckan.licenses_group_url = file://${LICENSES_FILE}" /srv/app/ckan.ini
    else
      echo "ckan.licenses_group_url = file://${LICENSES_FILE}" | sudo tee -a /srv/app/ckan.ini
    fi
  fi
  echo "Configuration des licences terminée (fichier: ${LICENSES_FILE})"
else
  echo " Fichier de licences non trouvé: ${LICENSES_FILE}"
fi

# Ajout des configurations pour les vues datatables
echo "Configuration des vues datatables par défaut..."
if ! sudo grep -q '^ckan.views.available_views' /srv/app/ckan.ini; then
  sudo sed -i '/^ckan.plugins = /a \
ckan.views.available_views = datatables_view' /srv/app/ckan.ini
fi

echo "Configuration des vues datatables terminée"

echo "Vérification de la configuration de datastore sql search..."
if ! sudo grep -q '^ckan.datastore.sql_search_enabled' /srv/app/ckan.ini; then
  if sudo grep -q '^ckan.site_id' /srv/app/ckan.ini; then
    sudo sed -i '/^ckan.site_id[[:space:]]*=/a ckan.datastore.sql_search_enabled = true' /srv/app/ckan.ini
  else
    echo 'ckan.datastore.sql_search_enabled = true' | sudo tee -a /srv/app/ckan.ini
  fi
fi

echo "Configuration de datastore sql search terminée"
echo "Configuration de ckan.ini"

ACTIVITY_ENABLED="ckan.activity_streams_enabled"
EMAIL_NOTIF="ckan.activity_streams_email_notifications"
FLASK_ONLY="ckan.flask_app"
# Fonction pour ajouter ou modifier une ligne de config
set_or_append_config() {
  local key="$1"
  local value="$2"

  if sudo grep -q "^${key}[[:space:]]*=" /srv/app/ckan.ini; then
    echo "Mise à jour de ${key}..."
    sudo sed -i "s|^${key}[[:space:]]*=.*|${key} = ${value}|" /srv/app/ckan.ini
  else
    echo "Ajout de ${key}..."
    echo "${key} = ${value}" | sudo tee -a /srv/app/ckan.ini > /dev/null
  fi
}

set_or_append_config "${ACTIVITY_ENABLED}" "true"
set_or_append_config "${EMAIL_NOTIF}" "false"
set_or_append_config "${FLASK_ONLY}" "false"

echo "Configuration de ckan.ini terminée"

# Continuer les éventuels scripts suivants
exec "$@"

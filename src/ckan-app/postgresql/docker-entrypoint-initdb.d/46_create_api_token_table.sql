-- Création de la table api_token si elle n'existe pas
-- Cette table est nécessaire pour la fonctionnalité des tokens API dans CKAN

CREATE TABLE IF NOT EXISTS api_token (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    last_access TIMESTAMP WITHOUT TIME ZONE,
    plugin_extras JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT api_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
);

-- Créer l'index si il n'existe pas
CREATE INDEX IF NOT EXISTS idx_api_token_user_id ON api_token(user_id);

-- Créer la table system_info si elle n'existe pas (peut manquer si CKAN n'a pas été initialisé correctement)
-- Structure standard CKAN : id est un SERIAL (INTEGER auto-incrémenté)
CREATE TABLE IF NOT EXISTS system_info (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT,
    state VARCHAR(50) DEFAULT 'active',
    created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Créer les index pour améliorer les performances
CREATE INDEX IF NOT EXISTS idx_system_info_key ON system_info(key);
CREATE INDEX IF NOT EXISTS idx_system_info_state ON system_info(state);

-- Créer l'entrée system_info pour ckan.config_update si elle n'existe pas
-- Note: id n'est pas spécifié car c'est un SERIAL (auto-généré)
INSERT INTO system_info (key, value, state)
VALUES ('ckan.config_update', '0', 'active')
ON CONFLICT (key) DO NOTHING;






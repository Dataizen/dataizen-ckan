-- Création de la table system_info si elle n'existe pas
-- Cette table est nécessaire pour stocker les informations de configuration système de CKAN
-- Elle est normalement créée par "ckan db init" mais peut manquer dans certains cas
-- Structure standard CKAN : id est un SERIAL (INTEGER auto-incrémenté)

CREATE TABLE IF NOT EXISTS system_info (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT,
    state VARCHAR(50) DEFAULT 'active',
    created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Créer l'index sur la colonne key pour améliorer les performances
CREATE INDEX IF NOT EXISTS idx_system_info_key ON system_info(key);

-- Créer l'index sur la colonne state
CREATE INDEX IF NOT EXISTS idx_system_info_state ON system_info(state);

-- Insérer les valeurs par défaut si elles n'existent pas
-- Note: id n'est pas spécifié car c'est un SERIAL (auto-généré)
INSERT INTO system_info (key, value, state)
VALUES ('ckan.config_update', '0', 'active')
ON CONFLICT (key) DO NOTHING;


-- Création de la base datagis : stockage des couches géospatiales importées
-- depuis CKAN (Shapefile, GeoJSON, KML, etc.) par le plugin OGC.
-- Activée par défaut en local pour que le flow d'ingestion géospatial fonctionne
-- sans configuration supplémentaire. En prod / qualif, la base est déjà créée.

CREATE DATABASE datagis OWNER ckan ENCODING 'utf-8';

\c datagis
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
GRANT ALL PRIVILEGES ON DATABASE datagis TO ckan;
GRANT ALL ON SCHEMA public TO ckan;

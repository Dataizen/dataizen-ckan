-- Script pour corriger le SRID 900914 vers 4326
-- ATTENTION: Ce script suppose que les coordonnées sont déjà en WGS84 (EPSG:4326)
-- Si les coordonnées sont dans un autre système, il faut utiliser ST_Transform

-- Table: res_bfe3499b_91e6_4e1e_a872_4276115429d5

BEGIN;

-- 1. Mettre à jour geometry_columns
UPDATE geometry_columns
SET srid = 4326
WHERE f_table_schema = 'public'
AND f_table_name = 'res_bfe3499b_91e6_4e1e_a872_4276115429d5'
AND f_geometry_column = 'the_geom';

-- 2. Réinterpréter les géométries comme étant en 4326
-- ST_SetSRID ne transforme pas, il change juste le SRID de la géométrie
-- Si les coordonnées sont déjà en WGS84, cela fonctionnera
UPDATE "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
SET the_geom = ST_SetSRID(the_geom, 4326)
WHERE the_geom IS NOT NULL;

-- 3. Vérifier le résultat
SELECT 
    'Après correction:' as status,
    (SELECT srid FROM geometry_columns WHERE f_table_name = 'res_bfe3499b_91e6_4e1e_a872_4276115429d5') as srid_geometry_columns,
    (SELECT DISTINCT ST_SRID(the_geom) FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5" WHERE the_geom IS NOT NULL LIMIT 1) as srid_geometries,
    COUNT(*) as nb_geometries
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
WHERE the_geom IS NOT NULL;

COMMIT;

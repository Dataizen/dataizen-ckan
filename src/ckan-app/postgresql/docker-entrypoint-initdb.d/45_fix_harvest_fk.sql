\c ckan;

DO $$
DECLARE
  fkname text;
  job_col_exists boolean;
  job_table_exists boolean;
BEGIN
  -- Vérifie que la table harvest_object existe
  SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name = 'harvest_object'
  ) INTO job_table_exists;

  IF NOT job_table_exists THEN
    RAISE NOTICE '✅ Table harvest_object absente, rien à faire.';
    RETURN;
  END IF;

  -- Vérifie que la colonne harvest_job_id existe
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'harvest_object'
    AND column_name = 'harvest_job_id'
  ) INTO job_col_exists;

  IF NOT job_col_exists THEN
    RAISE NOTICE '⚠️ Colonne harvest_job_id absente, rien à faire.';
    RETURN;
  END IF;

  -- Si la contrainte existe déjà sous le bon nom, on arrête ici
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='harvest_object_harvest_job_id_fkey') THEN
    RAISE NOTICE '✅ Contrainte harvest_object_harvest_job_id_fkey déjà présente.';
    RETURN;
  END IF;

  -- Sinon, on cherche une FK équivalente sous un autre nom
  SELECT c.conname
  INTO fkname
  FROM pg_constraint c
  JOIN pg_class t  ON t.oid = c.conrelid
  JOIN pg_class rt ON rt.oid = c.confrelid
  WHERE c.contype='f'
    AND t.relname='harvest_object'
    AND rt.relname='harvest_job'
    AND c.conkey = ARRAY[
      (SELECT attnum FROM pg_attribute WHERE attrelid=c.conrelid AND attname='harvest_job_id')
    ]
    AND c.confkey = ARRAY[
      (SELECT attnum FROM pg_attribute WHERE attrelid=c.confrelid AND attname='id')
    ]
  LIMIT 1;

  -- Si on a trouvé une FK équivalente → on la renomme
  IF fkname IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE harvest_object RENAME CONSTRAINT %I TO harvest_object_harvest_job_id_fkey',
      fkname
    );
    RAISE NOTICE '🔁 Contrainte % a été renommée en harvest_object_harvest_job_id_fkey', fkname;
  ELSE
    -- Sinon → on la crée
    EXECUTE '
      ALTER TABLE harvest_object
      ADD CONSTRAINT harvest_object_harvest_job_id_fkey
      FOREIGN KEY (harvest_job_id)
      REFERENCES harvest_job(id)
      ON DELETE CASCADE
    ';
    RAISE NOTICE '✅ Contrainte harvest_object_harvest_job_id_fkey créée.';
  END IF;
END$$;
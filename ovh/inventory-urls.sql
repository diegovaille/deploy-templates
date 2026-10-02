-- Read-only: print column names and counts, never the stored URLs.
SELECT format(
  'SELECT %L || ''|'' || count(*)::text FROM %I.%I WHERE %I LIKE ''%%objectstorage.%%'' HAVING count(*) > 0;',
  table_name || '.' || column_name, table_schema, table_name, column_name)
FROM information_schema.columns
WHERE table_schema = 'public' AND data_type IN ('text', 'character varying')
ORDER BY table_name, column_name
\gexec

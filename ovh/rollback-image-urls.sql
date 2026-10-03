-- Leaves objects copied to R2 intact and preserves subsequent user edits.
\set ON_ERROR_STOP on
BEGIN;
UPDATE produto p SET imagem_url=b.old_url
FROM ovh_r2_url_backup b
WHERE b.table_name='produto' AND b.row_id=p.id AND p.imagem_url=b.new_url;
UPDATE filial f SET logo_url=b.old_url
FROM ovh_r2_url_backup b
WHERE b.table_name='filial' AND b.row_id=f.id AND f.logo_url=b.new_url;
COMMIT;

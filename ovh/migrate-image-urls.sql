-- psql -v image_origin=https://images.primeira.app.br -v apply=false -f ...
-- Run only after verified object copy and DB backup. Ignores external URLs.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE image_prefixes (old_prefix text PRIMARY KEY, new_prefix text NOT NULL);
INSERT INTO image_prefixes VALUES
('https://grz9zu8hdz2q.objectstorage.sa-saopaulo-1.oci.customer-oci.com/n/grz9zu8hdz2q/b/storehouse-images/o/', :'image_origin' || '/'),
('https://objectstorage.sa-saopaulo-1.oraclecloud.com/n/grz9zu8hdz2q/b/storehouse-images/o/', :'image_origin' || '/');

SELECT 'produto.imagem_url' AS field, count(*) AS matches
FROM produto p JOIN image_prefixes m ON starts_with(p.imagem_url, m.old_prefix)
UNION ALL
SELECT 'filial.logo_url', count(*)
FROM filial f JOIN image_prefixes m ON starts_with(f.logo_url, m.old_prefix);

-- Durable originals permit targeted rollback without restoring unrelated rows.
CREATE TABLE IF NOT EXISTS ovh_r2_url_backup (
    table_name text NOT NULL,
    row_id uuid NOT NULL,
    old_url text NOT NULL,
    new_url text NOT NULL,
    PRIMARY KEY (table_name, row_id)
);
INSERT INTO ovh_r2_url_backup
SELECT 'produto', p.id, p.imagem_url,
       m.new_prefix || substring(p.imagem_url FROM length(m.old_prefix) + 1)
FROM produto p JOIN image_prefixes m ON starts_with(p.imagem_url, m.old_prefix)
ON CONFLICT DO NOTHING;
INSERT INTO ovh_r2_url_backup
SELECT 'filial', f.id, f.logo_url,
       m.new_prefix || substring(f.logo_url FROM length(m.old_prefix) + 1)
FROM filial f JOIN image_prefixes m ON starts_with(f.logo_url, m.old_prefix)
ON CONFLICT DO NOTHING;

UPDATE produto p SET imagem_url = b.new_url
FROM ovh_r2_url_backup b
WHERE b.table_name='produto' AND b.row_id=p.id AND p.imagem_url=b.old_url;
UPDATE filial f SET logo_url = b.new_url
FROM ovh_r2_url_backup b
WHERE b.table_name='filial' AND b.row_id=f.id AND f.logo_url=b.old_url;

\if :apply
COMMIT;
\else
ROLLBACK;
\endif

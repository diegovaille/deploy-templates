# Deploy da PIB na OVH

Estes arquivos mantêm os serviços da PIB na VPS existente do TudoFirme. Primeira, preview e API foram ativados na OVH em 2026-10-03, com PostgreSQL 16 e imagens no R2. Os frontends Pinguim continuam na Oracle e usam a API migrada. Não execute bootstrap do financeiro novamente nem substitua seus hosts Nginx.

## Arquivos

- `compose.yml`: PostgreSQL persistente e API em localhost; exemplos de segredos são placeholders. Manter `.env`, `app.env` e `oci/` fora do Git, acessíveis somente ao operador.
- `render-nginx.py`: gera hosts de produção ou ensaio sem instalar nem recarregar Nginx. Produção pressupõe certificados válidos já provisionados. Frontend e preview usam CSP em modo de relatório até validar o scanner Quagga no navegador; demais hosts aplicam a política.
- `deploy-release.py`: ativação estática por symlink atômico e imagem API por commit, com verificação e retorno à release anterior em falha. Rollback de imagem não reverte migrations de banco; novas migrations precisam ser compatíveis ou ter estratégia própria.
- Scripts de ensaio: usam ambiente descartável, imagem e builds publicados. Nunca rodar `smoke-login.py` contra produção; ele altera e restaura senhas apenas na cópia nomeada `primeira-db-rehearsal`.
- `inventory-urls.sql`, `count-rows.sql`: consultas de inventário sem exibir URLs ou registros individuais.
- `migrate-image-urls.sql`: transforma os dois prefixos Oracle observados. Executar com `-v image_origin=https://images.primeira.app.br -v apply=false` para simular. `apply=true` grava somente após cópia verificada e backup. O rollback preserva URLs alteradas pelo usuário após a migração.
- `r2-images.yml.example`: lista completa de buckets para o override Spring; imagens no R2 e despesas ainda na Oracle. Anexos privados precisam de implementação separada antes da retirada de OCI.

## Cópia de imagens

`copy-images-r2.py` valida um manifesto JSON com os campos Oracle `name`, `size`, `md5` e `headers`, e arquivos baixados preservando keys. Sem `--upload` apenas confere o snapshot. Com `--upload`, usa AWS CLI e credenciais JSON modo 0600 (`access_key_id` e `secret_access_key`), limitadas ao bucket; copia sem apagar objetos, preserva Content-Type e metadados, valida tamanho, MD5 e cabeçalhos de cada objeto e grava relatório. Marcadores de diretório vazios também são preservados. Fornecer `--manifest`, `--files`, `--credentials`, `--account-id` e `--report`; não versionar dados nem credenciais. Executar nova sincronização com uploads pausados antes da virada.

## Ativação do CI

O workflow reutilizável `.github/workflows/deploy-ovh.yml` é chamado automaticamente em pushes para main da API e do frontend Primeira, com acionamento manual adicional; seus workflows Oracle foram desabilitados. Fixar sua referência em um commit aprovado nos consumidores; usar inputs `kind=static` e site `frontend`, `preview`, `pinguimice` ou `pinguimice-admin`, ou `kind=api` e `site=backend`. Os callers Pinguim permanecem em rascunho até sua migração.

Provisionar o ativador como `/usr/local/sbin/primeira-deploy-release`, proprietário root e modo 0755. Usar chave de CI de usuário separado com permissão para enviar artefatos e executar somente esse ativador por sudo; não adicionar o usuário ao grupo Docker. Configure ambiente GitHub `ovh` (ou `ovh-preview`), variables `DEPLOY_HOST` e `DEPLOY_USER`, secrets `DEPLOY_SSH_KEY` e `DEPLOY_KNOWN_HOSTS` verificado. Segredos da API permanecem no servidor.

Para API, provisionar `/srv/primeira/app/compose.yml`, `.env`, `app.env`, `api-image.env`, `oci/` e `config/r2-images.yml`, além do banco restaurado. O Compose importa o YAML completo, montado somente para leitura. `api-image.env` contém `API_IMAGE=storehouse-api:<commit>`. Para sites, o Nginx aponta para `/srv/primeira/sites/<site>/current`. Releases antigas são preservadas; definir limpeza e retenção após estabilizar.

O gerador aceita `--sites frontend preview backend` para ativar apenas Primeira, e `--certificate-root /srv/primeira/tls`. Durante a transição, adicionar `--trusted-api-proxy 152.67.44.147 --trusted-api-proxy 132.226.252.195`: somente esses proxies Oracle podem fornecer o IP do cliente à API; remover essas opções quando os proxies forem retirados. Isso preserva o limite de requisições por cliente durante a propagação DNS.

Instalar `primeira-tls` como `/etc/letsencrypt/renewal-hooks/deploy/primeira-tls`, root:0755. Emitir os três certificados com Certbot webroot `/var/www/letsencrypt`, cert-name igual ao hostname, e manter `certbot.timer` ativo. O hook atualiza os arquivos de `/srv/primeira/tls` usados pelo Nginx, ignorando certificados de outros projetos. Validar renovação com `certbot renew --dry-run --cert-name <hostname>`; não executar hooks de deploy usando certificados de staging. Depois de reload, os probes devem tolerar o breve intervalo de troca dos workers, preservando a validação TLS.

## Backups privados

`backup.py` usa PostgreSQL custom dump, age e boto3, sem excluir objetos. Instalar age pelo pacote Ubuntu, criar venv `/opt/primeira-backup` e instalar `backup-requirements.txt`. Instalar o script como `/usr/local/sbin/primeira-backup` e o monitor como `/usr/local/sbin/primeira-backup-health`; copiar as unidades systemd, validar com `systemd-analyze verify` e recarregar o daemon.

Provisionar `backup-config.json` e credenciais S3 JSON modo 0600 em `/srv/primeira/secrets`, conforme o exemplo. O bucket `primeira-db-backups` é privado e separado das imagens. A identidade privada age fica fora da VPS; só o arquivo de destinatários públicos é instalado no servidor. Configuration archive, dump e manifesto são criptografados; todos os objetos enviados são baixados novamente para conferir SHA-256. Cópias locais do ciphertext ficam em `/var/lib/primeira-backup/spool`; sua limpeza é uma tarefa separada, sem apagar a última cópia válida em falha de upload.

O timer produz backup horário, também diário à meia-noite UTC e semanal no domingo à meia-noite UTC. A retenção depende de lifecycle do bucket, não do script: `hourly/` 1 dia, `daily/` 7 dias, `weekly/` 28 dias, `migration/` 30 dias e `rehearsal/` 7 dias. A expiração do provedor não garante exatamente 24 arquivos horários. Não habilitar timers até o PostgreSQL de produção estar restaurado e o primeiro backup horário passar. Depois: `systemctl enable --now primeira-backup.timer primeira-backup-health.timer`. O monitor retorna falha quando o backup horário válido ultrapassa duas horas; ainda requer integração de alerta externo ao operador.

Para ensaiar, clonar o config apontando `db_container` para o banco isolado e usando outro `state_directory`; executar com `--class rehearsal`. `--dump-file` aceita somente `migration` ou `rehearsal` e nunca atualiza a marca de sucesso do backup horário. No snapshot Oracle, criptografar no Mac antes de enviar à OVH.

Recuperação: baixar ciphertext e manifesto do R2, descriptografar fora da VPS com a identidade privada, conferir SHA-256 do dump e restaurar por `pg_restore --exit-on-error` em PostgreSQL 16 isolado. Comparar tabelas, contagens, sequências, Liquibase e leitura pela API antes de considerar o backup recuperável. O arquivo `configuration.tar.age` permite recuperar também o ambiente e a chave OCI; nunca imprimir seu conteúdo. Repetir o teste mensalmente e após mudanças relevantes.

API e frontend começaram com acionamento manual e tiveram deploy em push para main habilitado após a virada; preview usa ambiente separado. Scripts Oracle existentes não devem receber o IP da OVH como substituição simples.

## Validação realizada

Ensaio na OVH: PostgreSQL 12 → 16, contagens iguais nas 17 tabelas, logins da loja e Pinguim Admin, leituras autenticadas e bloqueio sem token. Cinco hosts passaram em Nginx isolado; fallback SPA, 404 para JS ausente e bloqueio de dotfiles validados. Rollback estático preservou o symlink anterior quando o probe HTTPS falhou por ausência dos certificados PIB na OVH. Transformação de 450 imagens e dois logos passou com rollback. Workflow passou em actionlint. Cliente de URL pública passou nos três testes unitários.

Virada de 2026-10-03: publicações por CI da API e frontend passaram; banco final restaurado com contagens iguais à origem; 450 imagens e dois logos atualizados com originais preservados. Upload R2, venda e cancelamento com recomposição de estoque passaram em cópia isolada. Certificados dos três hosts foram emitidos na OVH e as três renovações simuladas passaram; login Google real e acesso à organização passaram no ambiente novo. Backups horários criptografados e monitor de validade estão ativos; recuperação externa foi testada. Câmera/leitor físico e alerta por canal externo ainda precisam de validação/configuração.

PostgreSQL 16 na OVH é a única fonte de dados após a virada. API Oracle está parada, sem restart automático, e o usuário do banco antigo está NOLOGIN. O Nginx Oracle encaminha HTTPS para o IP explícito OVH com Host/SNI e verificação de certificado. Depois de novas gravações, rollback de imagem ou site mantém o banco OVH: não reabrir o PostgreSQL 12 antigo. Recursos Oracle permanecem preservados para observação e recuperação; não foram excluídos.

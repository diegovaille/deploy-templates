# Deploy da PIB na OVH

Estes arquivos preparam os serviços da PIB na VPS existente do TudoFirme. Não execute bootstrap do financeiro novamente nem substitua seus hosts Nginx. A produção continua na Oracle até concluir certificados, backups externos e a virada do banco.

## Arquivos

- `compose.yml`: PostgreSQL persistente e API em localhost; exemplos de segredos são placeholders. Manter `.env`, `app.env` e `oci/` fora do Git, acessíveis somente ao operador.
- `render-nginx.py`: gera hosts de produção ou ensaio sem instalar nem recarregar Nginx. Produção pressupõe certificados válidos já provisionados. Frontend e preview usam CSP em modo de relatório até validar o scanner Quagga no navegador; demais hosts aplicam a política.
- `deploy-release.py`: ativação estática por symlink atômico e imagem API por commit, com verificação e retorno à release anterior em falha. Rollback de imagem não reverte migrations de banco; novas migrations precisam ser compatíveis ou ter estratégia própria.
- Scripts de ensaio: usam ambiente descartável, imagem e builds publicados. Nunca rodar `smoke-login.py` contra produção; ele altera e restaura senhas apenas na cópia nomeada `primeira-db-rehearsal`.
- `inventory-urls.sql`, `count-rows.sql`: consultas de inventário sem exibir URLs ou registros individuais.
- `migrate-image-urls.sql`: transforma os dois prefixos Oracle observados. Executar com `-v image_origin=https://imagens.primeira.app.br -v apply=false` para simular. `apply=true` grava somente após cópia verificada e backup. O rollback preserva URLs alteradas pelo usuário após a migração.
- `r2-images.yml.example`: lista completa de buckets para o override Spring; imagens no R2 e despesas ainda na Oracle. Anexos privados precisam de implementação separada antes da retirada de OCI.

## Ativação do CI

O workflow reutilizável `.github/workflows/deploy-ovh.yml` ainda não substitui os workflows Oracle. Após revisão, fixar sua referência em um commit aprovado nos consumidores; usar inputs `kind=static` e site `frontend`, `preview`, `pinguimice` ou `pinguimice-admin`, ou `kind=api` e `site=backend`.

Provisionar o ativador como `/usr/local/sbin/primeira-deploy-release`, proprietário root e modo 0755. Usar chave de CI de usuário separado com permissão para enviar artefatos e executar somente esse ativador por sudo; não adicionar o usuário ao grupo Docker. Configure ambiente GitHub `ovh` (ou `ovh-preview`), variables `DEPLOY_HOST` e `DEPLOY_USER`, secrets `DEPLOY_SSH_KEY` e `DEPLOY_KNOWN_HOSTS` verificado. Segredos da API permanecem no servidor.

Para API, provisionar `/srv/primeira/app/compose.yml`, `.env`, `app.env`, `api-image.env` e `oci/`, além do banco restaurado. `api-image.env` contém `API_IMAGE=storehouse-api:<commit>`. Para sites, o Nginx aponta para `/srv/primeira/sites/<site>/current`. Releases antigas são preservadas; definir limpeza e retenção após estabilizar.

Começar com acionamento manual. Habilitar deploy em push para main somente após a virada; preview usa ambiente separado. Scripts Oracle existentes não devem receber o IP da OVH como substituição simples.

## Validação realizada

Ensaio na OVH: PostgreSQL 12 → 16, contagens iguais nas 17 tabelas, logins da loja e Pinguim Admin, leituras autenticadas e bloqueio sem token. Cinco hosts passaram em Nginx isolado; fallback SPA, 404 para JS ausente e bloqueio de dotfiles validados. Rollback estático preservou o symlink anterior quando o probe HTTPS falhou por ausência dos certificados PIB na OVH. Transformação de 450 imagens e dois logos passou com rollback. Workflow passou em actionlint. Cliente de URL pública passou nos três testes unitários.

Ainda não validados: publicação positiva por CI, callback Google real, câmera/leitor no navegador, operações de gravação completas, upload R2 e certificados dos domínios PIB na OVH.

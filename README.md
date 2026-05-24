# deploy-templates

Reusable GitHub Actions workflows for deploying to Oracle Cloud VMs, with nginx
hardening (rate limiting + bot/probe blocking) baked in once and shared by all consumers.

## What's here

- **`.github/workflows/deploy-static.yml`** — build a Node SPA and deploy it to a VM behind nginx.
- **`.github/workflows/deploy-docker.yml`** — build a Docker image and run it on a VM behind nginx.
- **`actions/apply-nginx-hardening`** — composite action; writes `conf.d/hardening.conf`
  (rate-limit zone + probe-path `map`) and `snippets/hardening.conf` (method allowlist +
  `return 444` for bad methods/paths + `limit_req`) on the target VM. Single source of truth
  for the security rules — change it here and every consumer inherits it on its next deploy.

See `docs/specs/` for the full design.

## Requirements in the consumer repo

**Secrets:**
- `OCI_VM_SSH_KEY` — base64-encoded SSH private key for the VM (both workflows).
- `DOCKER_ENV` — full `.env`-format blob with the container's env vars (`deploy-docker` only).

**Variables (`vars`):**
- `OCI_VM_SSH_USER` — SSH user (e.g. `ubuntu`).
- `OCI_FRONTEND_VM_HOST` / `OCI_VM_HOST` — target VM host/IP (passed as the `vm_host` input).

**nginx server block:** the consumer ships its own server block as a file in the repo
(e.g. `deploy/nginx.conf`) and **must include this line inside the server block:**
```nginx
include snippets/hardening.conf;
```
The workflow guarantees the snippet exists on the VM; it does not edit your server block.

## Usage — static site

```yaml
# .github/workflows/deploy.yml in the consumer repo
name: Deploy
on:
  push:
    branches: [ main ]
  workflow_dispatch:

jobs:
  deploy:
    uses: diegovaille/deploy-templates/.github/workflows/deploy-static.yml@v1
    with:
      vm_host: ${{ vars.OCI_FRONTEND_VM_HOST }}
      ssh_user: ${{ vars.OCI_VM_SSH_USER }}
      domains: "pinguimice.com.br www.pinguimice.com.br"   # space-separated, for certbot -d
      remote_path: /var/www/pinguimice
      site_name: pinguimice                                 # nginx sites-available filename
      nginx_config_path: deploy/nginx.conf
      # optional: build_command (default "npm run build"), build_dir ("dist"), node_version ("22")
    secrets: inherit                                        # OCI_VM_SSH_KEY
```

## Usage — Docker service

```yaml
jobs:
  deploy:
    uses: diegovaille/deploy-templates/.github/workflows/deploy-docker.yml@v1
    with:
      vm_host: ${{ vars.OCI_VM_HOST }}
      ssh_user: ${{ vars.OCI_VM_SSH_USER }}
      image_name: storehouse-api
      domains: "api.primeira.app.br"
      site_name: backend
      nginx_config_path: deploy/nginx.conf
      container_port: "8080"
      # optional: restart_policy (default "unless-stopped"), build_context ("."), platforms ("linux/amd64")
    secrets: inherit                                        # OCI_VM_SSH_KEY, DOCKER_ENV
```

The container env comes entirely from the `DOCKER_ENV` secret (one `.env` blob), written to a
`0600` file on the VM and used via `docker run --env-file`. `--restart unless-stopped` is the default.

## Versioning

Pin to the major tag: `@v1`. Releases are tagged `v1.0.0`, `v1.1.0`, … and the `v1` tag is moved
to the latest stable release. Breaking changes ship as `@v2`.

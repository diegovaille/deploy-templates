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
- For `deploy-docker`: every other inherited secret becomes a container env var automatically
  (via `secrets: inherit` + `toJSON(secrets)`), except `OCI_VM_SSH_KEY`, `OCI_PRIVATE_KEY_PEM_B64`,
  and `github_token`. Non-secret env (vars + literals) goes through the `extra_env` input.

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
      extra_env: |
        SPRING_PROFILES_ACTIVE=prod
        OCI_PRIVATE_KEY_PATH=/root/.oci/oci_api_key.pem
        POSTGRES_USER=${{ vars.POSTGRES_USER }}
        POSTGRES_HOST=${{ vars.POSTGRES_HOST }}
      volume_mounts: "-v /home/ubuntu/.oci:/root/.oci"
      # optional: restart_policy (default "unless-stopped"), build_context ("."), platforms ("linux/amd64")
    secrets: inherit                                        # OCI_VM_SSH_KEY + the container's secret env vars
```

The container env is assembled on the fly: each inherited **secret** becomes an env var (minus the
infra ones above), plus the non-secret `extra_env` lines (vars + literals). It's written to a `0600`
file on the VM and used via `docker run --env-file`. `--restart unless-stopped` is the default;
`volume_mounts` is appended verbatim to `docker run` (e.g. for the OCI key volume).

## Versioning

Pin to the major tag: `@v1`. Releases are tagged `v1.0.0`, `v1.1.0`, … and the `v1` tag is moved
to the latest stable release. Breaking changes ship as `@v2`.

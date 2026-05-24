# Design: Reusable deploy templates for Oracle Cloud projects

**Date:** 2026-05-24
**Status:** Approved (pending spec review)
**Author:** Diego Vaillé (with Claude)

## Problem

Five GitHub Actions deploys across four repos (`storehouse-api`, `primeira-frontend-app`,
`pinguimice-react`, `pinguimice-admin`) target Oracle Cloud VMs with near-identical logic:
build → scp artifact → ssh → `tee` nginx config → certbot → reload. Each one duplicates ~120
lines, including the just-added nginx hardening. A change to the hardening, restart policy,
certbot flow, or SSH plumbing must be copied into every repo by hand — error-prone and already
the cause of drift (the hardening had to be retrofitted into 5 files).

## Goal

Centralize the deploy pipeline so each consumer repo declares intent with a handful of inputs,
and shared concerns (hardening, restart policy, certbot, SSH plumbing) live in exactly one place.

## Non-goals

- Rewriting provisioning (Terraform / cloud-init in `storehouse-infra`). The cloud-init is
  bootstrap-only and stale; the CI deploys are the real nginx-config maintainers.
- Changing the VMs' runtime topology (API VM `152.67.44.147`, front VM `132.226.252.195`).
- fail2ban and the `default`-server lockdown: these are VM-level, not per-deploy, and stay as
  one-time host setup (documented separately, e.g. in cloud-init or a bootstrap script).

## Chosen approach

GitHub **reusable workflows** (`workflow_call`), one per deploy type, backed by a shared
**composite action** for the nginx hardening. Rejected alternatives: composite-actions-only
(each repo still writes the pipeline structure → less dedup) and a full hybrid build-out (more
machinery than needed now).

## Architecture

New **public** repo `diegovaille/deploy-templates`:

```
.github/workflows/
  deploy-static.yml        # reusable: static SPA -> VM/nginx
  deploy-docker.yml        # reusable: Docker image -> VM/nginx
actions/
  apply-nginx-hardening/
    action.yml             # composite: writes conf.d/hardening.conf + snippets/hardening.conf on the VM
README.md                  # usage + examples
```

The hardening config (rate-limit zone + probe-path `map` + the method/path `if` snippet) is
defined once, inside the `apply-nginx-hardening` composite action. Both reusable workflows call
it. Changing a security rule is a one-file change; consumers inherit it on their next deploy.

### Component: `apply-nginx-hardening` (composite action)

- **Does:** SSH to the target VM and write `/etc/nginx/conf.d/hardening.conf` (`limit_req_zone`
  + `map $request_uri $bad_request_uri`) and `/etc/nginx/snippets/hardening.conf` (method
  allowlist incl. POST/PUT/DELETE, `return 444` for bad methods/paths, `limit_req`), idempotently.
- **Inputs:** `vm_host`, `ssh_user`, `ssh_key` (passed through), `rate` (default `30r/s`),
  `burst` (default `100`), `zone_name` (default `site_limit`).
- **Depends on:** SSH access to the VM; nginx already installed.
- **Note:** consumer nginx server blocks must contain `include snippets/hardening.conf;`. The
  action only guarantees the snippet exists; it does not edit the consumer's server block.

### Component: `deploy-static.yml` (reusable workflow)

Consumer caller:
```yaml
jobs:
  deploy:
    uses: diegovaille/deploy-templates/.github/workflows/deploy-static.yml@v1
    with:
      vm_host: ${{ vars.OCI_FRONTEND_VM_HOST }}
      domains: "pinguimice.com.br www.pinguimice.com.br"
      remote_path: /var/www/pinguimice
      site_name: pinguimice
      nginx_config_path: deploy/nginx.conf
      ssh_user: ${{ vars.OCI_VM_SSH_USER }}
      build_command: npm run build   # default
      build_dir: dist                # default
      node_version: "22"             # default
    secrets: inherit                 # OCI_VM_SSH_KEY
```

**Inputs:** `vm_host` (required), `domains` (required, space-separated for certbot `-d`),
`remote_path` (required), `site_name` (required, nginx sites-available filename),
`nginx_config_path` (required, path in consumer repo to the server block),
`ssh_user` (required, from `vars.OCI_VM_SSH_USER`), `build_command` (default `npm run build`),
`build_dir` (default `dist`), `node_version` (default `"22"`).

**Secrets:** `OCI_VM_SSH_KEY` (required). (`ssh_user` and `vm_host` are non-secret `vars`,
passed as inputs.)

**Flow:** checkout → setup-node → `npm ci` + `build_command` → tar `build_dir` → scp artifact +
`nginx_config_path` to VM → ssh: extract build into `remote_path`, install server block as
`sites-available/<site_name>` + enable, call `apply-nginx-hardening`, certbot (idempotent:
issue only if cert absent), `nginx -t` && reload, cleanup artifact.

### Component: `deploy-docker.yml` (reusable workflow)

Consumer caller:
```yaml
jobs:
  deploy:
    uses: diegovaille/deploy-templates/.github/workflows/deploy-docker.yml@v1
    with:
      vm_host: ${{ vars.OCI_VM_HOST }}
      image_name: storehouse-api
      domains: "api.primeira.app.br"
      site_name: backend
      nginx_config_path: deploy/nginx.conf
      container_port: "8080"
      restart_policy: unless-stopped   # default
      build_context: "."               # default
      platforms: linux/amd64           # default
      ssh_user: ${{ vars.OCI_VM_SSH_USER }}
    secrets: inherit                   # OCI_VM_SSH_KEY, DOCKER_ENV
```

**Inputs:** `vm_host`, `image_name`, `domains`, `site_name`, `nginx_config_path`,
`ssh_user` (from `vars.OCI_VM_SSH_USER`), `container_port` (default `"8080"`),
`restart_policy` (default `unless-stopped`), `build_context` (default `"."`),
`platforms` (default `linux/amd64`).

**Secrets:** `OCI_VM_SSH_KEY` (required), `DOCKER_ENV` (required).

**Flow:** buildx build → `docker save` + gzip → scp → ssh: `docker load`, install server block,
call `apply-nginx-hardening`, certbot, write `DOCKER_ENV` to a root-owned `0600` env file on the
VM, `docker rm -f <image>` then `docker run -d --restart <restart_policy> --env-file <file>
-p <port>:<port> ...`, `nginx -t` && reload, cleanup.

**Container env decision:** today the backend passes ~14 `-e SECRET=...`. Declaring each secret
in a reusable workflow is awkward. Instead collapse them into a **single `DOCKER_ENV` secret**
(`.env` format, multiline), written to a `0600` file on the VM and consumed via `--env-file`.
One secret instead of 14; the template stays generic. Trade-off: a one-time migration of the
backend's secrets into the `DOCKER_ENV` blob. The `--restart unless-stopped` default bakes in
the fix for the 2026-05-24 reboot outage.

## Versioning

Semantic tags with a moving major. Consumers pin `@v1`. Releases `v1.0.0`, `v1.1.0`, … and the
`v1` tag is moved to the latest stable. Breaking changes → `@v2`. Avoids `@main` applying
changes unintentionally.

## Migration plan (incremental, no big bang)

1. Create `deploy-templates` with both workflows + the composite action. Pilot against
   **`pinguimice-react`** (simplest front) referencing `@main`; run a real deploy and verify the
   site + hardening (`/.env` → blocked, site → 200).
2. Tag `v1`. Migrate the other 3 fronts: each adds `deploy/nginx.conf` (its server block, with
   `include snippets/hardening.conf;`) and shrinks its workflow to a ~10-line caller. One PR per repo.
3. Migrate the backend (`storehouse-api`): add `deploy/nginx.conf`, switch to `DOCKER_ENV`,
   replace the workflow with the `deploy-docker.yml` caller. One PR.
4. Each migration removes the duplicated pipeline, leaving the minimal caller.

**Outcome:** ~120 duplicated lines × 5 → ~10 lines per repo + one source of truth for hardening,
restart policy, certbot, and SSH plumbing.

## Testing / validation

- Pilot deploy on `pinguimice-react` via `@main` before tagging `v1`.
- Per migration: confirm the live site/API still serves (200 / expected status), a probe path
  (`/.env`) is dropped (444), and `nginx -t` passes in the workflow logs.
- Rollback: each consumer keeps its previous workflow in git history; reverting the migration PR
  restores the old self-contained deploy.

## Open considerations

- Reusable workflows read the caller's `vars` context; `vm_host` is passed explicitly as an
  input (from `vars.*`) for clarity rather than read implicitly.
- `apply-nginx-hardening` assumes `ubuntu` SSH user + key auth, matching both current VMs.

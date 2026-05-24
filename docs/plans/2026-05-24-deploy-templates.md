# Reusable Deploy Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public `deploy-templates` repo with two reusable GitHub Actions workflows (`deploy-static`, `deploy-docker`) and one shared nginx-hardening composite action, then migrate the 4 existing repos to use them.

**Architecture:** Reusable workflows (`workflow_call`) own the full pipeline (build → scp → ssh → nginx → certbot → reload). nginx hardening lives once in a composite action both workflows call. Consumers supply their own nginx server block (containing `include snippets/hardening.conf;`) plus a handful of inputs. Validation is by real deploy + `curl`/`nginx -t`, since these are YAML/shell artifacts with no unit tests.

**Tech Stack:** GitHub Actions (reusable workflows + composite actions), `appleboy/ssh-action` + `appleboy/scp-action`, nginx, certbot, Docker, bash.

**Reference:** spec at `docs/superpowers/specs/2026-05-24-deploy-templates-design.md`.

**Conventions used throughout:**
- SSH key secret `OCI_VM_SSH_KEY` is **base64-encoded** (decode before use), matching current repos.
- `ssh_user` / `vm_host` are non-secret repo **vars** passed as inputs.
- VMs run Ubuntu, SSH user `ubuntu`, key auth.

---

## Task 1: Create the `deploy-templates` repo and move the spec

**Files:**
- Create: repo `diegovaille/deploy-templates` (public)
- Create: `README.md`, `docs/specs/2026-05-24-deploy-templates-design.md` (moved from storehouse-api)

- [ ] **Step 1: Create the repo locally and on GitHub**

```bash
mkdir -p ~/Git/pib/deploy-templates && cd ~/Git/pib/deploy-templates
git init -b main
gh repo create diegovaille/deploy-templates --public --source=. --remote=origin
```
Expected: repo created, `origin` set.

- [ ] **Step 2: Move the approved spec into this repo**

```bash
mkdir -p docs/specs actions .github/workflows
cp ~/Git/pib/storehouse-api/docs/superpowers/specs/2026-05-24-deploy-templates-design.md docs/specs/
```

- [ ] **Step 3: Write a minimal README placeholder (expanded in Task 5)**

Create `README.md`:
```markdown
# deploy-templates

Reusable GitHub Actions workflows for deploying to Oracle Cloud VMs.

- `.github/workflows/deploy-static.yml` — static SPA → VM/nginx
- `.github/workflows/deploy-docker.yml` — Docker image → VM/nginx
- `actions/apply-nginx-hardening` — shared nginx hardening (rate limit + probe blocking)

See `docs/specs/` for the design. Usage examples below (Task 5).
```

- [ ] **Step 4: Commit**

```bash
git add . && git commit -m "chore: scaffold deploy-templates repo + design spec"
git push -u origin main
```
Expected: push succeeds.

---

## Task 2: `apply-nginx-hardening` composite action

**Files:**
- Create: `actions/apply-nginx-hardening/action.yml`

- [ ] **Step 1: Write the composite action**

Create `actions/apply-nginx-hardening/action.yml`:
```yaml
name: Apply nginx hardening
description: Idempotently writes /etc/nginx/conf.d/hardening.conf and /etc/nginx/snippets/hardening.conf on a remote VM.
inputs:
  vm_host:
    description: Target VM host/IP
    required: true
  ssh_user:
    description: SSH user
    required: true
  ssh_key_b64:
    description: Base64-encoded SSH private key
    required: true
  rate:
    description: limit_req rate (e.g. 30r/s)
    required: false
    default: "30r/s"
  burst:
    description: limit_req burst
    required: false
    default: "100"
  zone_name:
    description: limit_req zone name
    required: false
    default: "site_limit"
runs:
  using: composite
  steps:
    - name: Decode SSH key
      shell: bash
      run: |
        echo "${{ inputs.ssh_key_b64 }}" | base64 -d > "${{ runner.temp }}/hardening_key.pem"
        chmod 600 "${{ runner.temp }}/hardening_key.pem"
    - name: Write hardening config over SSH
      uses: appleboy/ssh-action@v1.0.0
      with:
        host: ${{ inputs.vm_host }}
        username: ${{ inputs.ssh_user }}
        key_path: ${{ runner.temp }}/hardening_key.pem
        script: |
          set -e
          sudo tee /etc/nginx/conf.d/hardening.conf > /dev/null <<'HARDENING_EOF'
          limit_req_zone $binary_remote_addr zone=${{ inputs.zone_name }}:10m rate=${{ inputs.rate }};
          limit_req_status 429;
          map $request_uri $bad_request_uri {
              default 0;
              ~*(?:\.env|\.git|\.svn|\.hg|/\.aws|/\.ssh|/\.vscode|/\.idea)   1;
              ~*(?:wp-admin|wp-login|wp-includes|wp-content|xmlrpc\.php)     1;
              ~*(?:phpunit|eval-stdin|allow_url_include|auto_prepend_file)   1;
              ~*(?:/vendor/|/cgi-bin/|luci|/SDK/webLanguage|adminer)         1;
              ~*\.(?:php|asp|aspx|jsp|cgi|env)(?:$|[?/])                     1;
          }
          HARDENING_EOF
          sudo mkdir -p /etc/nginx/snippets
          sudo tee /etc/nginx/snippets/hardening.conf > /dev/null <<'SNIPPET_EOF'
          if ($request_method !~ ^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)$) { return 444; }
          if ($bad_request_uri) { return 444; }
          limit_req zone=${{ inputs.zone_name }} burst=${{ inputs.burst }} nodelay;
          SNIPPET_EOF
    - name: Remove SSH key
      if: always()
      shell: bash
      run: rm -f "${{ runner.temp }}/hardening_key.pem"
```

Note: `$binary_remote_addr` / `$request_uri` / `$request_method` / `$bad_request_uri` stay literal because the heredocs are quoted (`<<'..._EOF'`); only `${{ inputs.* }}` is expanded by GitHub.

- [ ] **Step 2: Lint the YAML**

Run: `ruby -ryaml -e "YAML.load_file('actions/apply-nginx-hardening/action.yml'); puts 'OK'"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add actions/apply-nginx-hardening/action.yml
git commit -m "feat: apply-nginx-hardening composite action"
git push
```

---

## Task 3: `deploy-static.yml` reusable workflow

**Files:**
- Create: `.github/workflows/deploy-static.yml`

- [ ] **Step 1: Write the reusable workflow**

Create `.github/workflows/deploy-static.yml`:
```yaml
name: Deploy static site to Oracle VM (reusable)

on:
  workflow_call:
    inputs:
      vm_host:        { required: true,  type: string }
      ssh_user:       { required: true,  type: string }
      domains:        { required: true,  type: string }   # space-separated, for certbot -d
      remote_path:    { required: true,  type: string }   # e.g. /var/www/pinguimice
      site_name:      { required: true,  type: string }   # nginx sites-available filename
      nginx_config_path: { required: true, type: string } # path in consumer repo to server block
      build_command:  { required: false, type: string, default: "npm run build" }
      build_dir:      { required: false, type: string, default: "dist" }
      node_version:   { required: false, type: string, default: "22" }
      cert_email:     { required: false, type: string, default: "diegovaille@gmail.com" }
    secrets:
      OCI_VM_SSH_KEY: { required: true }

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ inputs.node_version }}

      - name: Build
        run: |
          npm ci
          ${{ inputs.build_command }}

      - name: Package artifact
        run: tar -czf site.tar.gz "${{ inputs.build_dir }}"

      - name: Decode SSH key
        run: |
          echo "${{ secrets.OCI_VM_SSH_KEY }}" | base64 -d > "${{ runner.temp }}/key.pem"
          chmod 600 "${{ runner.temp }}/key.pem"

      - name: Upload artifact + nginx config to VM
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ inputs.vm_host }}
          username: ${{ inputs.ssh_user }}
          key_path: ${{ runner.temp }}/key.pem
          source: "site.tar.gz,${{ inputs.nginx_config_path }}"
          target: /home/${{ inputs.ssh_user }}/_deploy

      - name: Apply nginx hardening
        uses: diegovaille/deploy-templates/actions/apply-nginx-hardening@main
        with:
          vm_host: ${{ inputs.vm_host }}
          ssh_user: ${{ inputs.ssh_user }}
          ssh_key_b64: ${{ secrets.OCI_VM_SSH_KEY }}

      - name: Deploy on VM
        uses: appleboy/ssh-action@v1.0.0
        with:
          host: ${{ inputs.vm_host }}
          username: ${{ inputs.ssh_user }}
          key_path: ${{ runner.temp }}/key.pem
          script: |
            set -e
            cd /home/${{ inputs.ssh_user }}/_deploy

            echo "Publishing static build..."
            sudo rm -rf "${{ inputs.remote_path }}"
            sudo mkdir -p "${{ inputs.remote_path }}"
            sudo tar -xzf site.tar.gz -C "${{ inputs.remote_path }}" --strip-components=1
            sudo chown -R www-data:www-data "${{ inputs.remote_path }}"

            echo "Installing nginx server block..."
            sudo cp "${{ inputs.nginx_config_path }}" "/etc/nginx/sites-available/${{ inputs.site_name }}"
            sudo ln -sf "/etc/nginx/sites-available/${{ inputs.site_name }}" "/etc/nginx/sites-enabled/${{ inputs.site_name }}"

            echo "Reloading nginx..."
            sudo nginx -t && sudo systemctl reload nginx

            echo "Ensuring SSL certificate..."
            FIRST_DOMAIN=$(echo "${{ inputs.domains }}" | awk '{print $1}')
            if ! sudo certbot certificates 2>/dev/null | grep -q "$FIRST_DOMAIN"; then
              CERT_DOMAINS=""
              for d in ${{ inputs.domains }}; do CERT_DOMAINS="$CERT_DOMAINS -d $d"; done
              sudo certbot --nginx -n --agree-tos --redirect --email "${{ inputs.cert_email }}" $CERT_DOMAINS
              sudo systemctl reload nginx
            fi

            rm -rf /home/${{ inputs.ssh_user }}/_deploy

      - name: Remove SSH key
        if: always()
        run: rm -f "${{ runner.temp }}/key.pem"
```

- [ ] **Step 2: Lint the YAML**

Run: `ruby -ryaml -e "YAML.load_file('.github/workflows/deploy-static.yml'); puts 'OK'"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-static.yml
git commit -m "feat: deploy-static reusable workflow"
git push
```

---

## Task 4: `deploy-docker.yml` reusable workflow

**Files:**
- Create: `.github/workflows/deploy-docker.yml`

- [ ] **Step 1: Write the reusable workflow**

Create `.github/workflows/deploy-docker.yml`:
```yaml
name: Deploy Docker image to Oracle VM (reusable)

on:
  workflow_call:
    inputs:
      vm_host:        { required: true,  type: string }
      ssh_user:       { required: true,  type: string }
      image_name:     { required: true,  type: string }
      domains:        { required: true,  type: string }
      site_name:      { required: true,  type: string }
      nginx_config_path: { required: true, type: string }
      container_port: { required: false, type: string, default: "8080" }
      restart_policy: { required: false, type: string, default: "unless-stopped" }
      build_context:  { required: false, type: string, default: "." }
      platforms:      { required: false, type: string, default: "linux/amd64" }
      cert_email:     { required: false, type: string, default: "diegovaille@gmail.com" }
    secrets:
      OCI_VM_SSH_KEY: { required: true }
      DOCKER_ENV:     { required: true }   # full .env-format blob for the container

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - name: Build image
        run: docker buildx build --platform ${{ inputs.platforms }} -t ${{ inputs.image_name }}:latest --load "${{ inputs.build_context }}"

      - name: Save image
        run: docker save ${{ inputs.image_name }}:latest | gzip > image.tar.gz

      - name: Decode SSH key
        run: |
          echo "${{ secrets.OCI_VM_SSH_KEY }}" | base64 -d > "${{ runner.temp }}/key.pem"
          chmod 600 "${{ runner.temp }}/key.pem"

      - name: Upload image + nginx config to VM
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ inputs.vm_host }}
          username: ${{ inputs.ssh_user }}
          key_path: ${{ runner.temp }}/key.pem
          source: "image.tar.gz,${{ inputs.nginx_config_path }}"
          target: /home/${{ inputs.ssh_user }}/_deploy

      - name: Apply nginx hardening
        uses: diegovaille/deploy-templates/actions/apply-nginx-hardening@main
        with:
          vm_host: ${{ inputs.vm_host }}
          ssh_user: ${{ inputs.ssh_user }}
          ssh_key_b64: ${{ secrets.OCI_VM_SSH_KEY }}
          zone_name: api_limit
          rate: "20r/s"
          burst: "40"

      - name: Deploy on VM
        uses: appleboy/ssh-action@v1.0.0
        env:
          DOCKER_ENV: ${{ secrets.DOCKER_ENV }}
        with:
          host: ${{ inputs.vm_host }}
          username: ${{ inputs.ssh_user }}
          key_path: ${{ runner.temp }}/key.pem
          envs: DOCKER_ENV
          script: |
            set -e
            cd /home/${{ inputs.ssh_user }}/_deploy

            echo "Installing nginx server block..."
            sudo cp "${{ inputs.nginx_config_path }}" "/etc/nginx/sites-available/${{ inputs.site_name }}"
            sudo ln -sf "/etc/nginx/sites-available/${{ inputs.site_name }}" "/etc/nginx/sites-enabled/${{ inputs.site_name }}"
            sudo nginx -t && sudo systemctl reload nginx

            echo "Ensuring SSL certificate..."
            FIRST_DOMAIN=$(echo "${{ inputs.domains }}" | awk '{print $1}')
            if ! sudo certbot certificates 2>/dev/null | grep -q "$FIRST_DOMAIN"; then
              CERT_DOMAINS=""
              for d in ${{ inputs.domains }}; do CERT_DOMAINS="$CERT_DOMAINS -d $d"; done
              sudo certbot --nginx -n --agree-tos --redirect --email "${{ inputs.cert_email }}" $CERT_DOMAINS
            fi

            echo "Writing container env file..."
            printf '%s' "$DOCKER_ENV" | sudo tee /home/${{ inputs.ssh_user }}/${{ inputs.image_name }}.env > /dev/null
            sudo chmod 600 /home/${{ inputs.ssh_user }}/${{ inputs.image_name }}.env

            echo "Loading and (re)starting container..."
            gunzip -c image.tar.gz | sudo docker load
            sudo docker rm -f ${{ inputs.image_name }} || true
            sudo docker run -d --name ${{ inputs.image_name }} \
              --restart ${{ inputs.restart_policy }} \
              --env-file /home/${{ inputs.ssh_user }}/${{ inputs.image_name }}.env \
              -p ${{ inputs.container_port }}:${{ inputs.container_port }} \
              ${{ inputs.image_name }}:latest

            sudo systemctl reload nginx
            rm -rf /home/${{ inputs.ssh_user }}/_deploy

      - name: Remove SSH key
        if: always()
        run: rm -f "${{ runner.temp }}/key.pem"
```

Note: the backend's OCI private-key volume mount (`-v .../.oci:/root/.oci`) and `OCI_PRIVATE_KEY_PATH` are backend-specific. Capture them in Task 9 via the backend's `DOCKER_ENV` (path) and a `volumes` input if needed; this base template covers the common case. **If the backend needs the OCI key volume, add a `volume_mounts` input in Task 9 rather than here.**

- [ ] **Step 2: Lint the YAML**

Run: `ruby -ryaml -e "YAML.load_file('.github/workflows/deploy-docker.yml'); puts 'OK'"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-docker.yml
git commit -m "feat: deploy-docker reusable workflow"
git push
```

---

## Task 5: README with usage examples

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write full usage docs**

Replace `README.md` with consumer examples for both workflows (static + docker), documenting every input, the required `OCI_VM_SSH_KEY` / `DOCKER_ENV` secrets, the `OCI_VM_SSH_USER` / `vm_host` vars, the requirement that the consumer's nginx server block include `include snippets/hardening.conf;`, and the `@v1` pinning convention. Include the two caller snippets from the spec verbatim.

- [ ] **Step 2: Commit**

```bash
git add README.md && git commit -m "docs: usage examples for both workflows" && git push
```

---

## Task 6: Pilot — migrate `pinguimice-react` against `@main`

**Files (in `pinguimice-react`):**
- Create: `deploy/nginx.conf`
- Modify: `.github/workflows/deploy.yml`

- [ ] **Step 1: Branch**

```bash
cd ~/Git/Personal/pinguimice-react && git checkout main && git pull
git checkout -b chore/use-deploy-templates
```

- [ ] **Step 2: Add the server block as a repo file**

Create `deploy/nginx.conf` (the SSL server block, with the hardening include and the http→https redirect):
```nginx
server {
    listen 80;
    server_name pinguimice.com.br www.pinguimice.com.br;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name pinguimice.com.br www.pinguimice.com.br;

    ssl_certificate /etc/letsencrypt/live/pinguimice.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pinguimice.com.br/privkey.pem;

    root /var/www/pinguimice;
    index index.html;

    include snippets/hardening.conf;

    location ~* \.(js|css)$ {
        add_header Cache-Control "public, max-age=86400";
        try_files $uri =404;
    }
    location ~* \.html$ {
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        try_files $uri =404;
    }
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```
Note: on a brand-new domain the `ssl_certificate` paths won't exist until certbot runs. For this already-live domain the cert exists, so `nginx -t` passes. (For first-time domains, issue the cert with a temporary http-only block first — out of scope here.)

- [ ] **Step 3: Replace the workflow with a caller (against `@main` for the pilot)**

Replace `.github/workflows/deploy.yml`:
```yaml
name: Deploy Pinguim Ice Frontend to Oracle VM

on:
  push:
    branches: [ main ]
  workflow_dispatch:

jobs:
  deploy:
    uses: diegovaille/deploy-templates/.github/workflows/deploy-static.yml@main
    with:
      vm_host: ${{ vars.OCI_FRONTEND_VM_HOST }}
      ssh_user: ${{ vars.OCI_VM_SSH_USER }}
      domains: "pinguimice.com.br www.pinguimice.com.br"
      remote_path: /var/www/pinguimice
      site_name: pinguimice
      nginx_config_path: deploy/nginx.conf
    secrets: inherit
```

- [ ] **Step 4: Validate YAML**

Run: `ruby -ryaml -e "YAML.load_file('.github/workflows/deploy.yml'); puts 'OK'"`
Expected: `OK`

- [ ] **Step 5: Commit, push, open PR**

```bash
git add deploy/nginx.conf .github/workflows/deploy.yml
git commit -m "chore(deploy): use deploy-templates reusable workflow"
git push -u origin chore/use-deploy-templates
gh pr create --base main --title "chore(deploy): use deploy-templates" --body "Pilot of the reusable deploy-static workflow."
```

- [ ] **Step 6: Trigger a real deploy and watch it**

Merge the PR (or `gh workflow run` on the branch), then:
Run: `gh run watch $(gh run list --workflow=deploy.yml -L1 --json databaseId -q '.[0].databaseId')`
Expected: workflow completes green.

- [ ] **Step 7: Validate the live result**

Run:
```bash
curl -sS -o /dev/null -w "site %{http_code}\n" https://pinguimice.com.br/
curl -sS -o /dev/null -w "probe %{http_code}\n" https://pinguimice.com.br/.env
```
Expected: site `200`; probe `000` (444, connection closed).

---

## Task 7: Tag `v1`

- [ ] **Step 1: Tag and push**

```bash
cd ~/Git/pib/deploy-templates
git tag v1.0.0 && git tag -f v1
git push origin v1.0.0 && git push -f origin v1
```
Expected: tags pushed. Consumers will pin `@v1`.

---

## Task 8: Migrate remaining fronts

**Repos:** `primeira-frontend-app` (deploy.yml on main; preview-deploy.yml on `feature/new-ui-refactor`), `pinguimice-admin`.

- [ ] **Step 1: pinguimice-admin — add `deploy/nginx.conf` (with `/api/` proxy)**

In `pinguimice-admin`, branch off main. Create `deploy/nginx.conf` mirroring its current SSL block (root `/var/www/pinguimice-admin`, cert `admin.pinguimice.com.br`, `include snippets/hardening.conf;`, the three static locations, and the `location /api/ { proxy_pass https://api.primeira.app.br/api/; ... }` block).

- [ ] **Step 2: pinguimice-admin — caller workflow (`@v1`)**

Replace `.github/workflows/deploy.yml` with a `deploy-static.yml@v1` caller: `vm_host: ${{ vars.OCI_FRONTEND_VM_HOST }}`, `domains: "admin.pinguimice.com.br"`, `remote_path: /var/www/pinguimice-admin`, `site_name: pinguimice-admin`, `nginx_config_path: deploy/nginx.conf`, `ssh_user: ${{ vars.OCI_VM_SSH_USER }}`, `secrets: inherit`.

- [ ] **Step 3: pinguimice-admin — validate YAML, PR, deploy, verify**

`ruby -ryaml` lint → PR → merge/run → `gh run watch` → `curl https://admin.pinguimice.com.br/` = 200 and `/.env` = 000.

- [ ] **Step 4: primeira-frontend-app — `deploy/nginx.conf` for primeira.app.br**

Branch off main. Create `deploy/nginx.conf` (root `/var/www/frontend`, cert `primeira.app.br`, `include snippets/hardening.conf;`, the js/css + html + `/` locations). Replace `.github/workflows/deploy.yml` with a `deploy-static.yml@v1` caller (`domains: "primeira.app.br"`, `remote_path: /var/www/frontend`, `site_name: frontend`).

- [ ] **Step 5: primeira-frontend-app — validate, PR, deploy, verify**

Lint → PR to main → merge/run → watch → `curl https://primeira.app.br/` = 200, `/.env` = 000.

- [ ] **Step 6: primeira-frontend-app preview — on the feature branch**

Branch off `feature/new-ui-refactor`. Create `deploy/nginx-preview.conf` (root `/var/www/preview`, cert `preview.primeira.app.br`, include + static locations). Replace `preview-deploy.yml` with a caller using `deploy-static.yml@v1`, `domains: "preview.primeira.app.br"`, `remote_path: /var/www/preview`, `site_name: frontend-preview`, `nginx_config_path: deploy/nginx-preview.conf`, and trigger `on: push: branches-ignore: [main]`. PR into `feature/new-ui-refactor`.

---

## Task 9: Migrate the backend (`storehouse-api`)

**Files (in `storehouse-api`):**
- Create: `deploy/nginx.conf`
- Modify: `.github/workflows/deploy-backend.yml`
- GitHub repo settings: add `DOCKER_ENV` secret

- [ ] **Step 1: Create `DOCKER_ENV` secret from current env vars**

Compose the `.env` blob from the values currently passed as individual `-e` flags (SPRING_PROFILES_ACTIVE, GOOGLE_CLIENT_ID/SECRET, JWT_SECRET, ISBNDB_API_KEY, OPENAI_API_KEY, S3_ACCESS_KEY/SECRET, POSTGRES_USER/PASSWORD/HOST, OCI_USER_ID/TENANCY_ID/FINGERPRINT, OCI_PRIVATE_KEY_PATH=/root/.oci/oci_api_key.pem). Set it:
```bash
gh secret set DOCKER_ENV -R diegovaille/storehouse-api < /path/to/local.env
```
Expected: `✓ Set secret DOCKER_ENV`.

- [ ] **Step 2: Decide the OCI key volume**

The backend mounts `-v ~/.oci:/root/.oci`. Add a `volume_mounts` input to `deploy-docker.yml` (default `""`, appended to `docker run` when set) — a small generalization. Update the template, re-lint, commit, push, and move `v1` tag.

- [ ] **Step 3: Add `deploy/nginx.conf` for the API**

Create `deploy/nginx.conf` (server_name `api.primeira.app.br`, `include snippets/hardening.conf;`, `location / { proxy_pass http://localhost:8080; ...proxy headers... }`). Keep it http-only (`listen 80`); certbot mutates it to 443 at deploy time, as today.

- [ ] **Step 4: Replace the deploy workflow with a caller**

Replace `.github/workflows/deploy-backend.yml` with a `deploy-docker.yml@v1` caller: `vm_host: ${{ vars.OCI_VM_HOST }}`, `image_name: storehouse-api`, `domains: "api.primeira.app.br"`, `site_name: backend`, `nginx_config_path: deploy/nginx.conf`, `container_port: "8080"`, `volume_mounts: "-v /home/ubuntu/.oci:/root/.oci"`, `ssh_user: ${{ vars.OCI_VM_SSH_USER }}`, `secrets: inherit`. Preserve the existing "save OCI private key to ~/.oci" step if still needed — add it as a pre-step input or keep it in a thin wrapper.

- [ ] **Step 5: Validate, PR, deploy, verify**

Lint → PR to main → merge/run → `gh run watch` → verify: `curl https://api.primeira.app.br/` = 401, `/.env` = 000, and on the VM `sudo docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' storehouse-api` = `unless-stopped`.

---

## Task 10: Cleanup

- [ ] **Step 1: Confirm all five deploys are green on the new templates**

Run: `gh run list -L1` in each repo; all latest runs green.

- [ ] **Step 2: Remove the temporary spec branch in storehouse-api**

The spec now lives in `deploy-templates`. Delete the local/remote `docs/deploy-templates-spec` branch if it was pushed; otherwise drop it locally.
```bash
cd ~/Git/pib/storehouse-api && git branch -D docs/deploy-templates-spec
```

- [ ] **Step 3: Update memory**

Add a `reference` memory pointing at `diegovaille/deploy-templates` as the home of the deploy pipeline + hardening, and note `@v1` pinning.

---

## Notes on validation philosophy

There are no unit tests here — the artifacts are YAML + remote shell. Each task is validated by: (1) `ruby -ryaml` syntax lint locally, (2) a real `gh run watch` of the deploy, and (3) `curl` assertions against the live domain (legit path = expected status, probe path `/.env` = `000`). The pilot (Task 6) de-risks the templates before `v1` is tagged and the rest migrate.

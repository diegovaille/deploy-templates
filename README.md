# deploy-templates

Reusable GitHub Actions workflows for deploying to Oracle Cloud VMs.

- `.github/workflows/deploy-static.yml` — static SPA → VM/nginx
- `.github/workflows/deploy-docker.yml` — Docker image → VM/nginx
- `actions/apply-nginx-hardening` — shared nginx hardening (rate limit + probe blocking)

See `docs/specs/` for the design. Usage examples below (Task 5).

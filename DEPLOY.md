# Deploy

`.github/workflows/deploy.yml` runs on every push to `main` (and from **Actions > CI/CD > Run workflow**):

1. **test**: the Django tests on Postgres and Redis, plus a check for missing migrations. Pull requests to `main` run only this.
2. **build**: builds the image and pushes it to GitHub Container Registry as `ghcr.io/vrittech/skillshikshya-crm-backend:latest` and `:<commit>`.
3. **deploy**: copies `docker-compose.prod.yml` and a `.env` (written from the secrets below) to the server over SSH, pulls that commit's image and restarts the stack (Postgres, Redis, API, Celery worker and beat). The API runs migrations on start.

The frontend repo deploys the same way; its nginx serves the app and proxies `/api` to this API over the shared Docker network `skillshikshya`. **Deploy the backend first.**

## Server

A Linux server with Docker and the compose plugin, reachable over SSH with a key. The SSH user must be allowed to run `docker` (in the `docker` group).

## Secrets

Settings > Secrets and variables > Actions > Repository secrets. They start as `CHANGE_ME`; a secret left as `CHANGE_ME` counts as not set. The deploy stops and names any required secret that is not set; optional ones are left out of `.env`, so the app uses its default.

| Secret | Required | What |
|---|---|---|
| `SSH_HOST` | yes | Server IP or host name |
| `SSH_USER` | yes | SSH user |
| `SSH_PRIVATE_KEY` | yes | Private key whose public key is in the server's `~/.ssh/authorized_keys` |
| `SSH_PORT` | | Default `22` |
| `DEPLOY_PATH` | | Folder on the server. Default `~/skillshikshya-crm-backend` |
| `SECRET_KEY` | yes | Django secret key: `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `SETTINGS_ENCRYPTION_KEY` | | Encrypts passwords saved on the Settings page. Default: `SECRET_KEY`. Changing it later makes saved passwords unreadable |
| `ALLOWED_HOSTS` | yes | The CRM's domain, comma separated, e.g. `crm.skillshikshya.com` |
| `FRONTEND_URL` | yes | The CRM's address, e.g. `https://crm.skillshikshya.com` (used in emailed links) |
| `DB_NAME` | yes | Postgres database name |
| `DB_USER` | yes | Postgres user |
| `DB_PASSWORD` | yes | Postgres password. Set it before the first deploy: the database keeps the first one |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | | SMTP fallback; email can also be set on the Settings page |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `PUBLIC_DEVELOPMENT_URL` | | Cloudflare R2 file storage. Without them, files stay on the server (volume `media`) |
| `ORBITCHAT_API_URL`, `ORBITCHAT_EMAIL`, `ORBITCHAT_PASSWORD` | | OrbitChat |
| `PUBLIC_COURSE_API_URL` | | Website course list |
| `LEAD_INTAKE_TOKEN` | | Token the website sends to add leads |
| `GUNICORN_WORKERS` | | API worker processes. Default `3` |

Values can't contain a single quote or a line break (except `SSH_PRIVATE_KEY`).

Change a value with **Update**, then rerun the workflow (**Actions > CI/CD > the run > Re-run all jobs**, or **Run workflow**). Every deploy rewrites `.env` on the server from the secrets.

## On the server

```bash
cd ~/skillshikshya-crm-backend
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

The API also listens on `127.0.0.1:8188` on the server, for checks from the server itself.

# Skill Shikshya CRM — Backend

Django REST Framework backend for Skill Shikshya CRM.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/)
- Python 3.11+ (for local development)

---

## Quick Start (Docker)

```bash
# 1. Clone and enter the project
git clone <repo-url> crm-skillshikshya-backend
cd crm-skillshikshya-backend

# 2. Set up environment variables
cp .env.example .env
# Edit .env if needed (defaults work for local dev)

# 3. Build and start all services
docker compose up --build -d

# 4. Run database migrations
docker exec skillshikshyacrm_web python manage.py migrate

# 5. Create a superuser
docker exec -it skillshikshyacrm_web python manage.py createsuperuser
```

The API is now available at `http://localhost:8188`.

---

## Local Development (without Docker)

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment
cp .env.example .env
# Edit .env:
#   DB_HOST=localhost  (instead of 'db')
#   CELERY_BROKER_URL=redis://localhost:6379/0

# 4. Make sure PostgreSQL and Redis are running locally

# 5. Run migrations
python manage.py migrate

# 6. Create superuser
python manage.py createsuperuser

# 7. Start dev server
python manage.py runserver 8188
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `True` | Enable debug mode |
| `SECRET_KEY` | *(change me)* | Django secret key |
| `DB_ENGINE` | `django.db.backends.postgresql` | Database engine |
| `DB_NAME` | `skillshikshyacrm_db` | Database name |
| `DB_USER` | `skillshikshyacrm_user` | Database user |
| `DB_PASSWORD` | *(set a secure password)* | Database password |
| `DB_HOST` | `db` | Database host (`db` for Docker, `localhost` for local) |
| `DB_PORT` | `5432` | Database port |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts |
| `FRONTEND_URL` | `http://localhost:5173` | Frontend URL (used in password reset emails) |
| `EMAIL_HOST` | `smtp.gmail.com` | SMTP server |
| `EMAIL_PORT` | `587` | SMTP port |
| `EMAIL_USE_TLS` | `True` | Use TLS for SMTP |
| `EMAIL_HOST_USER` | *(empty)* | SMTP username |
| `EMAIL_HOST_PASSWORD` | *(empty)* | SMTP password |
| `DEFAULT_FROM_EMAIL` | `Skill Shikshya <noreply@skillshikshya.com>` | From address for emails |

---

## Useful Commands

### Docker

```bash
# Build and start
docker compose up --build -d

# Stop
docker compose down

# View logs
docker compose logs -f web
docker compose logs -f celery_worker

# Run management commands
docker exec skillshikshyacrm_web python manage.py <command>

# Open shell
docker exec -it skillshikshyacrm_web python manage.py shell

# Create migration
docker exec skillshikshyacrm_web python manage.py makemigrations
docker exec skillshikshyacrm_web python manage.py migrate

# Create superuser
docker exec -it skillshikshyacrm_web python manage.py createsuperuser
```

### Celery

```bash
# Check Celery worker status
docker exec skillshikshyacrm_celery celery -A skillshikshyacrm status
```

---

## Project Structure

```
crm-skillshikshya-backend/
├── apps/
│   ├── account/           # User auth, groups, permissions
│   ├── course/            # Course management
│   ├── branch/            # Branch/location management
│   ├── course/            # Course management
│   ├── lead/              # Lead management (source, campaign, status, calls, activities, followups)
│   ├── leadreport/        # Reports & analytics (overview, trends, revenue, conversion, counselor performance)
│   ├── student/           # Student profiles (no login)
│   ├── global_config/     # Shared pagination, permissions, file upload
│   └── app_documents/     # API documentation
├── skillshikshyacrm/      # Django project settings
├── backup/                # Backup of deprecated features
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── manage.py
└── .env.example
```

---

## API Overview

Base URL: `http://localhost:8188`

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login/` | POST | Login (sets JWT cookies) |
| `/api/auth/register/` | POST | Register (admin only) |
| `/api/auth/refresh/` | POST | Refresh token |
| `/api/auth/logout/` | POST | Logout |
| `/api/auth/change-password/` | POST | Change password |
| `/api/auth/forgot-password/` | POST | Request password reset link |
| `/api/auth/reset-password/` | POST | Reset password (uid + token) |

### Users

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/users/` | GET | List users (paginated, searchable) |
| `/api/users/export_csv/` | GET | Export users to CSV |
| `/api/users/{id}/` | GET/PATCH/DELETE | Retrieve/Update/Delete |
| `/api/users/{id}/call-minutes/add/` | POST | Top up call minutes (admin) |
| `/api/users/{id}/call-minutes/transactions/` | GET | Call minutes ledger (admin) |
| `/api/users/me/call-minutes/` | GET | Own balance + recent transactions |

### Courses

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/courses/` | GET/POST | List/Create courses |
| `/api/courses/{id}/` | GET/PUT/PATCH/DELETE | CRUD |

### Branches

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/branches/` | GET/POST | List/Create branches |
| `/api/branches/{id}/` | GET/PUT/PATCH/DELETE | CRUD |

### Leads

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/leads/` | GET/POST | List/Create leads |
| `/api/leads/export_csv/` | GET | Export to CSV |
| `/api/leads/recycle_bin/` | GET | List soft-deleted leads |
| `/api/leads/{id}/` | GET/PUT/PATCH/DELETE | CRUD (DELETE = soft delete) |
| `/api/leads/{id}/archive/` | POST | Archive lead |
| `/api/leads/{id}/restore/` | POST | Restore lead |
| `/api/leads/{id}/purge/` | POST | Permanent delete (staff only) |
| `/api/lead-sources/` | GET/POST | Lead sources (config) |
| `/api/campaigns/` | GET/POST | Campaigns (config) |
| `/api/lead-statuses/` | GET/POST | Lead statuses (config) |
| `/api/lead-priorities/` | GET/POST | Lead priorities (config) |
| `/api/lead-tags/` | GET/POST | Lead tags (config) |
| `/api/leads/{id}/calls/` | GET/POST | Lead calls |
| `/api/leads/{id}/activities/` | GET/POST | Lead activities |
| `/api/leads/{id}/followups/` | GET/POST | Lead followups |
| `/api/leads/{id}/documents/` | GET/POST | Lead documents |
| `/api/leads/{id}/course-interests/` | GET/POST | Course interests |
| `/api/leads/{id}/enrollments/` | GET/POST | Enrollments (with computed fees) |
| `/api/leads/{id}/enrollments/{eid}/payments/` | GET/POST | Enrollment payments |

### Students

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/students/` | GET/POST | List/Create students |
| `/api/students/{id}/` | GET/PUT/PATCH/DELETE | CRUD |
| `/api/students/export_csv/` | GET | Export to CSV |
| `/api/students/{id}/documents/` | GET/POST | Student documents |

### Reports

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/reports/overview/` | GET | Dashboard summary snapshot |
| `/api/reports/leads-by-status/` , `/api/reports/leads-by-source/` | GET | Breakdowns |
| `/api/reports/leads-by-type/` , `/api/reports/leads-by-branch/` | GET | Breakdowns |
| `/api/reports/leads-by-campaign/` , `/api/reports/leads-by-counselor/` | GET | Breakdowns |
| `/api/reports/lead-trend/` | GET | Leads / conversions / revenue over time |
| `/api/reports/revenue/` | GET | Collected & outstanding revenue |
| `/api/reports/enrollments/` | GET | Enrollments by course/status + fees |
| `/api/reports/conversion/` | GET | Lead funnel + conversion rates |
| `/api/reports/counselor-performance/` | GET | Per-counselor metrics |

### Global Lists

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/followups/` , `/api/enrollments/` , `/api/payments/` | GET | Flat read-only lists across all leads |
| `/api/calls/` | GET | All calls across leads — filters: `direction`, `created_by`, `student`, `lead`, date range, `search` |

### Invoices

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/invoices/` | GET | List invoices (auto-generated & emailed) |
| `/api/students/{id}/invoices/` | GET | Student invoice history |
| `/api/invoices/{id}/resend/` | POST | Re-send invoice email (admin) |
| `/api/invoices/email-config/` | GET/PUT | Global CC email config (admin) |

### File Upload

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/upload/` | POST | Upload file to R2 |

### Groups & Permissions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/groups/` | GET/POST | List/Create groups |
| `/api/groups/{id}/` | GET/PUT/PATCH/DELETE | CRUD |
| `/api/permissions/` | GET | List permissions |

### API Docs

| File | Description |
|------|-------------|
| `apps/app_documents/account_app_apis_guide.md` | Auth, users, groups, permissions |
| `apps/app_documents/course_app_apis_guide.md` | Courses |
| `apps/app_documents/branch_app_apis_guide.md` | Branches |
| `apps/app_documents/lead_app_apis_guide.md` | Leads (config, child records, filters, CSV) |
| `apps/app_documents/student_app_apis_guide.md` | Students (profiles, documents, CSV) |
| `apps/app_documents/leadreport_app_apis_guide.md` | Reports & analytics (overview, trends, revenue, conversion) |
| `apps/app_documents/invoice_app_apis_guide.md` | Invoices (auto-generation, email config) |
| `apps/app_documents/upload_api_guide.md` | File upload to R2 |

---

## Authentication Flow

1. **Login** — `POST /api/auth/login/` with `email` + `password`
2. **Cookies are set automatically** — `access_token` and `refresh_token` (HttpOnly)
3. **Authenticated requests** — cookies are sent automatically by the browser/Postman
4. **Token refresh** — `POST /api/auth/refresh/` (reads `refresh_token` cookie automatically)
5. **Logout** — `POST /api/auth/logout/` (blacklists refresh token + clears cookies)

---

## Password Reset Flow

1. **Request** — `POST /api/auth/forgot-password/` with `{"email": "..."}`
2. **Email sent** — Contains a signed one-time reset link (expires in 15 minutes)
3. **Reset** — `POST /api/auth/reset-password/` with `{"uid": "...", "token": "...", "new_password": "...", "confirm_new_password": "..."}`
4. **Confirmation email** — Sent asynchronously via Celery

> The reset link is **only sent via email** and never returned in the API response (prevents link leakage). The email is sent only if the email exists in the system; otherwise the same generic response is returned.

---

## Permissions

| User type | Access |
|-----------|--------|
| **Staff** (`is_staff=True`) | Full access to all resources |
| **Regular user with explicit permissions** | Limited to assigned Django permissions (e.g., `course.add_course`, `account.view_user`) |
| **Regular user without permissions** | Read-only access to resources |

Permissions are managed via Django Groups (`/api/groups/`). Assign users to groups, and groups to permissions.

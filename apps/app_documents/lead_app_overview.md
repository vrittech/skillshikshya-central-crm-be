# Lead App — API Overview

## Base URL

`http://localhost:8188`

---

## Branch Scoping

All endpoints support branch-level filtering via the `branch_id` query parameter.

| User type | Behavior |
|-----------|----------|
| **Staff / Superuser** | Can see data across all branches by default. Pass `?branch_id=X` to filter. |
| **Non-staff with branch** | Automatically scoped to their assigned branch — cannot see data from other branches. |
| **Non-staff without branch** | See all data (no restriction). |

The `branch_id` parameter works on every list endpoint.

**Examples:**
```
GET /api/leads/?branch_id=1
GET /api/courses/?branch_id=2
GET /api/batches/?branch_id=2
GET /api/course-batches/?branch_id=2
GET /api/lead-sources/?branch_id=1
GET /api/students/?branch_id=1&status=active
```

---

## Permission Model

| User type | Access |
|-----------|--------|
| **Staff** (`is_staff=True`) | Full CRUD on all endpoints + permanent purge |
| **Regular user** with permissions | Limited by assigned Django permissions |
| **Regular user** no permissions | Read-only — can list and view |

**Django permissions:**
- `lead.add_lead`, `lead.change_lead`, `lead.delete_lead`, `lead.view_lead`
- `lead.add_leadsource`, `lead.change_leadsource`, `lead.delete_leadsource`, `lead.view_leadsource`
- `lead.add_campaign`, `lead.change_campaign`, `lead.delete_campaign`, `lead.view_campaign`
- `lead.add_leadstatus`, `lead.change_leadstatus`, `lead.delete_leadstatus`, `lead.view_leadstatus`
- `lead.add_leadpriority`, `lead.change_leadpriority`, `lead.delete_leadpriority`, `lead.view_leadpriority`
- `lead.add_leadtag`, `lead.change_leadtag`, `lead.delete_leadtag`, `lead.view_leadtag`
- `lead.add_learningmode`, `lead.change_learningmode`, `lead.delete_learningmode`, `lead.view_learningmode`
- `lead.add_institution`, `lead.change_institution`, `lead.delete_institution`, `lead.view_institution`
- `lead.add_province`, `lead.change_province`, `lead.delete_province`, `lead.view_province`
- `lead.add_city`, `lead.change_city`, `lead.delete_city`, `lead.view_city`
- `lead.add_leadcall`, `lead.change_leadcall`, `lead.delete_leadcall`, `lead.view_leadcall`
- `lead.add_leadactivity`, `lead.change_leadactivity`, `lead.delete_leadactivity`, `lead.view_leadactivity`
- `lead.add_followup`, `lead.change_followup`, `lead.delete_followup`, `lead.view_followup`
- `lead.add_leaddocument`, `lead.change_leaddocument`, `lead.delete_leaddocument`, `lead.view_leaddocument`
- `lead.add_leadcourseinterest`, `lead.change_leadcourseinterest`, `lead.delete_leadcourseinterest`, `lead.view_leadcourseinterest`
- `lead.add_enrollment`, `lead.change_enrollment`, `lead.delete_enrollment`, `lead.view_enrollment`
- `lead.add_payment`, `lead.change_payment`, `lead.delete_payment`, `lead.view_payment`
- `lead.add_notification`, `lead.change_notification`, `lead.delete_notification`, `lead.view_notification`

---

## Configuration / Lookup Endpoints

All config endpoints follow the same pattern. Config models are **not** nested — they live at the top level.

- **GET** — List (paginated), any authenticated user
- **POST** — Create, staff or `<app>.add_*` permission required
- **GET/PUT/PATCH/DELETE** — By ID, mutation requires staff or `<app>.change_*`/`<app>.delete_*` permission

### Lead Sources

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lead-sources/` | List sources |
| POST | `/api/lead-sources/` | Create source |
| GET/PUT/PATCH/DELETE | `/api/lead-sources/{id}/` | CRUD |

**Fields:** `id`, `name`, `description`, `is_active`, `is_protected`

**Deletion rules:**

| Condition | Result |
|-----------|--------|
| Source is referenced by any lead | `400` — *"This source is used in leads so it cannot be deleted."* |
| `is_protected = true` (staff toggles via PATCH or Django Admin) | `400` — *"This lead source is protected and cannot be deleted."* |
| Otherwise | deletes normally |

The rule is also enforced at the model level (`ProtectedError`), so Django Admin and bulk deletes cannot bypass it.

**Query params:** `?search=web&name=web&is_active=true`

### Campaigns

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/campaigns/` | List campaigns |
| POST | `/api/campaigns/` | Create campaign |
| GET/PUT/PATCH/DELETE | `/api/campaigns/{id}/` | CRUD |

**Fields:** `id`, `name`, `source` (FK), `start_date`, `end_date`, `budget`, `is_active`

**Query params:** `?search=summer&source=1&is_active=true`

### Lead Statuses

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lead-statuses/` | List statuses |
| POST | `/api/lead-statuses/` | Create status |
| GET/PUT/PATCH/DELETE | `/api/lead-statuses/{id}/` | CRUD |

**Fields:** `id`, `name`, `color` (hex), `sort_order`, `is_final`, `is_active`

Lead scores use the status `sort_order` — statuses closer to the max (excluding `is_final`) contribute more to the lead score. `is_final` statuses (like "Lost") result in `lead_score = 0`.

**Query params:** `?search=new&is_active=true&is_final=false`

### Lead Priorities

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lead-priorities/` | List priorities |
| POST | `/api/lead-priorities/` | Create priority |
| GET/PUT/PATCH/DELETE | `/api/lead-priorities/{id}/` | CRUD |

**Fields:** `id`, `name`, `color` (hex), `sort_order`, `is_active`

Lead score uses `sort_order` — priority 1 (highest) gives the most points.

### Lead Tags

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lead-tags/` | List tags |
| POST | `/api/lead-tags/` | Create tag |
| GET/PUT/PATCH/DELETE | `/api/lead-tags/{id}/` | CRUD |

**Fields:** `id`, `name`, `color` (hex), `is_active`

**Query params:** `?search=course&is_active=true`

---

## Learning Mode & Institution Endpoints

### Course Interests from External Intake

The external intake endpoint accepts course interests as plain text names — not IDs:

| Field | Format | Example |
|-------|--------|---------|
| `course_interests` | `array` of strings | `["Python Advanced", "Flutter"]` |
| `course` | `string` (singular, alternative) | `"Python Advanced"` |
| `courses` | `string` (comma-separated, alternative) | `"Python Advanced, Flutter"` |

**How it's stored:**

| Scenario | `course` (FK) | `course_name` (fallback) |
|---|---|---|
| Name matches a course in the public catalog (`base.skillshikshya.com/api/course/`) | Linked to matched/imported course (amount & categories from API) | `null` |
| Public API unavailable and name matches a local CRM course | Linked to matched course | `null` |
| Name matches nothing | Auto-creates a new `Course` (amount `0.00`) | `null` (always an FK now) |

The `course_name` field on the response always shows the correct display name regardless of how it was stored.

Auto-created/imported courses should be reviewed by staff to set the real `full_amount` and `course_type`. See [Course App Guide](course_app_apis_guide.md#external-intake-course-matching) for details.

---

### Learning Modes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/learning-modes/` | List learning modes |
| POST | `/api/learning-modes/` | Create learning mode |
| GET/PUT/PATCH/DELETE | `/api/learning-modes/{id}/` | CRUD |

**Fields:** `id`, `name`, `code` (unique), `is_active`

**Query params:** `?search=online&name=Online&code=ONLINE&is_active=true`

**Default examples:** Online, Offline, Hybrid

### Institutions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/institutions/` | List institutions |
| POST | `/api/institutions/` | Create institution |
| GET/PUT/PATCH/DELETE | `/api/institutions/{id}/` | CRUD |

**Fields:** `id`, `name`, `code` (unique), `address`, `phone`, `is_active`

**Query params:** `?search=tribhuvan&name=university&code=TU&phone=01&is_active=true`

---

## Location Endpoints

### Provinces

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/provinces/` | List provinces |
| POST | `/api/provinces/` | Create province |
| GET/PUT/PATCH/DELETE | `/api/provinces/{id}/` | CRUD |

**Fields:** `id`, `name`, `code`, `is_active`

**Query params:** `?search=bag&name=bagmati&is_active=true`

### Cities

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cities/` | List cities |
| POST | `/api/cities/` | Create city |
| GET/PUT/PATCH/DELETE | `/api/cities/{id}/` | CRUD |

**Fields:** `id`, `name`, `province` (FK), `province_name` (read), `is_active`

**Query params:** `?search=kath&province=1&is_active=true`

---

## Public Lead Intake

| Method | Endpoint | Description | Access |
|--------|----------|-------------|--------|
| POST | `/api/public/lead-intake/` | Create a lead from external forms | Public (no auth) |

This endpoint allows unauthenticated lead creation from external websites, landing pages, etc.

**Request:**
```json
{
    "full_name": "Rahul Sharma",
    "phone": "9812345678",
    "email": "rahul@example.com",
    "source_name": "Website",
    "heard_from": "Google Ads",
    "course_interests": [1, 2],
    "custom_data": {
        "preferred_learning_mode": "online",
        "preferred_shift": "evening"
    }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `full_name` | Yes | Lead's full name |
| `phone` | Yes | Contact phone |
| `email` | No | Email address |
| `source_name` | No | Matches `LeadSource.name` (case-insensitive). If no match, source is set to null. |
| `heard_from` | No | Free-text "how did you hear about us" |
| `course_interests` | No | Array of course IDs |
| `custom_data` | No | Any extra fields as JSON object |

**Response `201 Created`:**
```json
{
    "id": 1,
    "reference_number": "LD-2026-000001",
    "full_name": "Rahul Sharma",
    "phone": "9812345678"
}
```

---

## OrbitChat Lead Sync

OrbitChat leads are synced hourly by the `poll-orbitchat-leads-hourly` Celery Beat task (`minute=15` UTC == on-the-hour Nepal time).

### Enable / disable (no restart needed)

Toggle via **API** (for frontend settings screens) or **Django Admin** (Orbitchat → Sync states):

| Method | Endpoint | Description | Access |
|--------|----------|-------------|--------|
| GET | `/api/orbitchat/sync-state/` | Current sync status | Admin (`is_staff`) only |
| PATCH | `/api/orbitchat/sync-state/` | Enable/disable the hourly sync | Admin (`is_staff`) only |

**GET response:**
```json
{
    "key": "orbitchat_leads",
    "is_enabled": false,
    "last_sync_at": "2026-08-26T05:48:53Z",
    "updated_at": "2026-08-26T05:48:13Z"
}
```

**PATCH request** — takes effect on the next hourly tick, no restart needed:
```json
{ "is_enabled": true }
```

- ✅ enabled — automatic runs proceed every hour
- ⬜ disabled (default) — automatic runs are skipped immediately; watermark untouched
- Manual shell runs pass `force=True` to bypass the flag:
  ```bash
  docker compose exec web python -c "
  import os, django
  os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'skillshikshyacrm.settings')
  django.setup()
  from apps.orbitchat.tasks import poll_orbitchat_leads_task
  print(poll_orbitchat_leads_task(check_limit=5, force=True))
  "
  ```

### Incremental watermark

The task tracks its last successful sync in the `orbitchat_sync_states` table (`SyncState`):

| Run type | Window used | Watermark |
|----------|-------------|-----------|
| Automatic (no args) | from `last_sync_at.date()` → today. First-ever run falls back to the last 2 days | **Advances** to now on success |
| Manual (`start_date`/`end_date`) | exactly as given (backfills) | **Never moves** |

In steady state with hourly polling this means each run fetches **today's contacts only**; the first run after midnight automatically reaches back to cover anything missed before midnight. A failed run leaves the watermark untouched, so the next run naturally retries that window.

### How it works

1. A `requests`-based client logs into `https://base.orbitchat.ai/api/auth/user/login` using `ORBITCHAT_EMAIL` / `ORBITCHAT_PASSWORD`.
2. It fetches all contacts from `GET https://base.orbitchat.ai/api/contacts` with a Bearer token.
3. Each contact is mapped to a new `Lead` via `apps.orbitchat.services.create_lead_from_orbitchat_contact`.

### Field mapping

| OrbitChat field | CRM Lead field |
|-----------------|----------------|
| `name` | `full_name` |
| `phone` | `phone` |
| `email` | `email` |
| `notes` | `notes` (also used to guess a course interest) |
| — | `primary_course` = matched/created course from notes; also stored as `course_interests[is_primary=True]` |
| `created_at` | `custom_data.created_at` |
| `id` | `custom_data.orbitchat_id` |
| `channel_type` | `source` (mapped below) |

### Channel → Source mapping

| `channel_type` | CRM `source` |
|----------------|--------------|
| `instagram` | Instagram |
| `facebook` | Facebook |
| `messenger` | Messenger |
| `whatsapp` | WhatsApp |
| `tiktok` | TikTok |
| `chat_widget` | Chat Widget |

The source is matched by name via `get_or_create_source`; if the source doesn't exist it is created.

### Deduplication

A contact is **skipped** if an existing lead already has the same `custom_data.orbitchat_id` **or** the same `phone` (only when phone is provided — contacts with no phone are not deduped against each other). This prevents duplicate leads on re-poll.

### Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ORBITCHAT_API_URL` | `https://base.orbitchat.ai` | OrbitChat base URL |
| `ORBITCHAT_EMAIL` | — | OrbitChat login email |
| `ORBITCHAT_PASSWORD` | — | OrbitChat login password |

**Note:** If the password contains `$`, quote it in `.env` (e.g. `ORBITCHAT_PASSWORD='s26@dPqB$dt2Ye'`) so docker-compose doesn't try to interpolate it.

### Manual run

```bash
# Sync contacts from the last 2 days (default window)
docker compose exec web python -c "from apps.orbitchat.tasks import poll_orbitchat_leads_task; print(poll_orbitchat_leads_task())"

# Sync a specific date range
docker compose exec web python -c "from apps.orbitchat.tasks import poll_orbitchat_leads_task; print(poll_orbitchat_leads_task(start_date='2026-08-07', end_date='2026-08-07'))"

# Limited sync (first N contacts only — useful for testing)
docker compose exec web python -c "from apps.orbitchat.tasks import poll_orbitchat_leads_task; print(poll_orbitchat_leads_task(check_limit=5))"

# Or via Celery (async, requires worker running):
docker compose exec web python -c "from apps.orbitchat.tasks import poll_orbitchat_leads_task; print(poll_orbitchat_leads_task.delay(check_limit=5, page=1, page_size=50))"
```

**Pagination control:** `page_size` maps to the backend's `limit` param (clamped to 20–200); `page` sets the starting page. The client still auto-paginates across pages unless a `page` is given, and it stops early when `check_limit` or `total` is reached.

**Response shape note:** The OrbitChat contacts API returns `{status, message, data: {contacts, total, page, limit}}`. The client follows pagination automatically (up to 200 per page) and supports the `start_date`/`end_date` query params. When no date range is given, the task defaults to the last 2 days so it doesn't re-poll the full catalog (which can be 15k+ contacts).

---

## Related Documents

| Guide | Description |
|-------|-------------|
| [Lead API Guide](lead_app_lead_api_guide.md) | Lead CRUD, actions, lead_score |
| [Calls API Guide](lead_app_calls_api_guide.md) | LeadCall endpoints |
| [Activities API Guide](lead_app_activities_api_guide.md) | LeadActivity endpoints |
| [Followups API Guide](lead_app_followup_api_guide.md) | FollowUp endpoints |
| [Documents API Guide](lead_app_documents_api_guide.md) | LeadDocument endpoints |
| [Course Interests API Guide](lead_app_course_interests_api_guide.md) | LeadCourseInterest endpoints |
| [Enrollments & Payments API Guide](lead_app_enrollments_payments_api_guide.md) | Enrollment + Payment endpoints |
| [Global List Endpoints API Guide](lead_app_global_list_api_guide.md) | Flat cross-lead lists for followups, enrollments, payments |
| [Notifications API Guide](lead_app_notifications_api_guide.md) | Notification endpoints |

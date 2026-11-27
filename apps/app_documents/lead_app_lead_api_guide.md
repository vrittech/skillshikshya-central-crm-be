# Lead App — Lead API Guide

**Base URL:** `http://localhost:8188`

## Endpoints

| Method | Endpoint | Description | Access |
|--------|----------|-------------|--------|
| GET | `/api/leads/` | List leads (paginated, excludes archived + deleted) | Authenticated |
| POST | `/api/leads/` | Create a lead | Staff or `lead.add_lead` permission |
| GET | `/api/leads/{id}/` | Get lead detail (includes `enrollment_summary`) | Authenticated |
| PUT | `/api/leads/{id}/` | Full update | Staff or `lead.change_lead` permission |
| PATCH | `/api/leads/{id}/` | Partial update | Staff or `lead.change_lead` permission |
| DELETE | `/api/leads/{id}/` | Soft delete (`is_deleted=true`) | Staff or `lead.delete_lead` permission |
| GET | `/api/leads/export_csv/` | Export filtered leads to CSV | Authenticated |
| POST | `/api/leads/{id}/archive/` | Archive lead (`is_archived=true`) | Staff or `lead.change_lead` |
| POST | `/api/leads/{id}/restore/` | Restore lead (`is_archived=false`) | Staff or `lead.change_lead` |
| POST | `/api/leads/{id}/purge/` | Permanently delete lead | Staff only |
| GET | `/api/leads/recycle_bin/` | List soft-deleted leads | Authenticated |

---

## Lead Model Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-generated |
| `reference_number` | string (read-only) | Auto-generated `LD-{YEAR}-{seq:06d}` |
| `full_name` | string | Lead's full name |
| `phone` | string | Primary phone |
| `secondary_phone` | string | Alternate phone |
| `email` | string | Email address |
| `address` | text | Street address |
| `province` | int (FK) | Province ID — see `/api/provinces/` |
| `province_name` | string (read-only) | Province display name |
| `city` | int (FK) | City ID — see `/api/cities/` |
| `city_name` | string (read-only) | City display name |
| `landmark` | string | Nearby landmark or area |
| `source` | int (FK) | Lead source ID — see `/api/lead-sources/` |
| `source_name` | string (read-only) | Source display name |
| `campaign` | int (FK) | Campaign ID — see `/api/campaigns/` |
| `campaign_name` | string (read-only) | Campaign display name |
| `branch` | int (FK) | Branch ID — see `/api/branches/` |
| `branch_name` | string (read-only) | Branch display name |
| `priority` | int (FK) | Priority ID — see `/api/lead-priorities/` |
| `priority_name` | string (read-only) | Priority display name |
| `priority_color` | string (read-only) | Priority hex color |
| `status` | int (FK) | Status ID — see `/api/lead-statuses/` |
| `status_name` | string (read-only) | Status display name |
| `status_color` | string (read-only) | Status hex color |
| `assigned_counselor` | int (FK) | User ID of assigned counselor |
| `assigned_counselor_name` | string (read-only) | Counselor's display name |
| `primary_course` | object \| int | **Nested on read:** `{"id": …, "name": …, "price": "30000.00"}` (`price` = course full_amount, `null` if unset). **On write:** accepts the bare id (`14`) or `{"id": 14}`. See `/api/courses/` |
| `lead_score` | int (read-only) | Computed score 0–100 |
| `expected_revenue` | decimal | Expected revenue amount |
| `last_contact_at` | datetime | Auto-updated on call/activity creation |
| `next_followup_at` | datetime | Auto-updated on followup creation |
| `notes` | text | Notes |
| `tags` | int[] (M2M) | Tag IDs — see `/api/lead-tags/` |
| `custom_data` | JSON | Arbitrary key-value store |
| `heard_from` | string | Free-text "how did you hear" |
| `is_converted` | bool (read-only) | Whether converted to enrollment |
| `converted_at` | datetime (read-only) | When converted |
| `is_archived` | bool | Archive flag |
| `is_deleted` | bool (read-only) | Soft delete flag |
| `deleted_at` | datetime (read-only) | When soft-deleted |
| `deleted_by` | int (read-only) | User who deleted |
| `created_by` | int (read-only) | User who created |
| `created_by_name` | string (read-only) | Creator display name |
| `updated_by` | int (read-only) | User who last updated |
| `updated_by_name` | string (read-only) | Updater display name |
| `created_at` | datetime (read-only) | Created timestamp |
| `updated_at` | datetime (read-only) | Updated timestamp |

### Lead Score (computed, never writable)

`lead_score` is automatically computed whenever the lead is updated. Newly created leads start at `0` — the score is calculated once the lead has a status, priority, or related records (calls, activities, followups). Formula:

| Factor | Max Points | Details |
|--------|-----------|---------|
| Status progress | 35 | `(status.sort_order / max_non_final_order) * 35` |
| Priority | 25 | sort_order 1=25, 2=17, 3=9, 4=3 |
| Recency of contact | 20 | Last contact: ≤3d=20, ≤7d=15, ≤14d=10, ≤30d=5 |
| Engagement | 15 | `min(15, calls*3 + activities*1)` |
| Pending followup | 5 | Has at least one pending followup with future date |
| **Converted** | 100 | Fixed at 100 |
| **Final status** | 0 | Status with `is_final=true` resets to 0 |

Total clamped to 0–100. New leads start at 0; score updates on the next `PATCH` or when a call/activity/followup is added.

---

## POST `/api/leads/` — Create Lead

`reference_number` is auto-generated — do not send it.

**Request:**
```json
{
    "full_name": "Rahul Sharma",
    "phone": "9812345678",
    "email": "rahul@example.com",
    "address": "House no. 5, near the temple",
    "province": 1,
    "city": 1,
    "landmark": "Near Durbar Square",
    "source": 1,
    "campaign": 1,
    "priority": 1,
    "status": 1,
    "primary_course": {"id": 1},   # or just: "primary_course": 1,
    "branch": 1,
    "assigned_counselor": 1,
    "notes": "Interested in full stack course",
    "tags": [1],
    "expected_revenue": "50000.00",
    "last_contact_at": "2026-07-21T10:00:00Z",
    "next_followup_at": "2026-07-25T10:00:00Z",
    "heard_from": "Google Ads",
    "custom_data": {
        "preferred_learning_mode": "online",
        "preferred_shift": "evening",
        "has_laptop": true,
        "education_level": "bachelors",
        "institution_name": "ABC College",
        "emergency_contact": {
            "name": "Shyam Sharma",
            "phone": "9841111111",
            "relationship": "Brother"
        },
        "referral": {
            "code": "FRIEND10",
            "referred_by_name": "Hari",
            "referred_by_phone": "9800000000",
            "notes": "Walk-in referral"
        }
    }
}
```

---

## GET `/api/leads/` — List Leads

**Query params:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `search` | string | Search across `full_name`, `phone`, `email`, `notes`, `reference_number` |
| `source` | int | Filter by source ID |
| `source__in` | string | Filter by multiple sources (comma-separated IDs) |
| `campaign` | int | Filter by campaign ID |
| `campaign__in` | string | Filter by multiple campaigns |
| `branch` | int | Filter by branch ID |
| `branch__in` | string | Filter by multiple branches |
| `province` | int | Filter by province ID |
| `province__in` | string | Filter by multiple provinces |
| `city` | int | Filter by city ID |
| `city__in` | string | Filter by multiple cities |
| `priority` | int | Filter by priority ID |
| `status` | int | Filter by status ID |
| `status__in` | string | Filter by multiple statuses |
| `assigned_counselor` | int | Filter by counselor user ID |
| `primary_course` | int | Filter by course ID |
| `is_converted` | bool | Filter by conversion status |
| `is_archived` | bool | Filter by archive status |
| `tags` | int | Filter by tag ID |
| `lead_score__gte` | int | Min lead score |
| `lead_score__lte` | int | Max lead score |
| `expected_revenue__gte` | number | Min expected revenue |
| `expected_revenue__lte` | number | Max expected revenue |
| `created_at__gte` | datetime | Created after |
| `created_at__lte` | datetime | Created before |
| `created_date` | date | Single created date (`YYYY-MM-DD`) — exact day |
| `from_date` | date | Created on or after this date (`YYYY-MM-DD`) |
| `to_date` | date | Created on or before this date (`YYYY-MM-DD`) |
| `next_followup_at__gte` | datetime | Followup after |
| `next_followup_at__lte` | datetime | Followup before |
| `last_contact_at__gte` | datetime | Contacted after |
| `last_contact_at__lte` | datetime | Contacted before |
| `include_archived` | bool | Include archived leads in results |
| `ordering` | string | Order by `id`, `reference_number`, `full_name`, `lead_score`, `expected_revenue`, `created_at`, `updated_at`, `last_contact_at`, `next_followup_at`, `address`, `landmark` (prefix `-` for desc) |
| `page` | int | Page number |
| `page_size` | int | Items per page (max 100) |

**List defaults:** Archived and deleted leads excluded. Use `?include_archived=true` to include archived, `?is_archived=true` to show only archived, or `/api/leads/recycle_bin/` for deleted.

**Examples:**
```
GET /api/leads/?search=LD-2026
GET /api/leads/?search=rahul
GET /api/leads/?source__in=1,2&status__in=3,4
GET /api/leads/?lead_score__gte=10&ordering=-lead_score
GET /api/leads/?include_archived=true
GET /api/leads/?is_archived=true&include_archived=true
GET /api/leads/?created_at__gte=2026-07-01T00:00:00Z
GET /api/leads/?created_date=2026-08-04
GET /api/leads/?from_date=2026-08-01&to_date=2026-08-31
GET /api/leads/?assigned_counselor=1&lead_score__gte=50
```

**Response `200 OK` (paginated):**
```json
{
    "count": 25,
    "page_size": 10,
    "total_pages": 3,
    "current_page": 1,
    "next": "http://localhost:8188/api/leads/?page=2",
    "previous": null,
    "results": [
        {
            "id": 1,
            "reference_number": "LD-2026-000001",
            "full_name": "Rahul Sharma",
            "phone": "9812345678",
            "status_name": "New",
            "priority_name": "High",
            "source_name": "Website",
            "branch_name": "Kathmandu",
            "assigned_counselor_name": "Admin",
            "lead_score": 75,
            "expected_revenue": "50000.00",
            "next_followup_at": "2026-07-25T10:00:00Z",
            "last_contact_at": "2026-07-21T10:00:00Z",
            "created_at": "2026-07-20T10:00:00Z"
        }
    ]
}
```

---

## GET `/api/leads/{id}/` — Get Lead Detail

Full response includes `call_count`, `next_followup` object, `enrollment_summary`:

```json
{
    "id": 1,
    "reference_number": "LD-2026-000001",
    "full_name": "Rahul Sharma",
    "phone": "9812345678",
    "secondary_phone": null,
    "email": "rahul@example.com",
    "address": "House no. 5",
    "province": 1,
    "province_name": "Bagmati",
    "city": 1,
    "city_name": "Kathmandu",
    "landmark": "Near Durbar Square",
    "source": 1,
    "source_name": "Website",
    "campaign": 1,
    "campaign_name": "Summer 2026",
    "branch": 1,
    "branch_name": "Kathmandu",
    "priority": 1,
    "priority_name": "High",
    "priority_color": "#ef4444",
    "status": 1,
    "status_name": "New",
    "status_color": "#2563eb",
    "assigned_counselor": 1,
    "assigned_counselor_name": "Admin",
    "primary_course": {"id": 1, "name": "Python Advanced", "price": "50000.00"},
    "lead_score": 75,
    "expected_revenue": "50000.00",
    "last_contact_at": "2026-07-21T10:00:00Z",
    "next_followup_at": "2026-07-25T10:00:00Z",
    "notes": "Interested in full stack course",
    "tags": [1, 2],
    "heard_from": "Google Ads",
    "custom_data": {"preferred_shift": "evening"},
    "is_converted": false,
    "converted_at": null,
    "is_archived": false,
    "is_deleted": false,
    "deleted_at": null,
    "deleted_by": null,
    "created_by": 1,
    "created_by_name": "Admin",
    "updated_by": 1,
    "updated_by_name": "Admin",
    "created_at": "2026-07-20T10:00:00Z",
    "updated_at": "2026-07-21T10:00:00Z",
    "call_count": 3,
    "next_followup": {
        "id": 1,
        "scheduled_at": "2026-07-25T10:00:00Z",
        "purpose": "Follow up on pricing",
        "assigned_to": 1,
        "assigned_to_name": "Admin"
    },
    "enrollment_summary": {
        "is_enrolled": true,
        "latest_status": "active",
        "course": "Python Advanced",
        "batch": "PY-2026-01"
    }
}
```

---

## PATCH `/api/leads/{id}/` — Partial Update

**Note:** `lead_score`, `reference_number`, `created_at`, `updated_at`, `created_by`, `updated_by`, `is_converted`, `converted_at`, `is_deleted`, `deleted_at`, `deleted_by` are read-only. All other fields can be updated.

```json
{
    "status": 2,
    "priority": 2,
    "custom_data": {
        "preferred_shift": "morning",
        "has_laptop": false
    },
    "next_followup_at": "2026-07-28T10:00:00Z",
    "last_contact_at": "2026-07-22T10:00:00Z"
}
```

When a lead is updated, `lead_score` is automatically recomputed.

---

## DELETE `/api/leads/{id}/` — Soft Delete

Sets `is_deleted=true`, `deleted_at`, `deleted_by`. Lead hidden from standard listings.

**Response:** `204 No Content`

---

## GET `/api/leads/export_csv/` — Export CSV

Exports filtered list as CSV. Supports all query params.

```
GET /api/leads/export_csv/?status__in=1,2&ordering=-created_at
```

**Response:** `Content-Type: text/csv` with `Content-Disposition: attachment; filename="leads.csv"`.

---

## POST `/api/leads/{id}/archive/` — Archive

Sets `is_archived=true`. Hidden from default list.

**Response:**
```json
{ "detail": "Lead archived." }
```

## POST `/api/leads/{id}/restore/` — Restore

Sets `is_archived=false`.

**Response:**
```json
{ "detail": "Lead restored." }
```

## POST `/api/leads/{id}/purge/` — Permanent Delete

Hard-deletes lead. **Staff only.** Works on active and soft-deleted leads.

**Response:** `204 No Content`

## GET `/api/leads/recycle_bin/` — Recycle Bin

Lists all soft-deleted leads (paginated, same format as lead list).

```
GET /api/leads/recycle_bin/
```

---

## Counselor Assignment Notification

When `assigned_counselor` changes on an existing lead:
1. A `Notification` record is created for the new counselor (in-app)
2. An email is sent to the counselor's email with lead details (via Celery async task)

See [Notifications API Guide](lead_app_notifications_api_guide.md) for reading/clearing notifications.

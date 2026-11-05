# Lead App — Calls API Guide

**Base URL:** `http://localhost:8188`

## Endpoints

All CRUD endpoints are nested under a specific lead. A flat cross-lead call log is also available.

| Method | Endpoint | Description | Access |
|--------|----------|-------------|--------|
| GET | `/api/calls/` | **All calls across leads** (paginated, newest first) | Authenticated |
| GET | `/api/calls/summary/` | Totals, Nepal-day activity and top 10 callers | Authenticated |
| GET | `/api/calls/export/` | Download all matching calls as UTF-8 CSV | Authenticated |
| GET | `/api/leads/{lead_id}/calls/` | List calls for one lead (paginated) | Authenticated |
| POST | `/api/leads/{lead_id}/calls/` | Add a call (consumes call minutes) | Staff or `lead.add_leadcall` permission |
| GET | `/api/leads/{lead_id}/calls/{id}/` | Get call detail | Authenticated |
| PATCH | `/api/leads/{lead_id}/calls/{id}/` | Update call notes/outcome etc. | Staff or `lead.change_leadcall` permission |

> **DELETE is disabled** — call records are permanent and cannot be deleted via the API (`405 Method Not Allowed`), so the call-minutes ledger stays trustworthy.

## Global Call Log — `GET /api/calls/`

Read-only list of every call across all leads, **branch-scoped** (staff see all / `?branch_id=X`; non-staff locked to their branch). Ordered by `call_timestamp` descending by default.

**Query params:**

| Parameter | Description |
|-----------|-------------|
| `direction` | `inbound` or `outbound` |
| `created_by` | User ID — calls logged by that user |
| `student` | Student ID — calls on the lead linked to that student |
| `lead` | Lead ID |
| `call_timestamp__gte` / `__lte` | Date/time range (ISO 8601) |
| `search` | Matches caller, phone number, outcome, notes, lead name & reference number |
| `ordering` | `call_timestamp`, `duration_seconds`, `created_at` (prefix `-` for desc) |
| `page` / `page_size` | Standard pagination |

**Example response row:**
```json
{
    "id": 24,
    "lead": 101,
    "lead_name": "Sita Kumari InvoiceTest",
    "lead_reference_number": "LD-2026-000086",
    "call_timestamp": "2026-08-26T05:48:13Z",
    "direction": "outbound",
    "caller": "Shikhar",
    "phone_number": "9877700111",
    "duration_seconds": 270,
    "duration_formatted": "04:30",
    "outcome": "Interested",
    "notes": "",
    "branch": 1,
    "created_by": 9,
    "created_by_name": "Shikhar"
}
```

The `lead` field is auto-set from the URL. `created_by` is auto-set to the requesting user.

## Call Summary — `GET /api/calls/summary/`

Accepts the same filters and search as the global call log, including `branch_id`
for staff. Pagination and ordering do not affect summary totals. No additional
model permission is needed beyond authentication.

**Example:**
```
GET /api/calls/summary/?direction=outbound&created_by=9
```

**Response:**
```json
{
    "total_calls": 3,
    "inbound": 0,
    "outbound": 3,
    "total_seconds": 300,
    "total_formatted": "00:05:00",
    "avg_seconds": 100.0,
    "connected_calls": 2,
    "connect_rate": 0.6666666666666666,
    "by_day": [{"date": "2026-10-29", "calls": 3, "seconds": 300}],
    "by_user": [{"user_id": 9, "name": "Sita", "calls": 3, "seconds": 300}]
}
```

- Average duration includes zero-second calls and is rounded to two decimal places.
- Connect rate is a fraction from 0 to 1; a connected call has `duration_seconds > 0`.
- `total_formatted` uses `HH:MM:SS`; hours may exceed 23.
- Daily buckets use `Asia/Kathmandu`, independently of the server timezone. Days
  without calls are omitted. `by_user` is grouped by `created_by`, limited to 10,
  and ordered by call count descending, then user ID. Missing users are `null` / `Unknown`.
- An empty result returns zero metrics, `00:00:00`, and empty arrays.

## Call Export — `GET /api/calls/export/`

Accepts the same filters, search and ordering as the list, but exports **all**
matching rows regardless of `page` or `page_size`.

**Response headers:**
```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="calls-2026-10-29.csv"
Cache-Control: private, no-store
```

The filename date and row timestamps use Nepal time. Columns, in order:

```
Date & time (NPT),Direction,Lead,Reference,Phone,Caller,Logged by,Duration (MM:SS),Seconds,Outcome,Notes,Recording
```

The stream starts with a UTF-8 BOM for Excel. Text beginning with `=`, `+`, `-`
or `@` (including after whitespace) is prefixed with an apostrophe. Leading
tabs and line breaks are also protected. Commas, quotes and embedded line breaks
are handled by the CSV writer. Missing values produce empty cells.

Rows use `select_related` and a 2,000-row iterator; the response does not build a
full in-memory export. Report queries remain fixed at two for a paginated list,
three for a summary, and one for an export, excluding authentication and branch lookup.

### Inclusive date ranges

Send timezone-aware boundaries for Nepal calendar days. For 29 October 2026:

```
call_timestamp__gte=2026-10-29T00:00:00+05:45
call_timestamp__lte=2026-10-29T23:59:59.999999+05:45
```

Use a query encoder so the `+` offset is encoded as `%2B`. Date-only upper bounds
mean midnight and should not be used for an inclusive last day.

### Branch access

List, detail, summary and export all pass through `BranchFilterBackend`. Non-staff
with a branch cannot override it using `branch_id`; staff and superusers see all
branches by default and can narrow the result. Existing behavior is intentionally
preserved for non-staff users with **no assigned branch**: the backend returns the
unrestricted queryset. This is a cross-branch disclosure risk. A separate change
should deny access or return `.none()` until a branch is assigned, with an audit of
existing accounts and regression tests across every branch-scoped endpoint.

## Call Minutes Deduction

Creating a call **deducts minutes from the requesting user's balance**:

- Minutes charged = the call's **exact `duration_seconds`** (no rounding). A 4m30s (270s) call deducts exactly 270 seconds; a 0-second call is recorded but deducts nothing.
- If the balance is lower than the required minutes, the call is **rejected** with:
  ```json
  { "detail": "Insufficient call minutes (required 270 seconds, available 120 seconds)." }
  ```
- On success, the response includes: `seconds_used`, `remaining_seconds`, plus floored `minutes_used` / `remaining_minutes` for convenience.
- Every deduction writes a row in the immutable ledger in **seconds** (see [Call Minutes](account_app_apis_guide.md#call-minutes)).
- `duration_seconds` and `caller` are **locked after creation** — PATCH requests silently ignore changes to them.

### Flexible duration input (`duration_input`)

Send `duration_input` instead of `duration_seconds` — the backend parses these formats into seconds:

| Input | Meaning | Stored |
|-------|---------|--------|
| `"4:19"` | 4 min 19 sec | `259` |
| `"4.19"` | 4 min 19 sec (dot = MM.SS) | `259` |
| `"04:19"` | 4 min 19 sec (leading zero) | `259` |
| `"4m19s"` | 4 min 19 sec (suffix) | `259` |
| `"1:02:30"` | 1 hr 2 min 30 sec | `3750` |
| `"259"` | raw seconds | `259` |
| `"4.5"` | 4 min **05** sec (single digit = seconds, zero-padded) | `245` |
| `"7.89"` | **carries**: 7 min 89 sec → 8 min 29 sec | `509` |
| `"7:89"` | carries: 8 min 29 sec | `509` |

> **Overflow carry:** any seconds/minutes part ≥ 60 automatically rolls up (stopwatch style) — `7.89` → 8:29, `1:99:99` → 2:40:39.

**Request:**
```json
{
    "call_timestamp": "2026-08-26T05:48:00Z",
    "caller": "Counselor",
    "phone_number": "9800000001",
    "duration_input": "4:19",
    "outcome": "Interested"
}
```
Invalid formats return `400 {"duration_input": ["Invalid duration format: …"]}`. The plain integer `duration_seconds` field is still accepted unchanged.

### Formatted duration in responses

Every call response includes `duration_formatted` (`"MM:SS"`, e.g. `"04:19"`) for display.

**Example POST response (201):**
```json
{
    "id": 21,
    "duration_seconds": 259,
    "duration_formatted": "04:19",
    "seconds_used": 259,
    "remaining_seconds": 330,
    "minutes_used": 4,
    "remaining_minutes": 5,
    "...": "…other standard fields…"
}
```

---

## Model Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-generated |
| `lead` | int (FK, read-only) | Auto-set from URL |
| `call_timestamp` | datetime | When the call occurred |
| `direction` | string | `inbound` or `outbound` |
| `caller` | string | Caller name |
| `phone_number` | string | Phone number called/received |
| `duration_seconds` | int | Duration in seconds |
| `duration_input` | string (write-only) | Flexible duration input — accepts `"4:19"`, `"4.19"`, `"04:19"`, `"4m19s"`, raw seconds, `"H:MM:SS"`. Parsed to `duration_seconds`. |
| `duration_formatted` | string (read-only) | Display format `"MM:SS"` (e.g. `"04:19"`) |
| `outcome` | string | Call outcome description |
| `notes` | text | Notes |
| `recording_url` | string | URL to call recording |
| `next_followup_at` | datetime | Suggested next followup time |
| `next_action` | text | Suggested next action |
| `branch` | int (FK, read-only) | Inherited from the lead when the call is created |
| `created_by` | int (FK, read-only) | Auto-set |
| `created_by_name` | string (read-only) | Creator display name |
| `created_at` | datetime (read-only) | Created timestamp |

### Auto-updates on Lead

When a call is created, the parent lead is automatically updated:

| Lead field | Effect |
|------------|--------|
| `last_contact_at` | Set to `call_timestamp` if newer than existing value |
| `lead_score` | Recalculated (engagement factor) |

---

## POST `/api/leads/{lead_id}/calls/` — Add a Call

**Request:**
```json
{
    "call_timestamp": "2026-07-21T12:00:00Z",
    "direction": "inbound",
    "caller": "Rahul Sharma",
    "phone_number": "9812345678",
    "duration_seconds": 120,
    "outcome": "Interested, requested demo",
    "notes": "Wants to know about pricing",
    "recording_url": null,
    "next_followup_at": null,
    "next_action": null
}
```

`direction` options: `inbound`, `outbound`

---

## GET `/api/leads/{lead_id}/calls/` — List Calls

**Query params:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `direction` | string | Filter by direction (`inbound`, `outbound`) |
| `call_timestamp__gte` | datetime | Called after |
| `call_timestamp__lte` | datetime | Called before |
| `ordering` | string | Order by `call_timestamp`, `duration_seconds` (prefix `-` for desc) |
| `page` | int | Page number |
| `page_size` | int | Items per page (max 100) |

**Examples:**
```
GET /api/leads/1/calls/?direction=inbound
GET /api/leads/1/calls/?call_timestamp__gte=2026-07-01T00:00:00Z
GET /api/leads/1/calls/?ordering=-call_timestamp
```

---

## PATCH `/api/leads/{lead_id}/calls/{id}/` — Update Call

```json
{
    "outcome": "Demo scheduled for July 25",
    "next_followup_at": "2026-07-25T10:00:00Z"
}
```

---

## DELETE `/api/leads/{lead_id}/calls/{id}/` — Delete Call

**Response:** `405 Method Not Allowed` — call records are permanent.

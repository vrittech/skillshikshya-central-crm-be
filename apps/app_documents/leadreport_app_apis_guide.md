# Lead Reports App — API Guide

## Overview

The **Lead Reports** app aggregates CRM data across leads, enrollments, payments, follow-ups, and counselors into ready-to-use report payloads for dashboards and analytics.

**Base URL:** `http://localhost:8188`

All report endpoints are `GET` and require authentication (JWT cookie). Reports are **read-only** — no mutations.

### Common Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `start_date` | `YYYY-MM-DD` | Lower bound for the report window (per-report date field). |
| `end_date` | `YYYY-MM-DD` | Upper bound for the report window. `start_date` must be ≤ `end_date`. |
| `branch_id` | `int` | **Staff only.** Restrict the report to one branch. |
| … | — | Each report also accepts lead dimension filters (see below). |

### Branch Scoping

Identical to the rest of the CRM (see [Lead App Overview](lead_app_overview.md#branch-scoping)):

| User type | Behavior |
|-----------|----------|
| **Staff / Superuser** | All branches. Pass `?branch_id=X` to scope to one branch. |
| **Non-staff with branch** | Automatically scoped to their assigned branch. |
| **Non-staff without branch** | All data (no restriction). |

### Lead Dimension Filters (apply to lead-based reports)

| Parameter | Applies to |
|-----------|------------|
| `status` | lead `status` FK id |
| `source` | lead `source` FK id |
| `campaign` | lead `campaign` FK id |
| `priority` | lead `priority` FK id |
| `assigned_counselor` | lead `assigned_counselor` user id |
| `primary_course` | lead `primary_course` FK id |
| `is_converted` | `true` / `false` |
| `is_archived` | `true` / `false` |

---

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/reports/` | List available report names |
| GET | `/api/reports/overview/` | Dashboard summary snapshot |
| GET | `/api/reports/leads-by-status/` | Lead counts per status |
| GET | `/api/reports/leads-by-source/` | Lead counts per source |
| GET | `/api/reports/leads-by-branch/` | Lead counts per branch |
| GET | `/api/reports/leads-by-campaign/` | Lead counts per campaign |
| GET | `/api/reports/leads-by-counselor/` | Lead counts per assigned counselor |
| GET | `/api/reports/lead-trend/` | Leads / conversions / revenue over time |
| GET | `/api/reports/revenue/` | Collected & outstanding revenue |
| GET | `/api/reports/enrollments/` | Enrollments by course / status + fees |
| GET | `/api/reports/conversion/` | Lead funnel stages + conversion rates |
| GET | `/api/reports/counselor-performance/` | Per-counselor productivity metrics |

---

## `GET /api/reports/`

Lists the available report names.

```json
{
  "available_reports": [
    "overview", "leads-by-status", "leads-by-source",
    "leads-by-branch", "leads-by-campaign", "leads-by-counselor",
    "lead-trend", "revenue", "enrollments", "conversion",
    "counselor-performance"
  ]
}
```

---

## `GET /api/reports/overview/`

Summary snapshot. `start_date` / `end_date` bound the `new_in_range`, `converted_in_range`, enrollment and revenue figures; `total` and `converted_total` are all-time within the same lead filters.

```json
{
  "date_range": { "start_date": "2026-08-01", "end_date": "2026-08-20" },
  "leads": {
    "total": 120, "new_in_range": 45, "converted_total": 18,
    "converted_in_range": 7, "conversion_rate": 15.0,
    "archived": 3, "lost": 9
  },
  "pipeline": {
    "active_leads": 117, "expected_revenue": "2350000.00"
  },
  "enrollments": {
    "total": 32, "revenue_collected": "910000.00"
  },
  "students": 28,
  "communication": {
    "calls": 640,
    "activities": 502,
    "followups": {
      "pending": 14, "overdue": 6, "completed": 88, "missed": 12
    }
  }
}
```

---

## `GET /api/reports/leads-by-status/`

Returns `results` rows `{status: <id>, status__name, status__color, count}`.

```json
{
  "dimension": "status", "total": 120,
  "date_range": { "start_date": null, "end_date": null },
  "results": [
    { "status": 2, "status__name": "New", "status__color": "#3b82f6", "count": 48 },
    { "status": 5, "status__name": "Enrolled", "status__color": "#22c55e", "count": 18 }
  ]
}
```

`leads-by-source`, `leads-by-branch`, `leads-by-campaign`, and `leads-by-counselor` return the same shape with their respective dimension fields (`source__name`, `branch__name`, `campaign__name`, `assigned_counselor__name`).

---

## `GET /api/reports/lead-trend/`

Time-series of leads created, conversions, and revenue collected.

| Query param | Default | Description |
|-------------|---------|-------------|
| `group_by` | `month` | Bucket size: `day`, `week`, or `month` |

```json
{
  "group_by": "month",
  "date_range": { "start_date": "2026-01-01", "end_date": "2026-12-31" },
  "results": [
    { "period": "2026-07-01", "leads": 58, "converted": 9, "revenue": "350000.00" },
    { "period": "2026-08-01", "leads": 45, "converted": 7, "revenue": "560000.00" }
  ]
}
```

---

## `GET /api/reports/revenue/`

Revenue analytics. `start_date` / `end_date` apply to `payment_date` (collected) and `enrollment_date` (outstanding). Only payments with `status=successful` are counted. Outstanding excludes cancelled / dropped-out / transferred enrollments.

```json
{
  "date_range": { "start_date": null, "end_date": null },
  "summary": {
    "total_collected": "1230000.00",
    "outstanding_to_collect": "540000.00",
    "successful_payments": 61
  },
  "by_payment_method": [
    { "payment_method": "esewa", "amount": "610000.00", "count": 33 },
    { "payment_method": "cash", "amount": "510000.00", "count": 24 }
  ],
  "by_branch": [
    { "branch__name": "Kathmandu", "amount": "860000.00", "count": 42 },
    { "branch__name": "Pokhara", "amount": "370000.00", "count": 19 }
  ]
}
```

---

## `GET /api/reports/enrollments/`

Enrollment volume and fee totals.

| Query param | Description |
|-------------|-------------|
| `course` | filter enrollments by `course` FK id |
| `enrollment_status` | filter by enrollment status (`pending`, `confirmed`, `active`, `completed`, `cancelled`, `dropped_out`, `transferred`) |

```json
{
  "date_range": { "start_date": null, "end_date": null },
  "summary": {
    "total_enrollments": 32,
    "total_listed_fee": "960000.00",
    "total_agreed_amount": "890000.00",
    "collected": "720000.00"
  },
  "by_course": [
    { "course__name": "Python Advanced", "count": 14, "agreed_total": "420000.00" }
  ],
  "by_status": [
    { "status": "active", "count": 20 },
    { "status": "confirmed", "count": 8 }
  ]
}
```

---

## `GET /api/reports/conversion/`

Lead funnel.

```json
{
  "date_range": { "start_date": null, "end_date": null },
  "funnel": {
    "total_leads": 120,
    "contacted": 101, "with_activity": 96, "interested": 74,
    "enrolled": 32, "converted": 18, "lost": 9
  },
  "rates": {
    "contacted_rate": 84.2, "with_activity_rate": 80.0,
    "interested_rate": 61.7, "enrolled_rate": 26.7,
    "converted_rate": 15.0, "lost_rate": 7.5, "conversion_rate": 15.0
  }
}
```

Funnel definitions:

| Stage | Definition |
|-------|------------|
| `total_leads` | All leads (not soft-deleted) in scope |
| `contacted` | Leads with at least one `LeadCall` |
| `with_activity` | Leads with at least one `LeadActivity` |
| `interested` | Leads with at least one `LeadCourseInterest` |
| `enrolled` | Leads with at least one `Enrollment` |
| `converted` | `is_converted = True` |
| `lost` | Lead status `is_final = True` |

---

## `GET /api/reports/counselor-performance/`

Productivity per assigned counselor (users with at least one assigned lead in scope).

| Query param | Description |
|-------------|-------------|
| `sort_by` | Sort results by a metric (default `leads_assigned`). Allowed: `leads_assigned`, `converted`, `conversion_rate`, `calls`, `activities`, `enrollments`, `revenue_collected` |

```json
{
  "date_range": { "start_date": null, "end_date": null },
  "results": [
    {
      "counselor": { "id": 2, "name": "Sita Rai", "email": "sita@example.com" },
      "leads_assigned": 40, "converted": 9, "conversion_rate": 22.5,
      "calls": 210, "activities": 168,
      "followups_assigned": 30, "followups_completed": 22, "followups_pending": 8,
      "enrollments": 14, "revenue_collected": "420000.00"
    }
  ]
}
```

---

## Errors

Invalid or inverted date ranges return `400`-style errors inside the payload:

```json
{ "error": "Invalid date. Use YYYY-MM-DD format." }
```

```json
{ "error": "start_date cannot be after end_date." }
```

---

## Related Documents

| Guide | Description |
|-------|-------------|
| [Lead App Guide](lead_app_lead_api_guide.md) | Lead CRUD, actions, lead_score |
| [Enrollments & Payments Guide](lead_app_enrollments_payments_api_guide.md) | Enrollment + Payment endpoints |
| [Lead App Overview](lead_app_overview.md) | Branch scoping, permissions, config endpoints |
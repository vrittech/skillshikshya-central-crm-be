"""Lead import and export.

Import reads a CSV or Excel file and checks every row the way the Add lead
form does, before anything is saved. Names in the file (lead type, source,
status, course, branch, counselor...) are matched to the CRM's own lists,
ignoring case and spacing. Defaults picked on the import screen fill the
blanks. A row whose phone number is already on a lead (any branch), or on an
earlier row of the same file, is a duplicate: skipped unless the user asks
to import duplicates too. Rows with errors are never imported.

Export writes the leads the list shows (same filters), as CSV or Excel. The
column names match the import, so an exported file can be edited and
imported again; the extra columns (Reference, Created at...) are ignored.
"""
import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from apps.branch.models import Branch
from apps.course.models import Course
from apps.lead.models import (
    Campaign, City, Lead, LeadPriority, LeadSource, LeadStatus, LeadTag,
    Notification, Province,
)
from apps.lead.phones import MIN_DIGITS, phone_key

NEPAL = ZoneInfo('Asia/Kathmandu')
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class ImportFileError(Exception):
    """The file can't be read at all (wrong type, too big, no name or phone column)."""


# ---------------------------------------------------------------- columns

@dataclass(frozen=True)
class Column:
    key: str
    label: str
    aliases: tuple = ()


IMPORT_COLUMNS = (
    Column('full_name', 'Full name', ('name', 'lead name', 'student name', 'fullname')),
    Column('phone', 'Phone', (
        'phone number', 'mobile', 'mobile number', 'mobile no', 'phone no', 'contact',
        'contact number', 'contact no', 'primary phone', 'phone 1',
    )),
    Column('secondary_phone', 'Second phone', (
        'secondary phone', 'alternate phone', 'alternative phone', 'other phone', 'phone 2',
        'alt phone',
    )),
    Column('email', 'Email', ('email address', 'e mail', 'mail', 'email id')),
    Column('source', 'Source', ('lead source',)),
    Column('campaign', 'Campaign', ()),
    Column('status', 'Status', ('lead status',)),
    Column('priority', 'Priority', ()),
    Column('course', 'Course', (
        'primary course', 'interested course', 'course interested', 'course name', 'program',
    )),
    Column('branch', 'Branch', ('branch name',)),
    Column('counselor', 'Counselor', (
        'counsellor', 'assigned counselor', 'assigned counsellor', 'assigned to', 'assignee',
    )),
    Column('province', 'Province', ()),
    Column('city', 'City', ()),
    Column('address', 'Address', ()),
    Column('landmark', 'Landmark', ()),
    Column('heard_from', 'Heard from', ('how did you hear', 'how did you hear about us')),
    Column('expected_revenue', 'Expected revenue', ('expected amount', 'revenue')),
    Column('tags', 'Tags', ('tag',)),
    Column('notes', 'Notes', ('note', 'remarks', 'remark', 'comment', 'comments', 'message')),
)
REQUIRED = ('full_name', 'phone')
LABEL = {c.key: c.label for c in IMPORT_COLUMNS}

EXPORT_HEADERS = (
    'Reference', 'Full name', 'Phone', 'Second phone', 'Email', 'Source',
    'Campaign', 'Status', 'Priority', 'Course', 'Other courses', 'Branch', 'Counselor',
    'Province', 'City', 'Address', 'Landmark', 'Heard from', 'Expected revenue', 'Tags',
    'Notes', 'Lead score', 'Next follow-up', 'Last contact', 'Enrollments', 'Created by',
    'Created at', 'Updated at',
)


def _norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


_HEADER_KEYS = {}
for _c in IMPORT_COLUMNS:
    for _name in (_c.key, _c.label, *_c.aliases):
        _HEADER_KEYS.setdefault(_norm(_name), _c.key)


def match_headers(headers):
    """Which column each header is. Returns ({index: key}, [ignored headers])."""
    found, ignored, taken = {}, [], set()
    for i, header in enumerate(headers):
        key = _HEADER_KEYS.get(_norm(header))
        if key and key not in taken:
            found[i] = key
            taken.add(key)
        elif str(header or '').strip():
            ignored.append(str(header).strip())
    return found, ignored


# ------------------------------------------------------------ reading files

def _cell_text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, float):
        # Excel keeps phone numbers as numbers: 9841234567.0 → "9841234567".
        return str(int(value)) if value.is_integer() else str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value).strip()


def _read_xlsx(upload):
    from openpyxl import load_workbook

    try:
        book = load_workbook(upload, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable file gets the same answer
        raise ImportFileError('This Excel file could not be read. Save it again as .xlsx.') from exc
    try:
        sheet = book.worksheets[0]
        rows = [[_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    finally:
        book.close()
    return rows


def _decode(raw):
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == 'utf-16' and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            continue
        return text
    return raw.decode('cp1252', errors='replace')


def _read_csv(upload):
    text = _decode(upload.read())
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]


def read_file(upload):
    """(header row number, headers, [(row number, cells)]) from a .csv or .xlsx upload."""
    name = (upload.name or '').lower()
    if upload.size and upload.size > MAX_BYTES:
        raise ImportFileError('The file is larger than 5 MB. Split it into smaller files.')
    if name.endswith('.xlsx'):
        rows = _read_xlsx(upload)
    elif name.endswith(('.csv', '.txt')):
        rows = _read_csv(upload)
    elif name.endswith('.xls'):
        raise ImportFileError('Old .xls files can not be read. Save the file as .xlsx or .csv.')
    else:
        raise ImportFileError('Upload a .csv or .xlsx file.')

    numbered = [(i, row) for i, row in enumerate(rows, start=1) if any(c for c in row)]
    if not numbered:
        raise ImportFileError('The file is empty.')
    header_row, headers = numbered[0]
    body = numbered[1:]
    if not body:
        raise ImportFileError('The file has column names but no leads.')
    if len(body) > MAX_ROWS:
        raise ImportFileError(f'The file has {len(body)} leads. Import at most {MAX_ROWS} at a time.')
    return header_row, headers, body


# ------------------------------------------------------------------ lookups

def _active(qs):
    model_fields = {f.name for f in qs.model._meta.get_fields()}
    return qs.filter(is_active=True) if 'is_active' in model_fields else qs


def _by_name(qs, attr='name'):
    out = defaultdict(list)
    for obj in qs:
        out[_norm(getattr(obj, attr))].append(obj)
    return out


class Lookups:
    """The CRM's lists, loaded once per import and matched by name."""

    def __init__(self):
        self.lists = {
            'source': ('Source', _by_name(_active(LeadSource.objects.all()))),
            'campaign': ('Campaign', _by_name(_active(Campaign.objects.all()))),
            'status': ('Status', _by_name(_active(LeadStatus.objects.all()))),
            'priority': ('Priority', _by_name(_active(LeadPriority.objects.all()))),
            'course': ('Course', _by_name(_active(Course.objects.all()))),
            'branch': ('Branch', _by_name(_active(Branch.objects.all()))),
        }
        users = list(get_user_model().objects.filter(is_active=True).select_related('branch'))
        self.users_by_email = {u.email.lower(): u for u in users if u.email}
        self.users_by_name = _by_name(users)
        self.provinces = _by_name(_active(Province.objects.all()))
        self.cities = _by_name(_active(City.objects.all()))
        self.tags = _by_name(_active(LeadTag.objects.all()))
        # Leads already in the CRM, by phone key, for the duplicate check.
        self.phones = defaultdict(list)
        rows = Lead.objects.filter(is_deleted=False).values_list(
            'id', 'reference_number', 'full_name', 'phone', 'secondary_phone', 'branch__name',
        )
        for pk, ref, name, phone, phone2, branch in rows.iterator(chunk_size=2000):
            match = {'id': pk, 'reference_number': ref, 'full_name': name, 'branch_name': branch}
            for key in {phone_key(phone), phone_key(phone2)}:
                if len(key) >= MIN_DIGITS:
                    self.phones[key].append(match)

    def find(self, key, value):
        """(object, error) for a name from the file."""
        label, names = self.lists[key]
        hits = names.get(_norm(value), [])
        if len(hits) == 1:
            return hits[0], None
        if hits:
            return None, f'More than one {label.lower()} is named “{value}”.'
        return None, f'{label} “{value}” is not in the CRM.'

    def find_user(self, value):
        user = self.users_by_email.get(value.strip().lower())
        if user:
            return user, None
        hits = self.users_by_name.get(_norm(value), [])
        if len(hits) == 1:
            return hits[0], None
        if hits:
            return None, f'More than one user is named “{value}”. Use their email.'
        return None, f'Counselor “{value}” is not an active user.'


# ------------------------------------------------------------- checking rows

SCIENTIFIC = re.compile(r'^\d(\.\d+)?e\+?\d+$', re.IGNORECASE)


@dataclass
class Row:
    number: int
    values: dict
    data: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    matches: list = field(default_factory=list)
    same_as_row: int | None = None

    @property
    def status(self):
        if self.errors:
            return 'error'
        if self.matches or self.same_as_row:
            return 'duplicate'
        return 'ready'

    def summary(self):
        d = self.data
        return {
            'row': self.number,
            'status': self.status,
            'full_name': self.values.get('full_name', ''),
            'phone': self.values.get('phone', ''),
            'email': self.values.get('email', ''),
            'course': d['primary_course'].name if d.get('primary_course') else '',
            'branch': d['branch'].name if d.get('branch') else '',
            'counselor': _user_name(d.get('assigned_counselor')),
            'errors': self.errors,
            'warnings': self.warnings,
            'matches': self.matches,
            'same_as_row': self.same_as_row,
        }


def _user_name(user):
    return (user.name or user.email) if user else ''


def _check_phone(row, key, value, *, required):
    label = LABEL[key]
    if not value:
        if required:
            row.errors.append(f'{label} is missing.')
        return
    if SCIENTIFIC.match(value):
        row.errors.append(
            f'{label} “{value}” lost its digits in Excel. Format the column as text and type it again.'
        )
        return
    if len(value) > 20:
        row.errors.append(f'{label} is longer than 20 characters.')
    elif len(phone_key(value)) < MIN_DIGITS:
        row.errors.append(f'{label} “{value}” is not a phone number.')
    else:
        row.data[key] = value


def _money(value):
    cleaned = re.sub(r'(?i)npr|rs\.?|,|\s', '', value)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def check_row(row, *, defaults, lookups, walled_branch):
    v = row.values
    name = v.get('full_name', '')
    if not name:
        row.errors.append('Full name is missing.')
    elif len(name) > 255:
        row.errors.append('Full name is longer than 255 characters.')
    else:
        row.data['full_name'] = name

    _check_phone(row, 'phone', v.get('phone', ''), required=True)
    _check_phone(row, 'secondary_phone', v.get('secondary_phone', ''), required=False)

    email = v.get('email', '')
    if email:
        try:
            validate_email(email)
            row.data['email'] = email.lower()
        except ValidationError:
            row.errors.append(f'Email “{email}” is not a valid email address.')

    # Names from the file, else the default picked on the import screen.
    for key, model_field in (
        ('source', 'source'), ('campaign', 'campaign'),
        ('status', 'status'), ('priority', 'priority'), ('course', 'primary_course'),
        ('branch', 'branch'),
    ):
        value = v.get(key, '')
        if value:
            obj, error = lookups.find(key, value)
            if error:
                row.errors.append(error)
                continue
        else:
            obj = defaults.get(key)
        if obj is not None:
            row.data[model_field] = obj

    if walled_branch is not None:
        branch = row.data.get('branch')
        if branch is not None and branch.pk != walled_branch.pk:
            row.errors.append(f'You can add leads only to {walled_branch.name}.')
        row.data['branch'] = walled_branch

    counselor_name = v.get('counselor', '')
    if counselor_name:
        counselor, error = lookups.find_user(counselor_name)
        if error:
            row.errors.append(error)
    else:
        counselor = defaults.get('counselor')
    if counselor is not None:
        branch = row.data.get('branch')
        if branch is not None and counselor.branch_id and counselor.branch_id != branch.pk:
            row.errors.append(f'{_user_name(counselor)} is not in {branch.name}.')
        else:
            row.data['assigned_counselor'] = counselor
            # A lead given to a branch's counselor belongs to that branch.
            if branch is None and counselor.branch_id:
                row.data['branch'] = counselor.branch

    province = None
    if v.get('province'):
        hits = lookups.provinces.get(_norm(v['province']), [])
        if len(hits) == 1:
            province = row.data['province'] = hits[0]
        else:
            row.warnings.append(f'Province “{v["province"]}” was not found, so it is left blank.')
    if v.get('city'):
        hits = lookups.cities.get(_norm(v['city']), [])
        if province is not None:
            hits = [c for c in hits if c.province_id == province.pk]
        if len(hits) == 1:
            row.data['city'] = hits[0]
            if province is None and hits[0].province_id:
                row.data['province'] = hits[0].province
        else:
            row.warnings.append(f'City “{v["city"]}” was not found, so it is left blank.')

    for key, limit in (('address', None), ('landmark', 255), ('heard_from', 500), ('notes', None)):
        text = v.get(key, '')
        if not text:
            continue
        if limit and len(text) > limit:
            row.errors.append(f'{LABEL[key]} is longer than {limit} characters.')
        else:
            row.data[key] = text

    revenue = v.get('expected_revenue', '')
    if revenue:
        amount = _money(revenue)
        if amount is None:
            row.errors.append(f'Expected revenue “{revenue}” is not an amount.')
        elif amount < 0:
            row.errors.append('Expected revenue can not be below 0.')
        elif amount >= Decimal('10000000000'):
            row.errors.append('Expected revenue is too large.')
        else:
            row.data['expected_revenue'] = amount.quantize(Decimal('0.01'))

    for tag_name in filter(None, (t.strip() for t in re.split(r'[,;]', v.get('tags', '')))):
        hits = lookups.tags.get(_norm(tag_name), [])
        if len(hits) == 1:
            row.tags.append(hits[0])
        else:
            row.warnings.append(f'Tag “{tag_name}” is not in the CRM, so it is left out.')


def check_file(upload, *, defaults, user):
    """Read and check every row. Returns (rows, matched columns, ignored headers)."""
    from apps.global_config.filters import walled_branch_id

    header_row, headers, body = read_file(upload)
    found, ignored = match_headers(headers)
    missing = [LABEL[k] for k in REQUIRED if k not in found.values()]
    if missing:
        raise ImportFileError(
            f'No {" or ".join(missing)} column was found. '
            'Download the template to see the column names.'
        )

    lookups = Lookups()
    branch_id = walled_branch_id(user)
    walled_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    rows, first_row_for = [], {}
    for number, cells in body:
        values = {key: (cells[i].strip() if i < len(cells) else '') for i, key in found.items()}
        row = Row(number=number, values=values)
        check_row(row, defaults=defaults, lookups=lookups, walled_branch=walled_branch)
        if not row.errors:
            keys = {phone_key(row.data.get('phone')), phone_key(row.data.get('secondary_phone'))}
            keys = [k for k in keys if len(k) >= MIN_DIGITS]
            seen = {m['id']: m for k in keys for m in lookups.phones.get(k, [])}
            row.matches = list(seen.values())[:5]
            earlier = [first_row_for[k] for k in keys if k in first_row_for]
            if earlier:
                row.same_as_row = min(earlier)
            for k in keys:
                first_row_for.setdefault(k, number)
        rows.append(row)
    columns = [{'header': str(headers[i]).strip(), 'column': LABEL[k]} for i, k in sorted(found.items())]
    return rows, columns, ignored


def preview(rows, columns, ignored, file_name):
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    return {
        'file_name': file_name,
        'total': len(rows),
        'ready': counts['ready'],
        'duplicates': counts['duplicate'],
        'errors': counts['error'],
        'columns': columns,
        'ignored': ignored,
        'rows': [row.summary() for row in rows],
    }


# ------------------------------------------------------------------- saving

@transaction.atomic
def import_rows(rows, *, user, include_duplicates, file_name):
    """Save the rows that passed. Each row has its own savepoint, so one bad
    row can't undo the others. Counselors hear once, not once per lead."""
    from apps.lead.enquiries import ensure_primary_enquiry

    results, created = [], []
    for row in rows:
        result = {'row': row.number, 'full_name': row.values.get('full_name', '')}
        if row.status == 'error':
            results.append({**result, 'status': 'failed', 'message': ' '.join(row.errors)})
            continue
        if row.status == 'duplicate' and not include_duplicates:
            results.append({**result, 'status': 'skipped', 'message': _duplicate_text(row)})
            continue
        try:
            with transaction.atomic():
                lead = Lead(**row.data, created_by=user, custom_data={'import_file': file_name})
                lead._skip_assign_notice = True
                lead.save()
                if row.tags:
                    lead.tags.set(row.tags)
                ensure_primary_enquiry(lead, user=user)
        except Exception as exc:  # noqa: BLE001 - the row is reported, the rest go on
            results.append({**result, 'status': 'failed', 'message': f'Could not be saved: {exc}'})
            continue
        created.append(lead)
        results.append({
            **result, 'status': 'created', 'lead_id': lead.pk,
            'reference_number': lead.reference_number,
        })

    _tell_counselors(created, user=user)
    counts = defaultdict(int)
    for r in results:
        counts[r['status']] += 1
    return {
        'file_name': file_name,
        'created': counts['created'],
        'skipped': counts['skipped'],
        'failed': counts['failed'],
        'rows': results,
    }


def _duplicate_text(row):
    if row.matches:
        m = row.matches[0]
        return f'Phone is already on {m["reference_number"]} ({m["full_name"]}).'
    return f'Same phone as row {row.same_as_row}.'


def _tell_counselors(leads, *, user):
    """One notification and one email per counselor for the leads they got."""
    from apps.lead.emails import lead_assigned, leads_assigned

    by_counselor = defaultdict(list)
    for lead in leads:
        if lead.assigned_counselor_id and lead.assigned_counselor_id != user.pk:
            by_counselor[lead.assigned_counselor].append(lead)
    for counselor, mine in by_counselor.items():
        if len(mine) == 1:
            lead = mine[0]
            Notification.objects.create(
                recipient=counselor,
                title='New Lead Assigned',
                message=f'Lead {lead.reference_number} ({lead.full_name}) has been assigned to you.',
                link=f'/leads/{lead.pk}',
            )
            lead_assigned(lead, user=user)
        else:
            Notification.objects.create(
                recipient=counselor,
                title='New Leads Assigned',
                message=f'{len(mine)} imported leads have been assigned to you.',
                link='/leads',
            )
            leads_assigned(counselor, mine, user=user)


# ----------------------------------------------------------------- template

def _sheet_lists(user):
    """The names a template's dropdowns offer."""
    from apps.global_config.filters import walled_branch_id

    branch_id = walled_branch_id(user)
    branches = _active(Branch.objects.all())
    users = get_user_model().objects.filter(is_active=True)
    if branch_id:
        branches = branches.filter(pk=branch_id)
        users = users.filter(branch_id=branch_id)

    def names(qs):
        return sorted({obj.name for obj in qs if obj.name})

    return {
        'Source': names(_active(LeadSource.objects.all())),
        'Campaign': names(_active(Campaign.objects.all())),
        'Status': names(_active(LeadStatus.objects.all())),
        'Priority': names(_active(LeadPriority.objects.all())),
        'Course': names(_active(Course.objects.all())),
        'Branch': names(branches),
        'Counselor': sorted({u.email for u in users if u.email}),
        'Tags': names(_active(LeadTag.objects.all())),
    }


def template_response(file_type, user):
    headers = [c.label for c in IMPORT_COLUMNS]
    if file_type != 'xlsx':
        return _csv_response([headers], 'lead-import-template.csv')

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    book = Workbook()
    sheet = book.active
    sheet.title = 'Leads'
    sheet.append(headers)
    for i, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=i).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(i)].width = max(14, len(header) + 4)
    sheet.freeze_panes = 'A2'
    # Phones stay text, so Excel keeps every digit.
    for key in ('phone', 'secondary_phone'):
        letter = get_column_letter(headers.index(LABEL[key]) + 1)
        for r in range(2, 1002):
            sheet[f'{letter}{r}'].number_format = '@'

    lists = book.create_sheet('Lists')
    for col, (title, values) in enumerate(_sheet_lists(user).items(), start=1):
        letter = get_column_letter(col)
        lists.cell(row=1, column=col, value=title).font = Font(bold=True)
        lists.column_dimensions[letter].width = max(16, len(title) + 4)
        for r, value in enumerate(values, start=2):
            lists.cell(row=r, column=col, value=value)
        if title == 'Tags' or not values or title not in headers:
            continue
        target = get_column_letter(headers.index(title) + 1)
        rule = DataValidation(
            type='list', formula1=f'=Lists!${letter}$2:${letter}${len(values) + 1}',
            allow_blank=True, errorStyle='warning',
            error='This is not in the CRM list.', errorTitle=title,
        )
        rule.add(f'{target}2:{target}1001')
        sheet.add_data_validation(rule)
    return _xlsx_response(book, 'lead-import-template.xlsx')


# ------------------------------------------------------------------- export

def _when(value):
    return value.astimezone(NEPAL).strftime('%Y-%m-%d %H:%M') if value else ''


def export_rows(leads, *, branch_id=None):
    yield list(EXPORT_HEADERS)
    for lead in leads:
        enquiries = [
            ci for ci in lead.course_interests.all()
            if not ci.is_primary and ci.stage != 'lost'
        ]
        enrollments = [
            e for e in lead.enrollments.all() if branch_id is None or e.branch_id == branch_id
        ]
        followups = sorted(
            (
                f for f in lead.followups.all()
                if f.status == 'pending' and (branch_id is None or f.branch_id == branch_id)
            ),
            key=lambda f: f.scheduled_at,
        )
        yield [
            lead.reference_number,
            lead.full_name,
            lead.phone or '',
            lead.secondary_phone or '',
            lead.email or '',
            lead.source.name if lead.source_id else '',
            lead.campaign.name if lead.campaign_id else '',
            lead.status.name if lead.status_id else '',
            lead.priority.name if lead.priority_id else '',
            lead.primary_course.name if lead.primary_course_id else '',
            '; '.join(ci.course.name if ci.course_id else (ci.course_name or '') for ci in enquiries),
            lead.branch.name if lead.branch_id else '',
            _user_name(lead.assigned_counselor),
            lead.province.name if lead.province_id else '',
            lead.city.name if lead.city_id else '',
            lead.address or '',
            lead.landmark or '',
            lead.heard_from or '',
            lead.expected_revenue if lead.expected_revenue is not None else '',
            ', '.join(t.name for t in lead.tags.all()),
            lead.notes or '',
            lead.lead_score,
            _when(followups[0].scheduled_at) if followups else '',
            _when(lead.last_contact_at),
            '; '.join(
                f'{e.course.name if e.course_id else "Course"} ({e.get_status_display()})'
                for e in enrollments
            ),
            _user_name(lead.created_by),
            _when(lead.created_at),
            _when(lead.updated_at),
        ]


def export_response(leads, *, file_type, branch_id=None):
    stamp = timezone.now().astimezone(NEPAL).strftime('%Y-%m-%d')
    rows = export_rows(leads, branch_id=branch_id)
    if file_type == 'xlsx':
        return _export_xlsx(rows, f'leads-{stamp}.xlsx')
    return _csv_response(rows, f'leads-{stamp}.csv')


def _csv_response(rows, filename):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    # The byte order mark tells Excel the file is UTF-8, so Nepali text shows.
    response.write('﻿')
    writer = csv.writer(response)
    for row in rows:
        writer.writerow(row)
    return response


def _export_xlsx(rows, filename):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    book = Workbook(write_only=True)
    sheet = book.create_sheet('Leads')
    sheet.freeze_panes = 'A2'
    for i, header in enumerate(EXPORT_HEADERS, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = max(12, min(40, len(header) + 6))
    for n, row in enumerate(rows):
        if n == 0:
            cells = []
            for value in row:
                cell = WriteOnlyCell(sheet, value=value)
                cell.font = Font(bold=True)
                cells.append(cell)
            sheet.append(cells)
        else:
            sheet.append([float(v) if isinstance(v, Decimal) else v for v in row])
    return _xlsx_response(book, filename)


def _xlsx_response(book, filename):
    buffer = io.BytesIO()
    book.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX_TYPE)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

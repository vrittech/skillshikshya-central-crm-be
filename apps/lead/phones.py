"""Find leads by phone number, whatever way the number was typed.

"+977 980-000-0700", "9779800000700" and "9800000700" are the same number:
only digits count, a leading 977 is dropped, and the last 10 digits are
compared. Matching looks across all branches, because a person may ask at
any branch.
"""
import re

from django.db.models import CharField, F, Func, Q, Value

from apps.lead.models import Lead

# Shorter numbers match too many leads to be useful.
MIN_DIGITS = 7


def phone_key(raw):
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) > 10 and digits.startswith('977'):
        digits = digits[3:]
    return digits[-10:]


def _digits(field):
    return Func(
        F(field), Value('[^0-9]'), Value(''), Value('g'),
        function='REGEXP_REPLACE', output_field=CharField(),
    )


def phone_matches(*phones, exclude=None):
    """Leads (not deleted) whose phone or second phone matches any of the numbers."""
    keys = {phone_key(p) for p in phones}
    keys = [k for k in keys if len(k) >= MIN_DIGITS]
    if not keys:
        return Lead.objects.none()
    match = Q()
    for key in keys:
        match |= Q(phone_digits__endswith=key) | Q(secondary_digits__endswith=key)
    leads = Lead.objects.filter(is_deleted=False).annotate(
        phone_digits=_digits('phone'), secondary_digits=_digits('secondary_phone'),
    ).filter(match)
    if exclude:
        leads = leads.exclude(pk=exclude)
    return leads.order_by('-created_at')

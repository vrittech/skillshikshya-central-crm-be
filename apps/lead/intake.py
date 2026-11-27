"""The public lead intake: the website and forms post new enquiries here.

It needs the LEAD_INTAKE_TOKEN value in an X-Intake-Token header (without a
token set, it only works while DEBUG is on), and is limited per IP. A course
name that matches no course is kept on the enquiry as text; the intake never
makes new courses.
"""
import hmac

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.global_config.throttles import LeadIntakeThrottle

from apps.lead.enquiries import add_enquiry, open_enquiry_for
from apps.lead.models import LeadCourseInterest, LeadSource, Lead
from apps.lead.phones import phone_matches
from apps.course.matching import match_or_create_course


def _course_names(data):
    courses_raw = data.get('course_interests') or data.get('course') or data.get('courses', '')
    if isinstance(courses_raw, str):
        return [c.strip() for c in courses_raw.split(',') if c.strip()]
    if isinstance(courses_raw, list):
        return [str(c).strip() for c in courses_raw if str(c).strip()]
    return []


def _token_ok(request):
    expected = settings.LEAD_INTAKE_TOKEN
    if not expected:
        return settings.DEBUG
    given = request.headers.get('X-Intake-Token', '')
    return hmac.compare_digest(given.encode(), expected.encode())


def _course(name):
    return match_or_create_course(name, create_unknown=False)


def _has_laptop(value):
    """A form's "false", "0", "no", "off" or "" is False."""
    if isinstance(value, str):
        return value.strip().lower() not in ('false', '0', 'no', 'off', '')
    return bool(value)


def _too_long(*, full_name, phone, email, heard_from, course_names):
    """Field errors for values longer than the CRM keeps (a 400, not a database error)."""
    def limit(model, field):
        return model._meta.get_field(field).max_length

    checks = [
        ('name', 'Name', [full_name], limit(Lead, 'full_name')),
        ('phone_number', 'Phone number', [phone], limit(Lead, 'phone')),
        ('email', 'Email', [email], limit(Lead, 'email')),
        ('heard_from', 'Heard from', [heard_from], limit(Lead, 'heard_from')),
        ('course_interests', 'A course name', course_names, limit(LeadCourseInterest, 'course_name')),
    ]
    return {
        key: [f'{label} is longer than {most} characters.']
        for key, label, texts, most in checks
        if any(len(str(text or '')) > most for text in texts)
    }


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LeadIntakeThrottle])
def lead_intake(request):
    if not _token_ok(request):
        return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
    data = request.data

    full_name = data.get('name') or data.get('full_name')
    if not full_name:
        return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)

    phone = data.get('phone_number') or data.get('phone')
    if not phone:
        return Response({'detail': 'phone_number is required.'}, status=status.HTTP_400_BAD_REQUEST)

    email = data.get('email', '')
    address = data.get('address', '')
    heard_from = data.get('heard_from', '')

    errors = _too_long(
        full_name=full_name, phone=phone, email=email, heard_from=heard_from,
        course_names=_course_names(data),
    )
    if errors:
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    source_name = data.get('source_name') or data.get('source', '')
    source = None
    if source_name:
        source = LeadSource.objects.filter(name__iexact=source_name.strip()).first()

    custom_data = {}
    if data.get('education_level'):
        custom_data['education_level'] = data['education_level']
    if data.get('learning_format'):
        custom_data['preferred_learning_mode'] = data['learning_format']
    if data.get('has_laptop') is not None:
        custom_data['has_laptop'] = _has_laptop(data['has_laptop'])
    if data.get('referred_by'):
        custom_data['referral'] = {
            'referred_by_name': data['referred_by'],
            'notes': 'Auto-created from external intake',
        }

    existing = phone_matches(phone).first()
    if existing is not None:
        # The same person asking again: add the new courses to their lead.
        for name in _course_names(data):
            course, _ = _course(name)
            if open_enquiry_for(existing, course, name):
                continue
            add_enquiry(existing, course=course, course_name=name, source=source, interest_level='high')
        return Response(
            {'reference_number': existing.reference_number, 'id': existing.id, 'existing': True},
            status=status.HTTP_200_OK,
        )

    lead = Lead.objects.create(
        full_name=full_name,
        phone=phone,
        email=email,
        address=address,
        source=source,
        heard_from=heard_from,
        custom_data=custom_data,
        notes=data.get('notes', ''),
    )

    for i, name in enumerate(_course_names(data)):
        course, _ = _course(name)
        if open_enquiry_for(lead, course, name):
            continue
        add_enquiry(lead, course=course, course_name=name, is_primary=(i == 0), interest_level='high')
        if i == 0 and course and lead.primary_course_id is None:
            lead.primary_course = course
            lead.save(update_fields=['primary_course', 'lead_score'])

    return Response(
        {'reference_number': lead.reference_number, 'id': lead.id},
        status=status.HTTP_201_CREATED,
    )

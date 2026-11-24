from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.lead.enquiries import add_enquiry, open_enquiry_for
from apps.lead.models import LeadSource, LeadType, Lead
from apps.course.matching import match_or_create_course


@api_view(['POST'])
@permission_classes([AllowAny])
def lead_intake(request):
    data = request.data

    full_name = data.get('name') or data.get('full_name')
    if not full_name:
        return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)

    phone = data.get('phone_number') or data.get('phone')
    if not phone:
        return Response({'detail': 'phone_number is required.'}, status=status.HTTP_400_BAD_REQUEST)

    email = data.get('email', '')
    address = data.get('address', '')

    source_name = data.get('source_name') or data.get('source', '')
    source = None
    if source_name:
        source = LeadSource.objects.filter(name__iexact=source_name.strip()).first()

    heard_from = data.get('heard_from', '')

    lead_type = LeadType.objects.first()

    custom_data = {}
    if data.get('education_level'):
        custom_data['education_level'] = data['education_level']
    if data.get('learning_format'):
        custom_data['preferred_learning_mode'] = data['learning_format']
    if data.get('has_laptop') is not None:
        custom_data['has_laptop'] = bool(data['has_laptop'])
    if data.get('referred_by'):
        custom_data['referral'] = {
            'referred_by_name': data['referred_by'],
            'notes': 'Auto-created from external intake',
        }

    lead = Lead.objects.create(
        full_name=full_name,
        phone=phone,
        email=email,
        address=address,
        source=source,
        heard_from=heard_from,
        lead_type=lead_type,
        custom_data=custom_data,
        notes=data.get('notes', ''),
    )

    courses_raw = data.get('course_interests') or data.get('course') or data.get('courses', '')
    if isinstance(courses_raw, str):
        course_names = [c.strip() for c in courses_raw.split(',') if c.strip()]
    elif isinstance(courses_raw, list):
        course_names = courses_raw
    else:
        course_names = []

    for i, name in enumerate(course_names):
        course, _ = match_or_create_course(name)
        if course is not None and open_enquiry_for(lead, course):
            continue
        add_enquiry(lead, course=course, course_name=name, is_primary=(i == 0), interest_level='high')
        if i == 0 and course and lead.primary_course_id is None:
            lead.primary_course = course
            lead.save(update_fields=['primary_course', 'lead_score'])

    return Response(
        {'reference_number': lead.reference_number, 'id': lead.id},
        status=status.HTTP_201_CREATED,
    )

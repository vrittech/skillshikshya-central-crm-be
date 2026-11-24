import logging

from apps.course.matching import match_or_create_course
from apps.lead.enquiries import add_enquiry
from apps.lead.models import Lead, LeadSource, LeadType

logger = logging.getLogger(__name__)

CHANNEL_SOURCE_NAMES = {
    'instagram': 'Instagram',
    'facebook': 'Facebook',
    'messenger': 'Messenger',
    'whatsapp': 'WhatsApp',
    'tiktok': 'TikTok',
    'chat_widget': 'Chat Widget',
}


def get_or_create_source(channel_type):
    source_name = CHANNEL_SOURCE_NAMES.get(channel_type or '')
    if not source_name:
        return None
    source, _ = LeadSource.objects.get_or_create(
        name__iexact=source_name,
        defaults={'name': source_name},
    )
    return source


def _lead_exists(contact):
    if contact.get('id') and Lead.objects.filter(custom_data__orbitchat_id=contact['id']).exists():
        return True
    phone = contact.get('phone')
    if phone and Lead.objects.filter(phone=phone).exists():
        return True
    return False


def create_lead_from_orbitchat_contact(contact):
    if _lead_exists(contact):
        return None

    full_name = (contact.get('name') or '').strip()
    phone = (contact.get('phone') or '').strip() or None
    if not full_name:
        logger.warning('OrbitChat contact missing name: %s', contact.get('id'))
        return None

    source = get_or_create_source(contact.get('channel_type'))

    custom_data = {'orbitchat_id': contact.get('id')}
    if contact.get('created_at'):
        custom_data['orbitchat_created_at'] = contact['created_at']
    custom_data['orbitchat_channel'] = contact.get('channel_type')

    lead = Lead.objects.create(
        full_name=full_name,
        phone=phone,
        email=contact.get('email') or '',
        source=source,
        heard_from=CHANNEL_SOURCE_NAMES.get(contact.get('channel_type')),
        lead_type=LeadType.objects.first(),
        notes=contact.get('notes') or '',
        custom_data=custom_data,
    )

    notes = contact.get('notes')
    if notes:
        course, _ = match_or_create_course(notes.strip())
        add_enquiry(
            lead, course=course, course_name=notes.strip(), is_primary=True, interest_level='high',
        )
        if course and lead.primary_course_id is None:
            lead.primary_course = course
            lead.save(update_fields=['primary_course', 'lead_score'])

    logger.info('Created lead %s from OrbitChat contact %s', lead.reference_number, contact.get('id'))
    return lead

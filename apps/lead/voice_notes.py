"""Voice notes: where the recordings are kept, and the checks on upload.

Recordings go to R2 when it is set up. Without R2 (a laptop running the CRM)
they go to the local media folder, so the feature still works. Files are
never public: VoiceNoteViewSet.audio streams them to users who can see the lead.
"""
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.global_config.utilities.r2_storage import get_r2_client
from apps.lead.models import VoiceNote

MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 10 * 60
# Chrome and Firefox record webm or ogg; Safari records mp4.
AUDIO_TYPES = {
    'audio/webm': 'webm',
    'audio/ogg': 'ogg',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
}


def base_type(content_type):
    """'audio/webm;codecs=opus' -> 'audio/webm'."""
    return (content_type or '').split(';')[0].strip().lower()


def _use_r2():
    return bool(settings.R2_BUCKET_NAME and settings.R2_ACCESS_KEY_ID)


def save_file(file, *, lead_id, content_type):
    """Store the recording and return its key."""
    key = f'voice-notes/{lead_id}/{uuid.uuid4().hex}.{AUDIO_TYPES[content_type]}'
    if _use_r2():
        get_r2_client().put_object(
            Bucket=settings.R2_BUCKET_NAME, Key=key, Body=file.read(), ContentType=content_type,
        )
        return key
    return default_storage.save(key, file)


def read_file(key):
    if _use_r2():
        obj = get_r2_client().get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
        return obj['Body'].read()
    with default_storage.open(key, 'rb') as f:
        return f.read()


def delete_file(key):
    if _use_r2():
        get_r2_client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    elif default_storage.exists(key):
        default_storage.delete(key)


@receiver(post_delete, sender=VoiceNote)
def remove_recording(sender, instance, **kwargs):
    """The file goes once the delete is saved (also when the whole lead is deleted)."""
    key = instance.file_key
    if key:
        transaction.on_commit(lambda: delete_file(key))

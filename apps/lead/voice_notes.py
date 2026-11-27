"""Voice notes: where the recordings are kept, and the checks on upload.

Saving a recording writes it to the local media folder and answers at once.
After the save is committed, the Celery worker copies the file to R2 and
removes the local copy, retrying if R2 is slow or down. Until then the
recording plays from the local copy. Without R2 (a laptop running the CRM)
the file simply stays in the local media folder. The web app and the worker
must share that folder (in docker-compose both mount the project folder).

Files are never public: VoiceNoteViewSet.audio streams them to users who can
see the lead.

Deleting a voice note (or deleting a lead forever) removes the row at once and
hands the file to the Celery worker, which deletes it from wherever it is and
retries if R2 is slow or down. Moving a lead to the recycle bin keeps its
recordings.
"""
import logging
import uuid

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.global_config.integrations import r2_config
from apps.global_config.utilities.r2_storage import get_r2_client
from apps.lead.models import VoiceNote
from apps.lead.tasks import delete_voice_note_file_task, upload_voice_note_file_task

logger = logging.getLogger(__name__)

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


def r2_in_use():
    """The R2 account in use, or None to keep files in the local media folder."""
    cfg = r2_config()
    return cfg if cfg.ready else None


def new_key(lead_id, content_type):
    return f'voice-notes/{lead_id}/{uuid.uuid4().hex}.{AUDIO_TYPES[content_type]}'


def save_file(file, *, lead_id, content_type):
    """Write the recording to the local media folder and return its key."""
    return default_storage.save(new_key(lead_id, content_type), file)


def upload_file(key, content_type):
    """Move a local recording to R2. Run by the worker; safe to run twice.

    Returns what happened, for the worker log.
    """
    cfg = r2_in_use()
    if not cfg:
        return 'kept in the local media folder (R2 is not set up)'
    if not default_storage.exists(key):
        return 'nothing to move (already moved or deleted)'
    if not VoiceNote.objects.filter(file_key=key).exists():
        default_storage.delete(key)
        return 'voice note was deleted; removed the local file'
    with default_storage.open(key, 'rb') as f:
        body = f.read()
    get_r2_client(cfg).put_object(Bucket=cfg.bucket_name, Key=key, Body=body, ContentType=content_type)
    default_storage.delete(key)
    return f'moved to R2 bucket {cfg.bucket_name}'


def queue_upload(key, content_type):
    """Put "move this file to R2" on the Redis queue for the worker.

    If Redis can't be reached, the file stays in the local media folder and
    still plays; the nightly sweep queues it again.
    """
    if not r2_in_use():
        return
    try:
        upload_voice_note_file_task.delay(key, content_type)
    except Exception:  # noqa: BLE001 - the recording is saved locally; don't fail the request
        logger.warning('Could not queue the upload of %s; it stays local for now.', key, exc_info=True)


def record(file, *, lead, enquiry=None, duration_seconds=0, note='', user=None):
    """Save a recording and its VoiceNote row; the move to R2 is queued after commit.

    `file` must already have passed VoiceNoteSerializer's checks.
    """
    content_type = base_type(file.content_type)
    key = save_file(file, lead_id=lead.id, content_type=content_type)
    voice_note = VoiceNote.objects.create(
        lead=lead,
        enquiry=enquiry,
        file_key=key,
        content_type=content_type,
        size_bytes=file.size,
        duration_seconds=duration_seconds or 0,
        note=note or '',
        branch=getattr(user, 'branch', None) or lead.branch,
        created_by=user,
    )
    transaction.on_commit(lambda: queue_upload(key, content_type))
    return voice_note


def read_file(key):
    """The recording's bytes: the local copy while it waits for the worker, else R2."""
    if default_storage.exists(key):
        with default_storage.open(key, 'rb') as f:
            return f.read()
    cfg = r2_in_use()
    if not cfg:
        raise FileNotFoundError(key)
    obj = get_r2_client(cfg).get_object(Bucket=cfg.bucket_name, Key=key)
    return obj['Body'].read()


def delete_file(key):
    """Remove the recording from the local media folder and from R2, wherever it is."""
    if default_storage.exists(key):
        default_storage.delete(key)
    cfg = r2_in_use()
    if cfg:
        get_r2_client(cfg).delete_object(Bucket=cfg.bucket_name, Key=key)


def queue_file_delete(key):
    """Put "delete this file" on the Redis queue for the worker.

    If Redis can't be reached, delete it now instead. If that fails too,
    the file stays in storage and the error is logged.
    """
    try:
        delete_voice_note_file_task.delay(key)
    except Exception:  # noqa: BLE001 - the row is already gone; don't fail the request
        logger.warning('Could not queue the delete of %s; deleting it now.', key, exc_info=True)
        try:
            delete_file(key)
        except Exception:  # noqa: BLE001
            logger.exception('Could not delete voice note file %s.', key)


@receiver(post_delete, sender=VoiceNote)
def remove_recording(sender, instance, **kwargs):
    """Queue the file's removal once the delete is saved.

    Runs for each voice note, also when the whole lead is deleted forever
    (the notes go with it by CASCADE). on_commit waits for the delete to be
    saved: if it is rolled back, the row comes back and so must the file.
    """
    key = instance.file_key
    if key:
        transaction.on_commit(lambda: queue_file_delete(key))

"""Nightly clean-up: recordings in storage that no voice note points to.

How a file can be left behind:
- the delete job gave up after all its retries (R2 down for hours),
- Redis and R2 were both down when the note was deleted,
- the upload reached storage but saving the voice note row failed.

The sweep lists every file under voice-notes/ in the storage in use (R2, or
the local media folder), keeps each file that has a VoiceNote row, and
deletes the rest. Files younger than a day are left alone, because an upload
may still be on its way to becoming a row. Leads in the recycle bin still
have their rows, so their recordings stay.

Safety stop: if more than 5 files and more than 10% of all files look
orphaned, nothing is deleted. That pattern means the CRM is looking at the
wrong database or the wrong bucket (for example a laptop saved with the live
R2 keys), not a few failed deletes. --force overrides it.

The same run also queues again any recording still waiting in the local
media folder to move to R2 (Redis was down when it was saved, or the upload
job gave up). Those files are never deleted here: they may be the only copy.

Runs from Celery beat every night (settings.CELERY_BEAT_SCHEDULE) and by hand
with `python manage.py sweep_voice_note_files --dry-run`.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.files.storage import default_storage
from django.utils import timezone

from apps.lead.models import VoiceNote
from apps.lead.voice_notes import delete_file, queue_upload, r2_in_use
from apps.global_config.utilities.r2_storage import get_r2_client

logger = logging.getLogger(__name__)

PREFIX = 'voice-notes/'
MIN_AGE = timedelta(hours=24)
ALWAYS_OK = 5        # this many orphans can always be cleaned up
MAX_SHARE = 0.10     # beyond that, stop if more than this share of files is orphaned
PAGE = 1000


@dataclass
class SweepResult:
    storage: str = ''
    checked: int = 0
    kept: int = 0
    too_new: int = 0
    orphans: list = field(default_factory=list)   # [(key, last_modified)]
    deleted: int = 0
    failed: list = field(default_factory=list)    # [(key, error)]
    stopped: bool = False

    def summary(self):
        text = (
            f'{self.storage}: checked {self.checked} files, {self.kept} in use, '
            f'{self.too_new} too new, {len(self.orphans)} with no voice note, '
            f'{self.deleted} deleted'
        )
        if self.failed:
            text += f', {len(self.failed)} failed'
        if self.stopped:
            text += ' (safety stop: nothing deleted)'
        return text


def stored_files():
    """(storage name, iterator of (key, last_modified)) for every file under voice-notes/."""
    cfg = r2_in_use()
    if cfg:
        return f'R2 bucket {cfg.bucket_name}', _r2_files(cfg)
    return 'Local media folder', _local_files(PREFIX.rstrip('/'))


def _r2_files(cfg):
    paginator = get_r2_client(cfg).get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=cfg.bucket_name, Prefix=PREFIX):
        for obj in page.get('Contents', []):
            yield obj['Key'], obj['LastModified']


def _local_files(folder):
    if not default_storage.exists(folder):
        return
    dirs, files = default_storage.listdir(folder)
    for name in files:
        key = f'{folder}/{name}'
        yield key, default_storage.get_modified_time(key)
    for sub in dirs:
        yield from _local_files(f'{folder}/{sub}')


def _pages(items, size=None):
    size = size or PAGE
    page = []
    for item in items:
        page.append(item)
        if len(page) == size:
            yield page
            page = []
    if page:
        yield page


def sweep_orphan_files(*, dry_run=False, force=False, min_age=MIN_AGE, now=None):
    now = now or timezone.now()
    cutoff = now - min_age
    storage, files = stored_files()
    result = SweepResult(storage=storage)

    for page in _pages(files):
        keys = [key for key, _ in page]
        in_use = set(VoiceNote.objects.filter(file_key__in=keys).values_list('file_key', flat=True))
        for key, modified in page:
            result.checked += 1
            if key in in_use:
                result.kept += 1
            elif modified > cutoff:
                result.too_new += 1
            else:
                result.orphans.append((key, modified))

    count = len(result.orphans)
    if count > ALWAYS_OK and count > result.checked * MAX_SHARE and not force:
        result.stopped = True
        logger.error(
            'Voice note sweep stopped: %s of %s files in %s have no voice note. '
            'Check that the CRM points at the right database and bucket.',
            count, result.checked, storage,
        )
        return result

    if dry_run:
        return result

    for key, _ in result.orphans:
        try:
            delete_file(key)
        except Exception as exc:  # noqa: BLE001 - keep going; tomorrow's run tries again
            result.failed.append((key, str(exc)))
            logger.warning('Voice note sweep could not delete %s: %s', key, exc)
        else:
            result.deleted += 1
    return result


def queue_waiting_uploads(*, min_age=timedelta(hours=1), now=None):
    """With R2 in use, queue the move again for local recordings that have a voice note.

    Files younger than `min_age` are left to the job queued when they were
    saved. Returns how many were queued.
    """
    if not r2_in_use():
        return 0
    cutoff = (now or timezone.now()) - min_age
    queued = 0
    for page in _pages(_local_files(PREFIX.rstrip('/'))):
        types = dict(
            VoiceNote.objects.filter(file_key__in=[key for key, _ in page])
            .values_list('file_key', 'content_type')
        )
        for key, modified in page:
            if key in types and modified <= cutoff:
                queue_upload(key, types[key])
                queued += 1
    return queued

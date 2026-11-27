import logging
import re

from botocore.exceptions import ClientError
from django.apps import apps
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotFound

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.global_config.filters import walled_branch_id
from apps.global_config.integrations import r2_config
from apps.global_config.permissions import OPEN, can, reads
from apps.global_config.utilities.r2_storage import upload_file_to_r2, get_r2_client

logger = logging.getLogger(__name__)

# Folders an upload may go to: letters, digits, dashes and slashes.
UPLOAD_FOLDER = re.compile(r'[a-z0-9][a-z0-9_/-]{0,60}')
# Kept private behind their own checked addresses.
PRIVATE_PREFIXES = ('voice-notes',)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_file(request):
    file = request.FILES.get('file')
    if not file:
        return Response({'detail': 'No file provided.'}, status=status.HTTP_400_BAD_REQUEST)

    if file.size > 10 * 1024 * 1024:
        return Response({'detail': 'File size exceeds 10MB limit.'}, status=status.HTTP_400_BAD_REQUEST)

    allowed = [
        'image/jpeg', 'image/png', 'image/gif', 'image/webp',
        'application/pdf',
        'text/csv',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/msword', 'application/vnd.ms-excel',
        'text/plain',
    ]
    if file.content_type not in allowed:
        return Response(
            {'detail': f'File type {file.content_type} not allowed.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    folder = request.data.get('folder', 'uploads')
    if not UPLOAD_FOLDER.fullmatch(folder or '') or folder.startswith(PRIVATE_PREFIXES):
        return Response({'detail': 'Pick a valid folder.'}, status=status.HTTP_400_BAD_REQUEST)
    result = upload_file_to_r2(file, folder=folder)

    return Response(result, status=status.HTTP_201_CREATED)


# Records that can hold a stored file's address: (model, field, the right to
# see the record, its branch field or None, shared with every branch when it
# has no branch).
FILE_HOLDERS = (
    ('lead.LeadDocument', 'file', 'lead.view_leaddocument', 'branch', False),
    ('lead.LeadCall', 'recording_url', 'lead.view_leadcall', 'branch', False),
    ('student.StudentDocument', 'file', 'student.view_studentdocument', 'branch', False),
    ('student.Student', 'photo_url', 'student.view_student', 'branch', False),
    ('course.Course', 'syllabus', OPEN, 'branch', True),
    ('global_config.OrganisationSettings', 'logo_url', OPEN, None, False),
)


def _held_by_visible_record(request, key):
    """True when the user can see a record that holds exactly this file.

    A record holds it as the address /api/upload/ gives (/api/files/<key>),
    or as that address on this server.
    """
    path = f'/api/files/{key}'
    host = request.get_host()
    addresses = [path, f'https://{host}{path}', f'http://{host}{path}']
    user = request.user
    branch_id = walled_branch_id(user)
    for label, field, right, branch_field, shared in FILE_HOLDERS:
        if not can(user, right):
            continue
        records = apps.get_model(label).objects.filter(**{f'{field}__in': addresses})
        if branch_id is not None and branch_field:
            mine = Q(**{f'{branch_field}_id': branch_id})
            if shared:
                mine |= Q(**{f'{branch_field}__isnull': True})
            records = records.filter(mine)
        if records.exists():
            return True
    return False


@api_view(['GET'])
@permission_classes([IsAuthenticated, reads(OPEN)])
def serve_file(request, key):
    """A stored file, for signed-in users who can see a record that holds it.

    Voice notes are never served here (they have their own checked address).
    A file on a document needs the right to see that kind of document, and
    one of another branch is hidden from branch users. A file no record holds
    is not served.
    """
    if key.startswith(PRIVATE_PREFIXES) or '..' in key:
        return HttpResponseNotFound('File not found.')
    if not _held_by_visible_record(request, key):
        return HttpResponseNotFound('File not found.')
    try:
        cfg = r2_config()
        obj = get_r2_client(cfg).get_object(Bucket=cfg.bucket_name, Key=key)
    except ClientError:
        return HttpResponseNotFound('File not found.')
    except Exception:
        logger.exception('Could not read %s from storage', key)
        return HttpResponseNotFound('File not available.')
    body = obj['Body'].read()
    response = HttpResponse(body, content_type=obj.get('ContentType') or 'application/octet-stream')
    filename = key.rsplit('/', 1)[-1]
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response

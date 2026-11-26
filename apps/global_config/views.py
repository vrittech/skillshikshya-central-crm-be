import logging
import re

from botocore.exceptions import ClientError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotFound

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.global_config.filters import walled_branch_id
from apps.global_config.integrations import r2_config
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


def _document_branches(key):
    """Branch ids of the lead and student documents stored under this key."""
    from apps.lead.models import LeadDocument
    from apps.student.models import StudentDocument

    url = f'/api/files/{key}'
    branches = set()
    for model in (LeadDocument, StudentDocument):
        branches |= set(
            model.objects.filter(Q(file=url) | Q(file__endswith=f'/{key}')).values_list('branch_id', flat=True)
        )
    return branches


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def serve_file(request, key):
    """A stored file, for signed-in users only.

    Voice notes are never served here (they have their own checked address).
    A file attached to a document of another branch is hidden from branch users.
    """
    if key.startswith(PRIVATE_PREFIXES) or '..' in key:
        return HttpResponseNotFound('File not found.')
    branch_id = walled_branch_id(request.user)
    if branch_id is not None:
        branches = _document_branches(key)
        if branches and branch_id not in branches:
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

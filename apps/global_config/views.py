import mimetypes

from django.http import StreamingHttpResponse, HttpResponseNotFound

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.global_config.integrations import r2_config
from apps.global_config.utilities.r2_storage import upload_file_to_r2, get_r2_client


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
    if file.content_type and file.content_type not in allowed:
        return Response(
            {'detail': f'File type {file.content_type} not allowed.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    folder = request.data.get('folder', 'uploads')
    result = upload_file_to_r2(file, folder=folder)

    return Response(result, status=status.HTTP_201_CREATED)


def serve_file(request, key):
    try:
        cfg = r2_config()
        obj = get_r2_client(cfg).get_object(Bucket=cfg.bucket_name, Key=key)
        content_type = obj['ContentType']
        body = obj['Body'].read()
        response = StreamingHttpResponse(
            iter([body]),
            content_type=content_type or 'application/octet-stream',
        )
        filename = key.rsplit('/', 1)[-1]
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        response['Content-Length'] = len(body)
        return response
    except client.exceptions.NoSuchKey:
        return HttpResponseNotFound('File not found.')
    except Exception:
        return HttpResponseNotFound('File not available.')

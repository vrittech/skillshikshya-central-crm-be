from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """DRF's handler, plus a clear 400 when a delete is blocked by PROTECT.

    Enrollments, payments, invoices and their history are never removed by a
    cascade. Deleting a lead, course, batch or user that still has them is
    refused here instead of crashing with a 500.
    """
    if isinstance(exc, (ProtectedError, RestrictedError)):
        objects = getattr(exc, 'protected_objects', None) or getattr(exc, 'restricted_objects', [])
        used_by = sorted({str(obj._meta.verbose_name_plural) for obj in objects})
        detail = 'This cannot be deleted because it is still used by money records'
        if used_by:
            detail += f' ({", ".join(used_by)})'
        detail += '. Archive or deactivate it instead.'
        return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
    return drf_exception_handler(exc, context)

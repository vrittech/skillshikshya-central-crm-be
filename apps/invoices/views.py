from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from apps.global_config.filters import BranchFilterBackend
from apps.global_config.pagination import CustomPagination
from apps.invoices.filters import InvoiceFilter
from apps.invoices.models import Invoice, InvoiceEmailConfig
from apps.invoices.serializers import (
    InvoiceEmailConfigSerializer, InvoiceSerializer,
)
from apps.invoices.services import cancel_invoice as cancel_invoice_service
from apps.invoices.tasks import send_invoice_email_task
from apps.lead.serializers import ReasonSerializer


class InvoiceListAPIView(generics.ListAPIView):
    serializer_class = InvoiceSerializer
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated]
    filterset_class = InvoiceFilter
    filter_backends = [DjangoFilterBackend, BranchFilterBackend]
    ordering_fields = ['created_at', 'invoice_number', 'amount']
    ordering = ['-created_at']

    def get_queryset(self):
        return Invoice.objects.select_related(
            'enrollment', 'student', 'payment', 'branch',
        )


class StudentInvoiceListAPIView(InvoiceListAPIView):
    def get_queryset(self):
        return Invoice.objects.filter(
            student_id=self.kwargs['student_pk'],
        ).select_related('enrollment', 'student', 'payment', 'branch')


@api_view(['POST'])
@permission_classes([IsAdminUser])
def resend_invoice(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related('enrollment', 'student'), pk=pk,
    )
    if invoice.status == Invoice.Status.CANCELLED:
        return Response(
            {'detail': 'A cancelled invoice is not sent again.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    recipient = (
        invoice.emailed_to
        or (invoice.student.email if invoice.student and invoice.student.email else None)
        or invoice.enrollment.lead.email
    )
    if not recipient:
        return Response(
            {'detail': 'Cannot resend: student/lead has no email address.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    send_invoice_email_task.delay(invoice.id)
    return Response({
        'detail': f'Invoice {invoice.invoice_number} queued for re-send.',
        'to': recipient,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def cancel_invoice(request, pk):
    """Cancel a wrong invoice with a reason. Invoices are never edited or deleted."""
    invoice = get_object_or_404(Invoice, pk=pk)
    reason = ReasonSerializer(data=request.data)
    reason.is_valid(raise_exception=True)
    invoice = cancel_invoice_service(
        invoice, reason=reason.validated_data['reason'], user=request.user,
    )
    return Response(InvoiceSerializer(invoice).data)


@api_view(['GET', 'PUT'])
@permission_classes([IsAdminUser])
def email_config(request):
    config = InvoiceEmailConfig.load()
    if request.method == 'PUT':
        serializer = InvoiceEmailConfigSerializer(config, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    return Response(InvoiceEmailConfigSerializer(config).data)
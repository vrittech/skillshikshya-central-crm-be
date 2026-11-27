from django.http import HttpResponse
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
from apps.lead.serializers import ReasonSerializer
from apps.global_config.permissions import ReadRights


class InvoiceListAPIView(ReadRights, generics.ListAPIView):
    read_perm = 'invoices.view_invoice'
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


class InvoicePdfAPIView(ReadRights, generics.RetrieveAPIView):
    """GET invoices/{id}/pdf/: the invoice or receipt as a PDF, for anyone who can see it."""
    read_perm = 'invoices.view_invoice'

    permission_classes = [IsAuthenticated]
    filter_backends = [BranchFilterBackend]

    def get_queryset(self):
        return Invoice.objects.select_related(
            'enrollment__lead', 'enrollment__course', 'enrollment__branch', 'enrollment__batch__batch',
            'student', 'payment__received_by', 'branch',
        )

    def retrieve(self, request, *args, **kwargs):
        from apps.invoices.pdf import invoice_pdf

        invoice = self.get_object()
        response = HttpResponse(invoice_pdf(invoice), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{invoice.invoice_number}.pdf"'
        return response


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
    from apps.global_config.models import EmailLog
    from apps.lead.emails import invoice_again

    log = invoice_again(invoice, user=request.user)
    if log.status == EmailLog.Status.SKIPPED:
        return Response(
            {'detail': 'Cannot resend: student/lead has no email address.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response({
        'detail': f'Invoice {invoice.invoice_number} queued for re-send.',
        'to': log.to_email,
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
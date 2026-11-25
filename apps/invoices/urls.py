from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.invoices.views import (
    InvoiceListAPIView, InvoicePdfAPIView, StudentInvoiceListAPIView,
    cancel_invoice, email_config, resend_invoice,
)

urlpatterns = [
    path('invoices/', InvoiceListAPIView.as_view(), name='invoice-list'),
    path('invoices/<int:pk>/pdf/', InvoicePdfAPIView.as_view(), name='invoice-pdf'),
    path(
        'invoices/<int:pk>/resend/',
        resend_invoice, name='invoice-resend',
    ),
    path(
        'invoices/<int:pk>/cancel/',
        cancel_invoice, name='invoice-cancel',
    ),
    path('invoices/email-config/', email_config, name='invoice-email-config'),
    path(
        'students/<int:student_pk>/invoices/',
        StudentInvoiceListAPIView.as_view(), name='student-invoices',
    ),
]
import django_filters

from apps.invoices.models import Invoice


class InvoiceFilter(django_filters.FilterSet):
    created_at__gte = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_at__lte = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')

    class Meta:
        model = Invoice
        fields = {
            'type': ['exact'],
            'status': ['exact'],
            'student': ['exact'],
            'enrollment': ['exact'],
        }
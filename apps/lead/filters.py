import django_filters
from django.db.models import Exists, OuterRef

from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, LeadType, Lead, LeadCourseInterest, Enrollment, Payment,
    LeadCall, LeadActivity, FollowUp, LeadDocument, Notification, TeamsLicence,
)


class LearningModeFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LearningMode
        fields = ['name', 'code', 'is_active']


class InstitutionFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Institution
        fields = ['name', 'code', 'phone', 'is_active']


class ProvinceFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Province
        fields = ['name', 'code', 'is_active']


class CityFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = City
        fields = ['name', 'province', 'is_active']


class LeadSourceFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadSource
        fields = ['name', 'is_active']


class CampaignFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Campaign
        fields = ['name', 'source', 'is_active']


class LeadStatusFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadStatus
        fields = ['name', 'is_active', 'is_final']


class LeadPriorityFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadPriority
        fields = ['name', 'is_active']


class LeadTagFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadTag
        fields = ['name', 'is_active']


class LeadTypeFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadType
        fields = ['name', 'is_active']


def has_enrollment_in(status, request=None):
    """Condition for leads with an enrollment in `status`.

    A branch user counts only enrollments taken at their branch; staff count
    every branch, or the one picked with ?branch_id.
    """
    enrollments = Enrollment.objects.filter(lead=OuterRef('pk'), status=status)
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        if user.is_staff or user.is_superuser:
            branch_id = request.query_params.get('branch_id')
        else:
            branch_id = getattr(user, 'branch_id', None)
        if branch_id:
            enrollments = enrollments.filter(branch_id=branch_id)
    return Exists(enrollments)


class LeadFilter(django_filters.FilterSet):
    full_name = django_filters.CharFilter(lookup_expr='icontains')
    phone = django_filters.CharFilter(lookup_expr='icontains')
    email = django_filters.CharFilter(lookup_expr='icontains')
    notes = django_filters.CharFilter(lookup_expr='icontains')
    lead_score__gte = django_filters.NumberFilter(field_name='lead_score', lookup_expr='gte')
    lead_score__lte = django_filters.NumberFilter(field_name='lead_score', lookup_expr='lte')
    expected_revenue__gte = django_filters.NumberFilter(field_name='expected_revenue', lookup_expr='gte')
    expected_revenue__lte = django_filters.NumberFilter(field_name='expected_revenue', lookup_expr='lte')
    created_at__gte = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_at__lte = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    next_followup_at__gte = django_filters.DateTimeFilter(field_name='next_followup_at', lookup_expr='gte')
    next_followup_at__lte = django_filters.DateTimeFilter(field_name='next_followup_at', lookup_expr='lte')
    created_date = django_filters.DateFilter(field_name='created_at', lookup_expr='date')
    from_date = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    to_date = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')
    # Leads with at least one enrollment in this status: ?enrollment_status=pending.
    enrollment_status = django_filters.ChoiceFilter(
        choices=Enrollment.Status.choices, method='filter_enrollment_status',
    )

    def filter_enrollment_status(self, queryset, name, value):
        return queryset.filter(has_enrollment_in(value, self.request))

    class Meta:
        model = Lead
        fields = {
            'lead_type': ['exact', 'in'],
            'source': ['exact', 'in'],
            'campaign': ['exact', 'in'],
            'branch': ['exact', 'in'],
            'province': ['exact', 'in'],
            'city': ['exact', 'in'],
            'priority': ['exact'],
            'status': ['exact', 'in'],
            'assigned_counselor': ['exact'],
            'primary_course': ['exact'],
            'is_converted': ['exact'],
            'is_archived': ['exact'],
            'tags': ['exact'],
        }


class LeadCourseInterestFilter(django_filters.FilterSet):
    class Meta:
        model = LeadCourseInterest
        fields = ['course', 'is_primary', 'interest_level', 'stage']


class EnrollmentFilter(django_filters.FilterSet):
    enrollment_date__gte = django_filters.DateFilter(field_name='enrollment_date', lookup_expr='gte')
    enrollment_date__lte = django_filters.DateFilter(field_name='enrollment_date', lookup_expr='lte')

    class Meta:
        model = Enrollment
        fields = {
            'course': ['exact'],
            'status': ['exact', 'in'],
            'batch': ['exact'],
            'current_batch': ['exact'],
        }


class PaymentFilter(django_filters.FilterSet):
    payment_date__gte = django_filters.DateTimeFilter(field_name='payment_date', lookup_expr='gte')
    payment_date__lte = django_filters.DateTimeFilter(field_name='payment_date', lookup_expr='lte')

    class Meta:
        model = Payment
        fields = {
            'payment_method': ['exact'],
            'status': ['exact', 'in'],
        }


class LeadCallFilter(django_filters.FilterSet):
    caller = django_filters.CharFilter(lookup_expr='icontains')
    notes = django_filters.CharFilter(lookup_expr='icontains')
    call_timestamp__gte = django_filters.DateTimeFilter(field_name='call_timestamp', lookup_expr='gte')
    call_timestamp__lte = django_filters.DateTimeFilter(field_name='call_timestamp', lookup_expr='lte')

    class Meta:
        model = LeadCall
        fields = ['lead', 'direction', 'outcome']


class GlobalCallFilter(django_filters.FilterSet):
    caller = django_filters.CharFilter(lookup_expr='icontains')
    notes = django_filters.CharFilter(lookup_expr='icontains')
    call_timestamp__gte = django_filters.DateTimeFilter(field_name='call_timestamp', lookup_expr='gte')
    call_timestamp__lte = django_filters.DateTimeFilter(field_name='call_timestamp', lookup_expr='lte')
    # Whole days, in local time: date_from=2026-11-01&date_to=2026-11-30.
    date_from = django_filters.DateFilter(field_name='call_timestamp', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='call_timestamp', lookup_expr='date__lte')
    created_by = django_filters.NumberFilter(field_name='created_by')
    student = django_filters.NumberFilter(field_name='lead__students')

    class Meta:
        model = LeadCall
        fields = ['lead', 'direction', 'outcome']


class LeadActivityFilter(django_filters.FilterSet):
    title = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = LeadActivity
        fields = ['lead', 'activity_type', 'performed_by']


class FollowUpFilter(django_filters.FilterSet):
    scheduled_at__gte = django_filters.DateTimeFilter(field_name='scheduled_at', lookup_expr='gte')
    scheduled_at__lte = django_filters.DateTimeFilter(field_name='scheduled_at', lookup_expr='lte')
    # payment=true: only payment follow-ups (made by the reminder job); false: only the others.
    payment = django_filters.BooleanFilter(method='filter_payment')

    def filter_payment(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(enrollment__isnull=not value)

    class Meta:
        model = FollowUp
        fields = ['lead', 'assigned_to', 'status']


class LeadDocumentFilter(django_filters.FilterSet):
    class Meta:
        model = LeadDocument
        fields = ['lead', 'document_type']


class GlobalEnrollmentFilter(django_filters.FilterSet):
    enrollment_date__gte = django_filters.DateFilter(field_name='enrollment_date', lookup_expr='gte')
    enrollment_date__lte = django_filters.DateFilter(field_name='enrollment_date', lookup_expr='lte')
    # seated = in a batch now, paused = paused out of one, none = never placed or left.
    seat = django_filters.ChoiceFilter(
        choices=[('seated', 'Seated'), ('paused', 'Paused'), ('none', 'No seat')],
        method='filter_seat',
    )

    def filter_seat(self, queryset, name, value):
        from apps.lead.services import filter_seat

        return filter_seat(queryset, value)

    class Meta:
        model = Enrollment
        fields = {
            'lead': ['exact'],
            'course': ['exact'],
            'status': ['exact', 'in'],
            'batch': ['exact'],
            'current_batch': ['exact'],
        }


class GlobalPaymentFilter(django_filters.FilterSet):
    payment_date__gte = django_filters.DateTimeFilter(field_name='payment_date', lookup_expr='gte')
    payment_date__lte = django_filters.DateTimeFilter(field_name='payment_date', lookup_expr='lte')
    lead = django_filters.NumberFilter(field_name='enrollment__lead_id')

    class Meta:
        model = Payment
        fields = {
            'enrollment': ['exact'],
            'payment_method': ['exact'],
            'status': ['exact', 'in'],
        }


class NotificationFilter(django_filters.FilterSet):
    class Meta:
        model = Notification
        fields = ['recipient', 'is_read']


class TeamsLicenceFilter(django_filters.FilterSet):
    # open=true: licences in use now; false: freed ones.
    open = django_filters.BooleanFilter(field_name='released_at', lookup_expr='isnull')
    batch = django_filters.NumberFilter(field_name='enrollment__current_batch')
    student = django_filters.NumberFilter(field_name='enrollment__student')

    class Meta:
        model = TeamsLicence
        fields = ['enrollment']

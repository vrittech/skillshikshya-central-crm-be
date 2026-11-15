import csv
import re

from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models import Count, Q , Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend

from apps.callminutes.utils import format_duration
from apps.lead.tasks import NEPAL_TZ
from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, LeadType, Lead, LeadCourseInterest, Enrollment, Payment,
    LeadCall, LeadActivity, FollowUp, LeadDocument, Notification,
)
from apps.lead.serializers import (
    LearningModeSerializer, InstitutionSerializer,
    ProvinceSerializer, CitySerializer,
    LeadSourceSerializer, CampaignSerializer, LeadStatusSerializer,
    LeadPrioritySerializer, LeadTagSerializer, LeadTypeSerializer, LeadSerializer,
    LeadCourseInterestSerializer, EnrollmentSerializer, PaymentSerializer,
    LeadCallSerializer, LeadActivitySerializer, FollowUpSerializer,
    LeadDocumentSerializer, NotificationSerializer,
    LeadListSerializer, LeadMinSerializer,
    GlobalFollowUpSerializer, GlobalEnrollmentSerializer, GlobalPaymentSerializer,
    GlobalCallSerializer,
)
from apps.lead.filters import (
    LearningModeFilter, InstitutionFilter,
    ProvinceFilter, CityFilter,
    LeadSourceFilter, CampaignFilter, LeadStatusFilter,
    LeadPriorityFilter, LeadTagFilter, LeadTypeFilter, LeadFilter,
    LeadCourseInterestFilter, EnrollmentFilter, PaymentFilter,
    LeadCallFilter, LeadActivityFilter, FollowUpFilter, LeadDocumentFilter,
    NotificationFilter,
    GlobalEnrollmentFilter, GlobalPaymentFilter, GlobalCallFilter
)
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission
from apps.global_config.filters import BranchFilterBackend


class ConfigViewSet(viewsets.ModelViewSet):
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class LearningModeViewSet(ConfigViewSet):
    queryset = LearningMode.objects.all()
    serializer_class = LearningModeSerializer
    filterset_class = LearningModeFilter
    search_fields = ['name', 'code']
    ordering_fields = ['id', 'name', 'code', 'is_active']
    ordering = ['name']


class InstitutionViewSet(ConfigViewSet):
    queryset = Institution.objects.all()
    serializer_class = InstitutionSerializer
    filterset_class = InstitutionFilter
    search_fields = ['name', 'code', 'phone']
    ordering_fields = ['id', 'name', 'is_active']
    ordering = ['name']


class ProvinceViewSet(ConfigViewSet):
    queryset = Province.objects.all()
    serializer_class = ProvinceSerializer
    filterset_class = ProvinceFilter
    search_fields = ['name', 'code']
    ordering_fields = ['id', 'name', 'code', 'is_active']
    ordering = ['name']


class CityViewSet(ConfigViewSet):
    queryset = City.objects.select_related('province')
    serializer_class = CitySerializer
    filterset_class = CityFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'is_active']
    ordering = ['name']


class LeadSourceViewSet(ConfigViewSet):
    queryset = LeadSource.objects.all()
    serializer_class = LeadSourceSerializer
    filterset_class = LeadSourceFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'is_active']
    ordering = ['name']

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        if source.is_protected:
            return Response(
                {'detail': 'This lead source is protected and cannot be deleted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if Lead.objects.filter(source=source).exists():
            return Response(
                {'detail': "This source is used in leads so it cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class CampaignViewSet(ConfigViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    filterset_class = CampaignFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'start_date', 'end_date']
    ordering = ['name']


class LeadStatusViewSet(ConfigViewSet):
    queryset = LeadStatus.objects.all()
    serializer_class = LeadStatusSerializer
    filterset_class = LeadStatusFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'sort_order', 'is_final']
    ordering = ['sort_order', 'name']


class LeadPriorityViewSet(ConfigViewSet):
    queryset = LeadPriority.objects.all()
    serializer_class = LeadPrioritySerializer
    filterset_class = LeadPriorityFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'sort_order']
    ordering = ['sort_order', 'name']


class LeadTagViewSet(ConfigViewSet):
    queryset = LeadTag.objects.all()
    serializer_class = LeadTagSerializer
    filterset_class = LeadTagFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'is_active']
    ordering = ['name']


class LeadTypeViewSet(ConfigViewSet):
    queryset = LeadType.objects.all()
    serializer_class = LeadTypeSerializer
    filterset_class = LeadTypeFilter
    search_fields = ['name']
    ordering_fields = ['id', 'name', 'is_active']
    ordering = ['name']


class LeadViewSet(viewsets.ModelViewSet):
    queryset = Lead.objects.select_related(
        'source', 'campaign', 'branch', 'priority', 'status',
        'assigned_counselor', 'primary_course', 'created_by', 'updated_by',
        'province', 'city',
    ).prefetch_related('tags')
    pagination_class = CustomPagination
    filterset_class = LeadFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['full_name', 'phone', 'email', 'notes', 'reference_number']
    ordering_fields = [
        'id', 'reference_number', 'full_name', 'lead_score', 'expected_revenue',
        'created_at', 'updated_at', 'last_contact_at', 'next_followup_at',
        'address', 'landmark',
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return LeadListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return LeadSerializer
        if self.action == 'export_csv':
            return LeadMinSerializer
        return LeadSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'recycle_bin':
            return qs.filter(is_deleted=True)
        if self.action == 'export_csv':
            return qs.filter(is_deleted=False).select_related('source', 'status', 'priority', 'assigned_counselor')
        qs = qs.filter(is_deleted=False)
        include_archived = self.request.query_params.get('include_archived', '').lower() in ('true', '1')
        if not include_archived:
            qs = qs.filter(is_archived=False)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class GlobalFollowUpViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FollowUp.objects.none()
    serializer_class = GlobalFollowUpSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = FollowUpFilter
    ordering_fields = ['scheduled_at', 'created_at']
    ordering = ['-scheduled_at']

    def get_queryset(self):
        return FollowUp.objects.select_related(
            'lead', 'assigned_to', 'branch',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


class GlobalEnrollmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Enrollment.objects.none()
    serializer_class = GlobalEnrollmentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalEnrollmentFilter
    ordering_fields = ['enrollment_date', 'agreed_amount', 'created_at']
    ordering = ['-enrollment_date']

    def get_queryset(self):
        return Enrollment.objects.select_related(
            'lead', 'student', 'course', 'branch',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


class GlobalPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.none()
    serializer_class = GlobalPaymentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalPaymentFilter
    ordering_fields = ['payment_date', 'amount', 'created_at']
    ordering = ['-payment_date']

    def get_queryset(self):
        return Payment.objects.select_related(
            'enrollment', 'enrollment__lead', 'enrollment__course', 'received_by', 'branch',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


FORMULA_TRIGGERS = ('=', '+', '-', '@')
PHONE_LIKE = re.compile(r'^\+?\d[\d\s()-]{3,18}\d$')


def csv_safe(value, allow_phone=False):
    """Stop spreadsheet apps from running a text cell as a formula.

    Checks the first non-whitespace character, so " =1+1" and "\\n=1+1" are
    caught too. Real phone numbers are left alone when `allow_phone` is set.
    """
    if value is None:
        return ''
    text = str(value)
    if allow_phone and PHONE_LIKE.match(text):
        return text
    if text.lstrip().startswith(FORMULA_TRIGGERS) or text.startswith(('\t', '\r')):
        return "'" + text
    return text

def resolve_caller_names(caller_values):
    """Map caller values to display names.

    Numeric values are treated as user IDs. Other values are
    already names and can be displayed as they are.
    """
    ids = {
        int(v) for v in caller_values
        if v and str(v).isascii() and str(v).isdigit() and len(str(v)) <= 9
    }
    if not ids:
        return {}

    User = get_user_model()

    return {
        str(pk): name
        for pk, name in User.objects.filter(pk__in=ids).values_list('pk', 'name')
    }


class GlobalCallViewSet(viewsets.ReadOnlyModelViewSet):
    """Flat, cross-lead call log. Newest calls first by default."""
    queryset = LeadCall.objects.none()
    serializer_class = GlobalCallSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalCallFilter
    search_fields = [
        'caller', 'phone_number', 'outcome', 'notes', 'lead__full_name',
        'lead__reference_number', 'created_by__name',
    ]
    ordering_fields = ['call_timestamp', 'duration_seconds', 'created_at']
    ordering = ['-call_timestamp']

    def get_queryset(self):
        return LeadCall.objects.select_related(
            'lead', 'created_by', 'branch',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'summary', 'export']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]

    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset()).order_by()

        totals = qs.aggregate(
            total=Count('id'),
            inbound=Count('id', filter=Q(direction='inbound')),
            outbound=Count('id', filter=Q(direction='outbound')),
            connected=Count('id', filter=Q(duration_seconds__gt=0)),
            talk_seconds=Sum('duration_seconds'),
        )

        total = totals['total']
        talk = totals['talk_seconds'] or 0

        per_day = (
            qs.annotate(day=TruncDate('call_timestamp', tzinfo=NEPAL_TZ))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )

        caller_rows = list(
            qs.values('caller')
            .annotate(count=Count('id'))
            .order_by('-count', 'caller')[:10]
        )

        names = resolve_caller_names(row['caller'] for row in caller_rows)

        top_callers = [
            {
                'caller_id': row['caller'],
                'caller': names.get(row['caller'], row['caller']),
                'count': row['count'],
            }
            for row in caller_rows
        ]

        return Response({
            'total_calls': total,
            'inbound_calls': totals['inbound'],
            'outbound_calls': totals['outbound'],
            'talk_seconds': talk,
            'avg_duration_seconds': round(talk / total) if total else 0,
            'connected_calls': totals['connected'],
            'connect_rate': round(
                totals['connected'] * 100 / total, 1
            ) if total else 0,
            'calls_per_day': [
                {
                    'date': row['day'].isoformat(),
                    'count': row['count'],
                }
                for row in per_day
            ],
            'top_callers': top_callers,
        })

    @action(detail=False, methods=['get'])
    def export(self, request):
        qs = self.filter_queryset(self.get_queryset())

        caller_names = resolve_caller_names(
            list(
                qs.order_by()
                .values_list('caller', flat=True)
                .distinct()
            )
        )

        today = timezone.now().astimezone(NEPAL_TZ).strftime('%Y-%m-%d')

        response = HttpResponse(
            content_type='text/csv; charset=utf-8'
        )
        response['Content-Disposition'] = (
            f'attachment; filename="calls-{today}.csv"'
        )

        # UTF-8 BOM helps Excel display Nepali/Devanagari text correctly.
        response.write('\ufeff')

        writer = csv.writer(response)

        writer.writerow([
            'Date & time (NPT)',
            'Direction',
            'Lead',
            'Reference',
            'Phone',
            'Caller',
            'Logged by',
            'Duration (MM:SS)',
            'Seconds',
            'Outcome',
            'Notes',
            'Recording',
        ])

        for call in qs.iterator(chunk_size=500):
            writer.writerow([
                call.call_timestamp.astimezone(NEPAL_TZ).strftime(
                    '%Y-%m-%d %H:%M:%S'
                ),
                call.get_direction_display(),
                csv_safe(call.lead.full_name),
                csv_safe(call.lead.reference_number),
                csv_safe(call.phone_number,allow_phone=True),
                csv_safe(
                    caller_names.get(call.caller, call.caller)
                ),
                csv_safe(
                    call.created_by.name if call.created_by else ''
                ),
                format_duration(call.duration_seconds),
                call.duration_seconds,
                csv_safe(call.outcome),
                csv_safe(call.notes),
                csv_safe(call.recording_url),
            ])

        return response


class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = NotificationFilter
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(recipient=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response({'detail': 'Notification marked as read.'})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'detail': 'All notifications marked as read.'})

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({'unread_count': count})

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="leads.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Reference', 'Full Name', 'Phone', 'Email', 'Lead Type',
            'Source', 'Status', 'Priority', 'Assigned Counselor',
            'Lead Score', 'Expected Revenue', 'Is Converted',
            'Created At', 'Updated At',
        ])
        for lead in queryset.iterator(chunk_size=500):
            writer.writerow([
                lead.reference_number, lead.full_name, lead.phone, lead.email,
                lead.lead_type.name if lead.lead_type else '',
                lead.source.name if lead.source else '',
                lead.status.name if lead.status else '',
                lead.priority.name if lead.priority else '',
                lead.assigned_counselor.name if lead.assigned_counselor else '',
                lead.lead_score, lead.expected_revenue,
                lead.is_converted,
                lead.created_at, lead.updated_at,
            ])
        return response

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        lead = self.get_object()
        lead.is_archived = True
        lead.save(update_fields=['is_archived'])
        return Response({'detail': 'Lead archived.'})

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        lead = self.get_object()
        lead.is_archived = False
        lead.save(update_fields=['is_archived'])
        return Response({'detail': 'Lead restored.'})

    @action(detail=True, methods=['post'])
    def purge(self, request, pk=None):
        if not request.user.is_staff:
            return Response(
                {'detail': 'Only staff members can permanently delete leads.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        lead = Lead._base_manager.filter(pk=pk).first()
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)
        lead.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def recycle_bin(self, request):
        self.action = 'recycle_bin'
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = LeadListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = LeadListSerializer(queryset, many=True)
        return Response(serializer.data)


class LeadCourseInterestViewSet(viewsets.ModelViewSet):
    serializer_class = LeadCourseInterestSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = LeadCourseInterestFilter
    ordering_fields = ['created_at']
    ordering = ['-is_primary', '-created_at']

    def get_queryset(self):
        return LeadCourseInterest.objects.filter(lead_id=self.kwargs['lead_pk']).select_related('course')

    def perform_create(self, serializer):
        serializer.save(lead_id=self.kwargs['lead_pk'])

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class EnrollmentViewSet(viewsets.ModelViewSet):
    serializer_class = EnrollmentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = EnrollmentFilter
    ordering_fields = ['enrollment_date', 'created_at']
    ordering = ['-enrollment_date']

    def get_queryset(self):
        return Enrollment.objects.filter(lead_id=self.kwargs['lead_pk']).select_related(
            'course', 'student', 'created_by', 'updated_by',
        ).prefetch_related('payments')

    def perform_create(self, serializer):
        serializer.save(
            lead_id=self.kwargs['lead_pk'],
            created_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = PaymentFilter
    ordering_fields = ['payment_date', 'created_at']
    ordering = ['-payment_date']

    def get_queryset(self):
        return Payment.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        ).select_related('received_by')

    def perform_create(self, serializer):
        instance = serializer.save(
            enrollment_id=self.kwargs['enrollment_pk'],
            received_by=self.request.user,
        )
        if instance.status == 'successful':
            self._generate_receipt(instance)

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        instance = serializer.save()
        if instance.status == 'successful' and previous_status != 'successful':
            self._generate_receipt(instance)

    def _generate_receipt(self, payment):
        from apps.invoices.models import Invoice
        from apps.invoices.services import create_invoice

        if not payment.invoices.exists():
            create_invoice(
                enrollment=payment.enrollment,
                invoice_type='payment',
                amount=payment.amount,
                payment=payment,
                created_by=self.request.user,
            )

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class LeadCallViewSet(viewsets.ModelViewSet):
    serializer_class = LeadCallSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = LeadCallFilter
    ordering_fields = ['call_timestamp', 'duration_seconds']
    ordering = ['-call_timestamp']

    def get_queryset(self):
        return LeadCall.objects.filter(lead_id=self.kwargs['lead_pk']).select_related('created_by')

    def perform_create(self, serializer):
        from apps.callminutes.services import charge_for_call

        parent = get_object_or_404(Lead, pk=self.kwargs['lead_pk'])

        # Calls inherit the lead's branch, otherwise the logger's branch.
        branch = (
            parent.branch
            or getattr(self.request.user, 'branch', None)
        )

        instance = charge_for_call(
            user=self.request.user,
            duration_seconds=serializer.validated_data.get('duration_seconds'),
            create_call=lambda: serializer.save(
                lead_id=self.kwargs['lead_pk'],
                created_by=self.request.user,
                branch=branch,
            ),
        )

        lead = instance.lead
        if lead.last_contact_at is None or instance.call_timestamp > lead.last_contact_at:
            lead.last_contact_at = instance.call_timestamp
        lead.save(update_fields=['lead_score', 'last_contact_at'])

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        data = dict(serializer.data)
        instance = serializer.instance
        seconds_used = getattr(instance, 'seconds_used', 0)
        remaining_seconds = getattr(instance, 'remaining_seconds', None)
        data['seconds_used'] = seconds_used
        data['remaining_seconds'] = remaining_seconds
        data['minutes_used'] = seconds_used // 60 if seconds_used is not None else None
        data['remaining_minutes'] = (
            remaining_seconds // 60 if remaining_seconds is not None else None
        )
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'detail': 'Call records cannot be deleted.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class LeadActivityViewSet(viewsets.ModelViewSet):
    serializer_class = LeadActivitySerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = LeadActivityFilter
    ordering_fields = ['activity_at']
    ordering = ['-activity_at']

    def get_queryset(self):
        return LeadActivity.objects.filter(lead_id=self.kwargs['lead_pk']).select_related('performed_by')

    def perform_create(self, serializer):
        instance = serializer.save(
            lead_id=self.kwargs['lead_pk'],
            performed_by=self.request.user,
        )
        lead = instance.lead
        if lead.last_contact_at is None or instance.activity_at > lead.last_contact_at:
            lead.last_contact_at = instance.activity_at
        lead.save(update_fields=['lead_score', 'last_contact_at'])

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class FollowUpViewSet(viewsets.ModelViewSet):
    serializer_class = FollowUpSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = FollowUpFilter
    ordering_fields = ['scheduled_at']
    ordering = ['-scheduled_at']

    def get_queryset(self):
        return FollowUp.objects.filter(lead_id=self.kwargs['lead_pk']).select_related(
            'assigned_to', 'created_by', 'updated_by',
        )

    def perform_create(self, serializer):
        instance = serializer.save(
            lead_id=self.kwargs['lead_pk'],
            created_by=self.request.user,
        )
        lead = instance.lead
        if instance.status == 'pending':
            if lead.next_followup_at is None or instance.scheduled_at < lead.next_followup_at:
                lead.next_followup_at = instance.scheduled_at
            else:
                lead.save(update_fields=['lead_score'])
                return
            lead.save(update_fields=['lead_score', 'next_followup_at'])

    def perform_update(self, serializer):
        instance = serializer.save(updated_by=self.request.user)
        instance.lead.save(update_fields=['lead_score'])

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class LeadDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = LeadDocumentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = LeadDocumentFilter
    ordering_fields = ['uploaded_at']
    ordering = ['-uploaded_at']

    def get_queryset(self):
        return LeadDocument.objects.filter(lead_id=self.kwargs['lead_pk']).select_related('uploaded_by')

    def perform_create(self, serializer):
        serializer.save(
            lead_id=self.kwargs['lead_pk'],
            uploaded_by=self.request.user,
        )

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

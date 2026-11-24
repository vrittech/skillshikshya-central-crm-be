import csv
from datetime import datetime

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend

from apps.lead import services
from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, LeadType, Lead, LeadCourseInterest, Enrollment, Payment, EnrollmentHistory,
    Adjustment,
    LeadCall, LeadActivity, FollowUp, LeadDocument, Notification,
)
from apps.lead.serializers import (
    LearningModeSerializer, InstitutionSerializer,
    ProvinceSerializer, CitySerializer,
    LeadSourceSerializer, CampaignSerializer, LeadStatusSerializer,
    LeadPrioritySerializer, LeadTagSerializer, LeadTypeSerializer, LeadSerializer,
    LeadCourseInterestSerializer, EnrollmentSerializer, PaymentSerializer,
    EnrollmentHistorySerializer, ReasonSerializer, AdjustmentSerializer, SeatActionSerializer,
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
    GlobalEnrollmentFilter, GlobalPaymentFilter, GlobalCallFilter,
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
        if self.action == 'restore':
            # Restore has to find the deleted or archived lead it brings back.
            return qs
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
        """Bring a lead back to the active list, from the recycle bin or from archive."""
        lead = self.get_object()
        lead.is_archived = False
        lead.is_deleted = False
        lead.deleted_at = None
        lead.deleted_by = None
        lead.save(update_fields=['is_archived', 'is_deleted', 'deleted_at', 'deleted_by'])
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
        if lead.enrollments.exists():
            return Response(
                {'detail': 'This lead has enrollments with money records, so it cannot be '
                           'deleted forever. It can stay in the recycle bin.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
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
            'lead', 'student', 'course', 'branch', 'batch', 'current_batch',
        ).prefetch_related('adjustments')

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


class GlobalCallViewSet(viewsets.ReadOnlyModelViewSet):
    """Flat, cross-lead call log. Newest calls first by default."""
    queryset = LeadCall.objects.none()
    serializer_class = GlobalCallSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalCallFilter
    search_fields = [
        'caller', 'phone_number', 'outcome', 'notes', 'lead__full_name',
        'lead__reference_number',
    ]
    ordering_fields = ['call_timestamp', 'duration_seconds', 'created_at']
    ordering = ['-call_timestamp']

    def get_queryset(self):
        return LeadCall.objects.select_related(
            'lead', 'created_by', 'branch',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


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
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        return Enrollment.objects.filter(lead_id=self.kwargs['lead_pk']).select_related(
            'course', 'student', 'batch', 'current_batch', 'created_by', 'updated_by',
        ).prefetch_related(
            'payments', 'adjustments',
            'placements__course_batch', 'placements__from_batch', 'placements__created_by',
        )

    def perform_create(self, serializer):
        serializer.save(
            lead_id=self.kwargs['lead_pk'],
            created_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def get_permissions(self):
        if self.action in [
            'create', 'update', 'partial_update', 'destroy', 'confirm', 'cancel', 'reinstate',
            'join_batch', 'move', 'pause', 'resume',
        ]:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    def _reason(self, request):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data['reason']

    @action(detail=True, methods=['post'])
    def confirm(self, request, *args, **kwargs):
        """Confirm a pending enrollment. This makes the lead a student."""
        enrollment = services.confirm_enrollment(self.get_object(), user=request.user)
        return Response(self.get_serializer(enrollment).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, *args, **kwargs):
        enrollment = self.get_object()
        reason = self._reason(request)
        enrollment = services.cancel_enrollment(enrollment, reason=reason, user=request.user)
        return Response(self.get_serializer(enrollment).data)

    @action(detail=True, methods=['post'])
    def reinstate(self, request, *args, **kwargs):
        enrollment = self.get_object()
        reason = self._reason(request)
        enrollment = services.reinstate_enrollment(enrollment, reason=reason, user=request.user)
        return Response(self.get_serializer(enrollment).data)

    # ---- seat actions: each adds a seat history row and a history row.

    def _seat_body(self, request, *, needs_batch=True, needs_reason=True):
        body = SeatActionSerializer(
            data=request.data, needs_batch=needs_batch, needs_reason=needs_reason,
        )
        body.is_valid(raise_exception=True)
        return body.validated_data

    def _seat_reply(self, enrollment):
        return Response(self.get_serializer(self.get_queryset().get(pk=enrollment.pk)).data)

    @action(detail=True, methods=['post'], url_path='join-batch')
    def join_batch(self, request, *args, **kwargs):
        """Seat a confirmed enrollment that sits in no batch. Reason optional."""
        enrollment = self.get_object()
        body = self._seat_body(request, needs_reason=False)
        enrollment = services.join_batch(
            enrollment, body['batch'], reason=body.get('reason', ''), user=request.user,
            on=body.get('started_on'),
        )
        return self._seat_reply(enrollment)

    @action(detail=True, methods=['post'])
    def move(self, request, *args, **kwargs):
        """Move the student to another batch of the course. The money stays put."""
        enrollment = self.get_object()
        body = self._seat_body(request)
        enrollment = services.move_batch(
            enrollment, body['batch'], reason=body['reason'], user=request.user,
            on=body.get('started_on'),
        )
        return self._seat_reply(enrollment)

    @action(detail=True, methods=['post'])
    def pause(self, request, *args, **kwargs):
        """Take the student out of their batch for now. Frees the seat."""
        enrollment = self.get_object()
        body = self._seat_body(request, needs_batch=False)
        enrollment = services.pause_enrollment(
            enrollment, reason=body['reason'], user=request.user, on=body.get('started_on'),
        )
        return self._seat_reply(enrollment)

    @action(detail=True, methods=['post'])
    def resume(self, request, *args, **kwargs):
        """Bring a paused student back into a batch of the course."""
        enrollment = self.get_object()
        body = self._seat_body(request)
        enrollment = services.resume_enrollment(
            enrollment, body['batch'], reason=body['reason'], user=request.user,
            on=body.get('started_on'),
        )
        return self._seat_reply(enrollment)


class PaymentViewSet(viewsets.ModelViewSet):
    """Payments are only added. Fix a wrong one with void (reason) and record a new one."""
    serializer_class = PaymentSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = PaymentFilter
    ordering_fields = ['payment_date', 'created_at']
    ordering = ['-payment_date']

    def get_queryset(self):
        return Payment.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        ).select_related('received_by', 'voided_by')

    def perform_create(self, serializer):
        payment = serializer.save(
            enrollment_id=self.kwargs['enrollment_pk'],
            received_by=self.request.user,
        )
        services.issue_receipt(payment, user=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'confirm', 'void']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=True, methods=['post'])
    def confirm(self, request, *args, **kwargs):
        payment = services.confirm_payment(self.get_object(), user=request.user)
        return Response(self.get_serializer(payment).data)

    @action(detail=True, methods=['post'])
    def void(self, request, *args, **kwargs):
        payment = self.get_object()
        reason = ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        payment = services.void_payment(
            payment, reason=reason.validated_data['reason'], user=request.user,
        )
        return Response(self.get_serializer(payment).data)


class AdjustmentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Money entries of one enrollment: list and add only. Each needs a reason."""
    serializer_class = AdjustmentSerializer
    pagination_class = CustomPagination
    filter_backends = [BranchFilterBackend]
    branch_field = 'enrollment__branch'

    def get_queryset(self):
        return Adjustment.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        ).select_related('created_by').order_by('-created_at', '-id')

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), IsStaffOrHasPermission()]
        return [IsAuthenticated()]

    def _enrollment(self):
        enrollments = Enrollment.objects.filter(lead_id=self.kwargs['lead_pk'])
        user = self.request.user
        if not (user.is_staff or user.is_superuser) and getattr(user, 'branch', None):
            enrollments = enrollments.filter(branch=user.branch)
        return get_object_or_404(enrollments, pk=self.kwargs['enrollment_pk'])

    def create(self, request, *args, **kwargs):
        enrollment = self._enrollment()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adjustment = services.add_adjustment(
            enrollment, user=request.user, **serializer.validated_data,
        )
        return Response(self.get_serializer(adjustment).data, status=status.HTTP_201_CREATED)


class EnrollmentHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Reason trail of one enrollment, newest first.

    Read-only: rows are only written by EnrollmentHistory.record().
    """
    serializer_class = EnrollmentHistorySerializer
    pagination_class = CustomPagination
    filter_backends = [BranchFilterBackend]
    branch_field = 'enrollment__branch'

    def get_queryset(self):
        return EnrollmentHistory.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        ).select_related('performed_by').order_by('-created_at', '-id')


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

        instance = charge_for_call(
            user=self.request.user,
            duration_seconds=serializer.validated_data.get('duration_seconds'),
            create_call=lambda: serializer.save(
                lead_id=self.kwargs['lead_pk'],
                created_by=self.request.user,
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

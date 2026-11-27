from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend

from apps.lead import services
from apps.lead.services import ActionRefused
from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, Lead, LeadCourseInterest, Enrollment, Payment, EnrollmentHistory,
    Adjustment, ReminderLog, TeamsLicence,
    LeadCall, LeadActivity, FollowUp, LeadDocument, Notification, VoiceNote,
)
from apps.lead.serializers import (
    LeadImportSerializer,
    LearningModeSerializer, InstitutionSerializer,
    ProvinceSerializer, CitySerializer,
    LeadSourceSerializer, CampaignSerializer, LeadStatusSerializer,
    LeadPrioritySerializer, LeadTagSerializer, LeadSerializer,
    LeadCourseInterestSerializer, EnrollmentSerializer, PaymentSerializer,
    EnrollmentHistorySerializer, ReasonSerializer, AdjustmentSerializer, SeatActionSerializer,
    StatusChangeSerializer,
    DueDatesSerializer, ReminderLogSerializer, GiveLicenceSerializer, TeamsLicenceSerializer,
    SendTeamsDetailsSerializer, AddTeamsAccountsSerializer,
    EnquiryStageChangeSerializer, EnquiryStageSerializer, EnquiryLostSerializer,
    PhoneMatchSerializer,
    LeadCallSerializer, LeadActivitySerializer, FollowUpSerializer,
    LeadDocumentSerializer, NotificationSerializer, VoiceNoteSerializer,
    LeadListSerializer, LeadMinSerializer,
    GlobalFollowUpSerializer, GlobalEnrollmentSerializer, GlobalPaymentSerializer,
    GlobalCallSerializer,
)
from apps.lead.filters import (
    LearningModeFilter, InstitutionFilter,
    ProvinceFilter, CityFilter,
    LeadSourceFilter, CampaignFilter, LeadStatusFilter,
    LeadPriorityFilter, LeadTagFilter, LeadFilter,
    LeadCourseInterestFilter, EnrollmentFilter, PaymentFilter,
    LeadCallFilter, LeadActivityFilter, FollowUpFilter, LeadDocumentFilter,
    NotificationFilter,
    GlobalEnrollmentFilter, GlobalPaymentFilter, GlobalCallFilter, TeamsLicenceFilter,
)
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission, needs
from apps.global_config.filters import BranchFilterBackend, LeadBranchFilterBackend, walled_branch_id


class ConfigViewSet(viewsets.ModelViewSet):
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    # Statuses, sources and the rest with no branch are shared by every branch.
    shared_when_no_branch = True

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


class LeadViewSet(viewsets.ModelViewSet):
    queryset = Lead.objects.select_related(
        'source', 'campaign', 'branch', 'priority', 'status',
        'assigned_counselor', 'primary_course', 'created_by', 'updated_by',
        'province', 'city',
    ).prefetch_related('tags')
    pagination_class = CustomPagination
    filterset_class = LeadFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, LeadBranchFilterBackend]
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
        if self.action == 'restore':
            # Restore has to find the deleted or archived lead it brings back.
            return qs
        qs = qs.filter(is_deleted=False)
        include_archived = self.request.query_params.get('include_archived', '').lower() in ('true', '1')
        if not include_archived:
            qs = qs.filter(is_archived=False)
        return qs

    def create(self, request, *args, **kwargs):
        """A new lead whose phone matches an existing lead is refused with the
        matches, unless allow_duplicate_phone is sent."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allow = str(request.data.get('allow_duplicate_phone', '')).lower() in ('true', '1')
        if not allow:
            matches = self._phone_matches(
                serializer.validated_data.get('phone'),
                serializer.validated_data.get('secondary_phone'),
            )
            if matches:
                return Response({
                    'phone': ['A lead with this phone number already exists.'],
                    'phone_match': PhoneMatchSerializer(matches, many=True).data,
                }, status=status.HTTP_400_BAD_REQUEST)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @staticmethod
    def _phone_matches(*phones, exclude=None):
        from apps.lead.phones import phone_matches

        leads = phone_matches(*phones, exclude=exclude).select_related(
            'branch', 'assigned_counselor', 'status',
        ).prefetch_related('course_interests__course', 'course_interests__branch')
        return list(leads[:5])

    @action(detail=False, methods=['get'], url_path='phone-match')
    def phone_match(self, request):
        """Leads in any branch with this phone number. ?exclude= leaves out the lead being edited."""
        exclude = request.query_params.get('exclude')
        matches = self._phone_matches(
            request.query_params.get('phone', ''),
            exclude=int(exclude) if exclude and exclude.isdigit() else None,
        )
        return Response(PhoneMatchSerializer(matches, many=True).data)

    def perform_create(self, serializer):
        from apps.lead.enquiries import ensure_primary_enquiry

        lead = serializer.save(created_by=self.request.user)
        ensure_primary_enquiry(lead, user=self.request.user)

    def perform_update(self, serializer):
        from apps.lead.enquiries import ensure_primary_enquiry

        serializer.instance._changed_by = self.request.user
        lead = serializer.save(updated_by=self.request.user)
        ensure_primary_enquiry(lead, user=self.request.user)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])

    def get_permissions(self):
        if self.action in ['archive', 'restore']:
            return [IsAuthenticated(), needs('lead.change_lead')()]
        if self.action in [
            'create', 'update', 'partial_update', 'destroy', 'import_preview', 'import_leads',
        ]:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Counts for the cards on the lead list, over every lead the user can see.

        Days and months are Nepal days and months. A follow-up is the lead's
        next pending one.
        """
        from apps.lead.filters import has_enrollment_in, nepal_today_bounds

        visible = LeadBranchFilterBackend().filter_queryset(request, self.get_queryset(), self)
        pending = visible.filter(has_enrollment_in(Enrollment.Status.PENDING, request))
        # One row per lead: the branch filter can join enquiries.
        leads = Lead.objects.filter(pk__in=visible.values('pk'))
        today, tomorrow = nepal_today_bounds()
        month = today.replace(day=1)
        last_month = (month - timedelta(days=1)).replace(day=1)
        week_ago = timezone.now() - timedelta(days=7)
        counts = leads.aggregate(
            new_this_week=Count('id', filter=Q(created_at__gte=week_ago)),
            new_last_week=Count('id', filter=Q(
                created_at__gte=week_ago - timedelta(days=7), created_at__lt=week_ago,
            )),
            followups_today=Count('id', filter=Q(
                next_followup_at__gte=today, next_followup_at__lt=tomorrow,
            )),
            followups_overdue=Count('id', filter=Q(next_followup_at__lt=today, is_converted=False)),
            enrolled_this_month=Count('id', filter=Q(is_converted=True, converted_at__gte=month)),
            enrolled_last_month=Count('id', filter=Q(
                is_converted=True, converted_at__gte=last_month, converted_at__lt=month,
            )),
        )
        return Response({'pending_enrollment': pending.distinct().count(), **counts})

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        """The leads the list shows (same filters), as CSV, or Excel with
        ?file_type=xlsx. ?ids=1,2,3 exports only those leads."""
        from apps.lead.imports import export_response

        queryset = self.filter_queryset(self.get_queryset())
        ids = [int(i) for i in request.query_params.get('ids', '').split(',') if i.strip().isdigit()]
        if ids:
            queryset = queryset.filter(pk__in=ids)
        queryset = queryset.prefetch_related(
            'tags', 'course_interests__course', 'enrollments__course', 'followups',
        )
        return export_response(
            queryset.iterator(chunk_size=500),
            file_type=request.query_params.get('file_type', 'csv'),
            branch_id=walled_branch_id(request.user),
        )

    @action(detail=False, methods=['get'], url_path='import-template')
    def import_template(self, request):
        """The columns an import reads, as CSV, or Excel with ?file_type=xlsx
        (with dropdowns of the CRM's lists)."""
        from apps.lead.imports import template_response

        return template_response(request.query_params.get('file_type', 'csv'), request.user)

    def _checked_import(self, request):
        from apps.lead.imports import check_file

        serializer = LeadImportSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data['file']
        rows, columns, ignored = check_file(upload, defaults=serializer.defaults(), user=request.user)
        return serializer, upload, rows, columns, ignored

    @action(
        detail=False, methods=['post'], url_path='import/preview',
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_preview(self, request):
        """Check a file and show what an import would do. Nothing is saved."""
        from apps.lead.imports import ImportFileError, preview

        try:
            _, upload, rows, columns, ignored = self._checked_import(request)
        except ImportFileError as exc:
            return Response({'file': [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(preview(rows, columns, ignored, upload.name))

    @action(
        detail=False, methods=['post'], url_path='import',
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_leads(self, request):
        """Check the file again and save the rows that pass. Duplicates are
        skipped unless duplicates=import."""
        from apps.lead.imports import ImportFileError, import_rows

        try:
            serializer, upload, rows, _, _ = self._checked_import(request)
        except ImportFileError as exc:
            return Response({'file': [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        result = import_rows(
            rows, user=request.user, file_name=upload.name,
            include_duplicates=serializer.validated_data['duplicates'] == 'import',
        )
        return Response(result)

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

    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """Everything about the person, newest first. ?kind= shows one kind."""
        from apps.lead.timeline import lead_timeline

        items = lead_timeline(self.get_object(), branch_id=walled_branch_id(request.user))
        kind = request.query_params.get('kind')
        if kind:
            items = [i for i in items if i['kind'] == kind]
        return Response({'count': len(items), 'results': items})

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
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = FollowUpFilter
    search_fields = ['lead__full_name', 'lead__phone', 'purpose', 'assigned_to__name']
    ordering_fields = ['scheduled_at', 'created_at']
    ordering = ['-scheduled_at']

    def get_queryset(self):
        return FollowUp.objects.select_related(
            'lead', 'assigned_to', 'branch', 'enrollment__course',
        )

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'summary']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """How many follow-ups are overdue, due today and upcoming, with the list's filters."""
        from apps.lead.filters import DUE_BUCKETS, due_q, nepal_today_bounds

        bounds = nepal_today_bounds()
        counts = self.filter_queryset(self.get_queryset()).aggregate(**{
            bucket: Count('id', filter=due_q(bucket, bounds)) for bucket in DUE_BUCKETS
        })
        return Response({**counts, 'total': sum(counts.values())})


class GlobalEnrollmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Enrollment.objects.none()
    serializer_class = GlobalEnrollmentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalEnrollmentFilter
    search_fields = [
        'lead__full_name', 'lead__phone', 'student__full_name', 'student__student_id',
        'course__name', 'batch__name', 'current_batch__name',
    ]
    ordering_fields = ['enrollment_date', 'agreed_amount', 'created_at']
    ordering = ['-enrollment_date']

    def get_queryset(self):
        from apps.lead.services import with_seat

        return with_seat(Enrollment.objects.select_related(
            'lead', 'student', 'course', 'branch', 'batch', 'current_batch',
        ).prefetch_related('adjustments'))

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


class GlobalPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.none()
    serializer_class = GlobalPaymentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = GlobalPaymentFilter
    search_fields = [
        'enrollment__lead__full_name', 'enrollment__lead__phone', 'enrollment__course__name',
        'transaction_reference',
    ]
    ordering_fields = ['payment_date', 'amount', 'created_at']
    ordering = ['-payment_date']

    def get_queryset(self):
        return Payment.objects.select_related(
            'enrollment', 'enrollment__lead', 'enrollment__course', 'received_by', 'branch',
        ).prefetch_related('invoices')

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'summary']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """How many payments match the list's filters, and the total of the successful ones."""
        totals = self.filter_queryset(self.get_queryset()).aggregate(
            count=Count('id'),
            successful=Coalesce(
                Sum('amount', filter=Q(status=Payment.Status.SUCCESSFUL)), Decimal('0'),
            ),
        )
        return Response({'count': totals['count'], 'successful_total': str(totals['successful'])})


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

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Totals for the calls the same filters pick: count, talk time, in/out."""
        calls = self.filter_queryset(self.get_queryset()).order_by()
        totals = calls.aggregate(
            calls=Count('id'),
            talk_seconds=Coalesce(Sum('duration_seconds'), 0),
            inbound=Count('id', filter=Q(direction=LeadCall.Direction.INBOUND)),
            outbound=Count('id', filter=Q(direction=LeadCall.Direction.OUTBOUND)),
            leads=Count('lead', distinct=True),
        )
        totals['average_seconds'] = (
            round(totals['talk_seconds'] / totals['calls']) if totals['calls'] else 0
        )
        return Response(totals)

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'summary']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsStaffOrHasPermission()]


class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    pagination_class = CustomPagination
    # Each user sees their own notifications; they have no branch.
    filter_backends = [DjangoFilterBackend, OrderingFilter]
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
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    ordering_fields = ['created_at']
    ordering = ['-is_primary', '-created_at']

    def get_queryset(self):
        return LeadCourseInterest.objects.filter(lead_id=self.kwargs['lead_pk']).select_related(
            'course', 'counselor', 'source', 'branch', 'created_by',
        )

    def get_permissions(self):
        if self.action in ['stage', 'lose']:
            return [IsAuthenticated(), needs('lead.change_leadcourseinterest')()]
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), IsStaffOrHasPermission()]
        return [IsAuthenticated()]

    def _reply(self, enquiry, status_code=200):
        enquiry = self.get_queryset().get(pk=enquiry.pk)
        return Response(LeadCourseInterestSerializer(enquiry).data, status=status_code)

    def create(self, request, *args, **kwargs):
        from apps.lead.enquiries import add_enquiry

        from apps.lead import voice_notes

        lead = get_object_or_404(Lead, pk=self.kwargs['lead_pk'], is_deleted=False)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        recording = self._checked_recording(request, lead)
        with transaction.atomic():
            enquiry = add_enquiry(
                lead,
                course=data.get('course'),
                course_name=data.get('course_name'),
                user=request.user,
                source=data.get('source'),
                counselor=data.get('counselor'),
                is_primary=data.get('is_primary', False),
                interest_level=data.get('interest_level'),
                about=data.get('about', ''),
                description=data.get('description', ''),
            )
            if recording:
                voice_notes.record(
                    recording['file'], lead=lead, enquiry=enquiry,
                    duration_seconds=recording.get('duration_seconds', 0), user=request.user,
                )
        return self._reply(enquiry, status.HTTP_201_CREATED)

    def _checked_recording(self, request, lead):
        """A description recorded instead of typed: multipart `recording` (+ `recording_seconds`).

        It becomes a voice note on the new enquiry. About and description stay
        optional here: the lead form and the phone match box add enquiries
        with a course only. The Add enquiry form asks for them.
        """
        file = request.FILES.get('recording')
        if not file:
            return None
        check = VoiceNoteSerializer(
            data={'file': file, 'duration_seconds': request.data.get('recording_seconds') or 0},
            context={'lead_pk': lead.pk},
        )
        if not check.is_valid():
            raise ValidationError({'recording': [str(e) for errs in check.errors.values() for e in errs]})
        return check.validated_data

    def perform_destroy(self, instance):
        from apps.lead.services import ActionRefused

        if instance.stage != LeadCourseInterest.Stage.NEW or instance.stage_changes.exists():
            raise ActionRefused('This enquiry has moved past New, so it is kept. Mark it lost instead.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def stage(self, request, *args, **kwargs):
        """Move to New, Contacted or Interested. A lost enquiry needs a note to come back."""
        from apps.lead.enquiries import move_stage

        body = EnquiryStageSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        enquiry = move_stage(
            self.get_object(), body.validated_data['stage'],
            note=body.validated_data['note'], user=request.user,
        )
        return self._reply(enquiry)

    @action(detail=True, methods=['post'])
    def lose(self, request, *args, **kwargs):
        from apps.lead.enquiries import mark_lost

        body = EnquiryLostSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        enquiry = mark_lost(
            self.get_object(), reason=body.validated_data['reason'],
            note=body.validated_data['note'], user=request.user,
        )
        return self._reply(enquiry)

    @action(detail=True, methods=['get'])
    def history(self, request, *args, **kwargs):
        changes = self.get_object().stage_changes.select_related('changed_by')
        return Response(EnquiryStageChangeSerializer(changes, many=True).data)


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
            'payments', 'adjustments', 'payment_plans__installments', 'invoices',
            'placements__course_batch', 'placements__from_batch', 'placements__created_by',
            'teams_licences',
        )

    def perform_create(self, serializer):
        from apps.lead.emails import enrollment_received

        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        data = serializer.validated_data
        batch = data.get('batch')
        # Staff adding an enrollment with a batch: it belongs to the batch's branch.
        given = data.get('branch') or (batch.branch if batch is not None else None)
        branch = new_record_branch(self.request, lead, given)
        self._batch_must_be_of(batch, branch)
        enrollment = serializer.save(lead=lead, created_by=self.request.user, branch=branch)
        enrollment_received(enrollment, user=self.request.user)

    @staticmethod
    def _batch_must_be_of(batch, branch):
        """A batch belongs to one branch; an enrollment can only plan a batch of its own."""
        if batch is not None and batch.branch_id and branch is not None and batch.branch_id != branch.pk:
            raise ValidationError({
                'batch': [f'{batch.name} is a batch of {batch.branch.name}. Pick a batch of {branch.name}.'],
            })

    def perform_update(self, serializer):
        data = serializer.validated_data
        if 'batch' in data or 'branch' in data:
            self._batch_must_be_of(
                data['batch'] if 'batch' in data else serializer.instance.batch,
                data['branch'] if 'branch' in data else serializer.instance.branch,
            )
        branch = serializer.validated_data.get('branch')
        if (
            'branch' in serializer.validated_data
            and walled_branch_id(self.request.user) is not None
            and branch != serializer.instance.branch
        ):
            raise ValidationError({'branch': ['Only staff can move an enrollment to another branch.']})
        if (
            'branch' in serializer.validated_data
            and branch != serializer.instance.branch
            and serializer.instance.student_id
        ):
            raise ValidationError({'branch': ['Use Move branch on the student to change this branch.']})
        serializer.save(updated_by=self.request.user)

    # Actions that change an enrollment need change_enrollment, not add_.
    STATE_ACTIONS = (
        'confirm', 'cancel', 'reinstate', 'change_status', 'join_batch', 'move', 'pause', 'resume',
        'due_dates', 'fee_invoice',
    )

    def get_permissions(self):
        if self.action in self.STATE_ACTIONS:
            return [IsAuthenticated(), needs('lead.change_enrollment')()]
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), IsStaffOrHasPermission()]
        return [IsAuthenticated()]

    def _reason(self, request):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data['reason']

    @action(detail=True, methods=['post'])
    def confirm(self, request, *args, **kwargs):
        """Confirm a pending enrollment. This makes the lead a student.

        Something must be paid first. Optional body: {"due_dates": [{"due_on",
        "amount"}, ...], "first_payment": {"amount", "payment_method",
        "payment_date", "transaction_reference", "notes"}}.
        """
        enrollment = self.get_object()
        body = DueDatesSerializer(data=request.data, for_confirm=True)
        body.is_valid(raise_exception=True)
        enrollment = services.confirm_enrollment(
            enrollment, user=request.user, due_dates=body.validated_data.get('due_dates'),
            first_payment=body.validated_data.get('first_payment'),
        )
        return self._fresh_reply(enrollment)

    @action(detail=True, methods=['post'], url_path='fee-invoice')
    def fee_invoice(self, request, *args, **kwargs):
        """Make a new fee invoice when the old one was cancelled."""
        from apps.invoices.serializers import InvoiceSerializer

        invoice = services.reissue_fee_invoice(self.get_object(), user=request.user)
        return Response(InvoiceSerializer(invoice).data, status=201)

    @action(detail=True, methods=['post'], url_path='due-dates')
    def due_dates(self, request, *args, **kwargs):
        """Change the due dates, with a reason. The old ones stay in the history."""
        enrollment = self.get_object()
        body = DueDatesSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        enrollment = services.set_due_dates(
            enrollment, body.validated_data['due_dates'],
            reason=body.validated_data['reason'], user=request.user,
        )
        return self._fresh_reply(enrollment)

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

    @action(detail=True, methods=['post'], url_path='status')
    def change_status(self, request, *args, **kwargs):
        """Set the status of a confirmed enrollment, with a reason."""
        enrollment = self.get_object()
        body = StatusChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        enrollment = services.change_status(
            enrollment, body.validated_data['status'],
            reason=body.validated_data['reason'], user=request.user,
        )
        return self._fresh_reply(enrollment)

    # ---- seat actions: each adds a seat history row and a history row.

    def _seat_body(self, request, *, needs_batch=True, needs_reason=True):
        body = SeatActionSerializer(
            data=request.data, needs_batch=needs_batch, needs_reason=needs_reason,
        )
        body.is_valid(raise_exception=True)
        return body.validated_data

    def _fresh_reply(self, enrollment):
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
        return self._fresh_reply(enrollment)

    @action(detail=True, methods=['post'])
    def move(self, request, *args, **kwargs):
        """Move the student to another batch of the course. The money stays put."""
        enrollment = self.get_object()
        body = self._seat_body(request)
        enrollment = services.move_batch(
            enrollment, body['batch'], reason=body['reason'], user=request.user,
            on=body.get('started_on'),
        )
        return self._fresh_reply(enrollment)

    @action(detail=True, methods=['post'])
    def pause(self, request, *args, **kwargs):
        """Take the student out of their batch for now. Frees the seat."""
        enrollment = self.get_object()
        body = self._seat_body(request, needs_batch=False)
        enrollment = services.pause_enrollment(
            enrollment, reason=body['reason'], user=request.user, on=body.get('started_on'),
        )
        return self._fresh_reply(enrollment)

    @action(detail=True, methods=['post'])
    def resume(self, request, *args, **kwargs):
        """Bring a paused student back into a batch of the course."""
        enrollment = self.get_object()
        body = self._seat_body(request)
        enrollment = services.resume_enrollment(
            enrollment, body['batch'], reason=body['reason'], user=request.user,
            on=body.get('started_on'),
        )
        return self._fresh_reply(enrollment)


class PaymentViewSet(viewsets.ModelViewSet):
    """Payments are only added. Fix a wrong one with void (reason) and record a new one."""
    serializer_class = PaymentSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = PaymentFilter
    ordering_fields = ['payment_date', 'created_at']
    ordering = ['-payment_date']

    # An enrollment's payments show to the branch that has the enrollment now,
    # also those taken at a branch the student left. Money lists and reports
    # still count each payment at the branch that took it.
    branch_field = 'enrollment__branch'

    def get_queryset(self):
        return Payment.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        ).select_related('received_by', 'voided_by').prefetch_related('invoices')

    @transaction.atomic
    def perform_create(self, serializer):
        enrollment = get_object_or_404(
            Enrollment.objects.select_for_update(),
            pk=self.kwargs['enrollment_pk'], lead=visible_lead(self.request, self.kwargs['lead_pk']),
        )
        refusal = services.payment_refusal(enrollment, serializer.validated_data['amount'])
        if refusal:
            raise ValidationError({'amount': [refusal]})
        payment = serializer.save(
            enrollment=enrollment,
            received_by=self.request.user,
            branch=enrollment.branch,
        )
        services.issue_receipt(payment, user=self.request.user)

    def get_permissions(self):
        if self.action in ['confirm', 'void', 'receipt']:
            return [IsAuthenticated(), needs('lead.change_payment')()]
        if self.action == 'create':
            return [IsAuthenticated(), IsStaffOrHasPermission()]
        return [IsAuthenticated()]

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

    @action(detail=True, methods=['post'])
    def receipt(self, request, *args, **kwargs):
        """Make a new receipt when the old one was cancelled."""
        from apps.invoices.serializers import InvoiceSerializer

        receipt = services.reissue_receipt(self.get_object(), user=request.user)
        return Response(InvoiceSerializer(receipt).data, status=201)


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


class EnrollmentReminderViewSet(viewsets.ReadOnlyModelViewSet):
    """Payment reminders sent for one enrollment, newest first."""
    serializer_class = ReminderLogSerializer
    pagination_class = CustomPagination
    filter_backends = [BranchFilterBackend]
    branch_field = 'enrollment__branch'

    def get_queryset(self):
        return ReminderLog.objects.filter(
            enrollment_id=self.kwargs['enrollment_pk'],
            enrollment__lead_id=self.kwargs['lead_pk'],
        )


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

        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        instance = charge_for_call(
            user=self.request.user,
            duration_seconds=serializer.validated_data.get('duration_seconds'),
            create_call=lambda: serializer.save(
                lead=lead,
                created_by=self.request.user,
                branch=new_record_branch(self.request, lead, serializer.validated_data.get('branch')),
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
        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        instance = serializer.save(
            lead=lead,
            performed_by=self.request.user,
            branch=new_record_branch(self.request, lead, serializer.validated_data.get('branch')),
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
            'assigned_to', 'created_by', 'updated_by', 'enrollment__course',
        )

    # Saving a follow-up updates the lead's next follow-up and last contacted
    # dates (FollowUp.save).
    def perform_create(self, serializer):
        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        serializer.save(
            lead=lead, created_by=self.request.user,
            branch=new_record_branch(self.request, lead, serializer.validated_data.get('branch')),
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

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
        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        serializer.save(
            lead=lead,
            uploaded_by=self.request.user,
            branch=new_record_branch(self.request, lead, serializer.validated_data.get('branch')),
        )

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


def new_record_branch(request, lead=None, given=None):
    """The branch a new record belongs to.

    A branch user's own branch, always. For staff (and users with no branch),
    the branch they sent, else the lead's.
    """
    if walled_branch_id(request.user) is not None:
        return request.user.branch
    return given or (lead.branch if lead is not None else None)


def visible_lead(request, lead_pk):
    """The lead, if this user may see it (their branch's leads, or all for staff); else 404."""
    leads = LeadBranchFilterBackend().filter_queryset(
        request, Lead.objects.filter(is_deleted=False), view=None,
    )
    return get_object_or_404(leads, pk=lead_pk)


class VoiceNoteViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Voice notes on a lead's enquiries. GET {id}/audio/ plays the recording.

    Anyone who can work enquiries records. Only the person who recorded a
    note, or staff, deletes it; the file goes with it.
    """

    serializer_class = VoiceNoteSerializer
    pagination_class = CustomPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        qs = lead.voice_notes.select_related('created_by', 'enquiry__course')
        # A branch hears only its own recordings, as in every list.
        branch_id = walled_branch_id(self.request.user)
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        enquiry = self.request.query_params.get('enquiry')
        if enquiry:
            qs = qs.filter(enquiry_id=enquiry)
        return qs

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'lead_pk': self.kwargs.get('lead_pk')}

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), needs('lead.change_leadcourseinterest')()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        from apps.lead import voice_notes

        lead = visible_lead(self.request, self.kwargs['lead_pk'])
        data = serializer.validated_data
        serializer.instance = voice_notes.record(
            data['file'], lead=lead, enquiry=data.get('enquiry'),
            duration_seconds=data.get('duration_seconds', 0), note=data.get('note', ''),
            user=self.request.user,
        )

    def destroy(self, request, *args, **kwargs):
        note = self.get_object()
        if not (request.user.is_staff or note.created_by_id == request.user.id):
            return Response(
                {'detail': 'Only the person who recorded it or an admin can delete a voice note.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def audio(self, request, lead_pk=None, pk=None):
        from apps.lead import voice_notes

        note = self.get_object()
        try:
            body = voice_notes.read_file(note.file_key)
        except Exception:
            return Response({'detail': 'The recording could not be found.'}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(body, content_type=note.content_type)
        response['Content-Disposition'] = 'inline'
        response['Cache-Control'] = 'private, max-age=3600'
        return response


class TeamsLicenceViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """Teams licences given to students.

    POST gives a ready account (and can email the sign-in), POST {id}/free/
    takes it back with a reason, POST {id}/send-details/ emails the sign-in.
    """

    serializer_class = TeamsLicenceSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    filterset_class = TeamsLicenceFilter
    search_fields = ['account', 'enrollment__lead__full_name', 'enrollment__student__full_name']
    ordering_fields = ['assigned_at', 'released_at']
    ordering = ['-assigned_at']

    def get_queryset(self):
        return TeamsLicence.objects.select_related(
            'enrollment__lead', 'enrollment__student', 'enrollment__course',
            'enrollment__current_batch', 'assigned_by', 'released_by',
        )

    def get_permissions(self):
        perms = {
            'create': 'lead.add_teamslicence',
            'free': 'lead.change_teamslicence',
            'send_details': 'lead.add_teamslicence',
        }
        if self.action in perms:
            return [IsAuthenticated(), needs(perms[self.action])()]
        return [IsAuthenticated()]

    def _reply(self, licence, status_code=200, email=None):
        licence = self.get_queryset().get(pk=licence.pk)
        data = dict(self.get_serializer(licence).data)
        if email is not None:
            data['email'] = {'status': email.status, 'to': email.to_email, 'error': email.error}
        return Response(data, status=status_code)

    def _check_branch(self, enrollment):
        branch_id = walled_branch_id(self.request.user)
        if branch_id is not None and enrollment.branch_id != branch_id:
            raise ValidationError({'enrollment': 'This enrollment belongs to another branch.'})

    def create(self, request, *args, **kwargs):
        from apps.lead.teams import give_licence, send_details

        body = GiveLicenceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        self._check_branch(data['enrollment'])
        licence = give_licence(data['enrollment'], teams_account=data['teams_account'], user=request.user)
        email = None
        if data['send_details']:
            email = send_details(licence, password=data['password'], user=request.user)
        return self._reply(licence, status.HTTP_201_CREATED, email=email)

    @action(detail=True, methods=['post'], url_path='send-details')
    def send_details(self, request, *args, **kwargs):
        from apps.lead.teams import send_details

        body = SendTeamsDetailsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        licence = self.get_object()
        email = send_details(licence, password=body.validated_data['password'], user=request.user)
        return self._reply(licence, email=email)

    @action(detail=True, methods=['post'])
    def free(self, request, *args, **kwargs):
        from apps.lead.teams import free_licence

        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        licence = free_licence(self.get_object(), reason=body.validated_data['reason'], user=request.user)
        return self._reply(licence)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        from apps.lead.teams import summary

        return Response(summary(_teams_branch(request)))


def _teams_branch(request):
    """The branch whose Teams accounts to show: the user's own, or ?branch_id for staff."""
    branch_id = walled_branch_id(request.user)
    if branch_id is None:
        picked = request.query_params.get('branch_id')
        branch_id = int(picked) if picked and picked.isdigit() else None
    return branch_id


class TeamsAccountViewSet(viewsets.ViewSet):
    """The Teams accounts the institute owns, each with its state.

    GET lists them (?state=, ?search=, ?give_at=<branch> for accounts that can
    be given at a branch). POST {emails, branch} adds accounts. POST
    {id}/retire/ with a reason stops counting one; {id}/restore/ undoes it.
    """

    def get_permissions(self):
        perms = {
            'create': 'lead.add_teamsaccount',
            'retire': 'lead.change_teamsaccount',
            'restore': 'lead.change_teamsaccount',
        }
        if self.action in perms:
            return [IsAuthenticated(), needs(perms[self.action])()]
        return [IsAuthenticated()]

    def _account(self, pk):
        """An account this user may change: their branch's. Shared ones are for staff."""
        from apps.lead.teams import visible_accounts

        branch_id = walled_branch_id(self.request.user)
        account = get_object_or_404(visible_accounts(branch_id), pk=pk)
        if branch_id is not None and account.branch_id is None:
            raise ActionRefused('This account is shared by every branch. An admin can change it.')
        return account

    def _row(self, account):
        from apps.lead.teams import account_row, visible_accounts

        branch_id = _teams_branch(self.request)
        return account_row(visible_accounts(branch_id).get(pk=account.pk), branch_id=branch_id)

    def list(self, request):
        from apps.lead.teams import account_rows

        rows = account_rows(_teams_branch(request))
        give_at = request.query_params.get('give_at')
        if give_at and give_at.isdigit():
            rows = [r for r in rows if r['branch'] in (None, int(give_at))]
        state = request.query_params.get('state')
        if state:
            rows = [r for r in rows if r['state'] == state]
        search = (request.query_params.get('search') or '').strip().lower()
        if search:
            def hit(row):
                licence = row['licence'] or {}
                text = ' '.join(str(v or '') for v in (
                    row['email'], licence.get('name'), licence.get('student_id'),
                    licence.get('email'), licence.get('course_name'), licence.get('batch_name'),
                ))
                return search in text.lower()

            rows = [r for r in rows if hit(r)]
        return Response(rows)

    def create(self, request):
        from apps.lead.teams import add_accounts

        body = AddTeamsAccountsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        branch = body.validated_data.get('branch')
        branch_id = walled_branch_id(request.user)
        if branch_id is not None:
            branch = request.user.branch
        added, existing = add_accounts(body.validated_data['emails'], branch=branch, user=request.user)
        return Response({'added': added, 'existing': existing}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):
        from apps.lead.teams import retire_account

        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        account = retire_account(self._account(pk), reason=body.validated_data['reason'], user=request.user)
        return Response(self._row(account))

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        from apps.lead.teams import restore_account

        account = restore_account(self._account(pk))
        return Response(self._row(account))

    @action(detail=False, methods=['get'])
    def summary(self, request):
        from apps.lead.teams import summary

        return Response(summary(_teams_branch(request)))

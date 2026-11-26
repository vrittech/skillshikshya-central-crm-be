import csv

from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend

from apps.student.models import Student, StudentDocument, Attendance
from apps.student.serializers import (
    BranchMoveSerializer, MoveBranchSerializer, StudentSerializer, StudentListSerializer,
    StudentDocumentSerializer, AttendanceSerializer,
)
from apps.student.filters import StudentFilter, StudentDocumentFilter, AttendanceFilter
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission, needs
from apps.global_config.filters import BranchFilterBackend, walled_branch_id


class StudentViewSet(viewsets.ModelViewSet):
    queryset = Student.objects.select_related(
        'source', 'branch', 'assigned_counselor', 'created_by', 'updated_by',
    )
    pagination_class = CustomPagination
    filterset_class = StudentFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = [
        'student_id', 'full_name', 'phone', 'email',
        'guardian_name', 'guardian_phone', 'notes',
    ]
    ordering_fields = [
        'id', 'student_id', 'full_name', 'status',
        'enrolled_at', 'created_at', 'updated_at',
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return StudentListSerializer
        return StudentSerializer

    @action(detail=True, methods=['get'])
    def attendance_profile(self, request, pk=None):
        student = self.get_object()
        course_id = request.query_params.get('course')
        profile = student.get_attendance_profile(course=course_id)

        recent = Attendance.objects.filter(student=student)
        if course_id:
            recent = recent.filter(course_id=course_id)
        recent = recent.select_related('course', 'marked_by').order_by('-date')[:20]

        profile['recent_records'] = AttendanceSerializer(recent, many=True).data
        return Response(profile)

    def perform_create(self, serializer):
        # A lead becomes a student only through Confirm enrollment, which also
        # links the enrollment and makes the fee invoice.
        if serializer.validated_data.get('lead'):
            raise ValidationError({
                'lead': 'A lead becomes a student when one of their enrollments is '
                        'confirmed. Use Confirm enrollment on the lead.'
            })
        # A branch user's students are always their own branch's.
        branch = (
            self.request.user.branch if walled_branch_id(self.request.user) is not None
            else serializer.validated_data.get('branch')
        )
        if branch is None:
            raise ValidationError({'branch': ['Pick a branch.']})
        serializer.save(created_by=self.request.user, branch=branch)

    def perform_update(self, serializer):
        branch = serializer.validated_data.get('branch')
        if 'branch' in serializer.validated_data and branch != serializer.instance.branch:
            raise ValidationError({'branch': ['Use Move branch to change a student’s branch.']})
        serializer.save(updated_by=self.request.user)

    def get_permissions(self):
        if self.action == 'move_branch':
            return [IsAuthenticated(), needs('student.move_branch')()]
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=True, methods=['post'], url_path='move-branch')
    def move_branch(self, request, pk=None):
        """Move the student (and their open enrollments) to another branch, with a reason.

        A branch user can only move their own branch's students.
        """
        from apps.lead.services import move_student_branch

        student = self.get_object()
        body = MoveBranchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        student = move_student_branch(
            student, body.validated_data['branch'], reason=body.validated_data['reason'], user=request.user,
        )
        student = Student.objects.select_related(
            'source', 'branch', 'assigned_counselor', 'created_by', 'updated_by',
        ).get(pk=student.pk)
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['get'], url_path='branch-moves')
    def branch_moves(self, request, pk=None):
        """Every branch move of this student, newest first."""
        moves = self.get_object().branch_moves.select_related('from_branch', 'to_branch', 'moved_by')
        return Response(BranchMoveSerializer(moves, many=True).data)

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="students.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Student ID', 'Full Name', 'Phone', 'Email', 'Gender',
            'Status', 'Enrolled At', 'Branch', 'Source',
            'Guardian Name', 'Guardian Phone', 'City', 'State',
            'Created At', 'Updated At',
        ])
        for s in queryset.iterator(chunk_size=500):
            writer.writerow([
                s.student_id, s.full_name, s.phone, s.email,
                s.get_gender_display() if s.gender else '',
                s.get_status_display(),
                s.enrolled_at,
                s.branch.name if s.branch else '',
                s.source.name if s.source else '',
                s.guardian_name, s.guardian_phone,
                s.city, s.state,
                s.created_at, s.updated_at,
            ])
        return response


def visible_student(request, pk):
    """The student, if this user may see them (their branch's, or all for staff); else 404."""
    students = BranchFilterBackend().filter_queryset(request, Student.objects.all(), view=None)
    return get_object_or_404(students, pk=pk)


class StudentDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = StudentDocumentSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    filterset_class = StudentDocumentFilter
    ordering_fields = ['uploaded_at']
    ordering = ['-uploaded_at']

    def get_queryset(self):
        return StudentDocument.objects.filter(student_id=self.kwargs['student_pk']).select_related('uploaded_by')

    def perform_create(self, serializer):
        student = visible_student(self.request, self.kwargs['student_pk'])
        branch = (
            self.request.user.branch if walled_branch_id(self.request.user) is not None
            else serializer.validated_data.get('branch') or student.branch
        )
        serializer.save(student=student, uploaded_by=self.request.user, branch=branch)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class AttendanceViewSet(viewsets.ModelViewSet):
    queryset = Attendance.objects.all()
    serializer_class = AttendanceSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, BranchFilterBackend]
    # Attendance has no branch of its own: it follows the student's.
    branch_field = 'student__branch'
    filterset_class = AttendanceFilter
    ordering_fields = ['date', 'created_at']
    ordering = ['-date']

    def get_queryset(self):
        qs = Attendance.objects.select_related('course', 'marked_by')
        if 'student_pk' in self.kwargs:
            qs = qs.filter(student_id=self.kwargs['student_pk'])
        return qs

    def perform_create(self, serializer):
        if 'student_pk' not in self.kwargs:
            raise ValidationError({'student': ['This field is required.']})
        student = visible_student(self.request, self.kwargs['student_pk'])
        serializer.save(marked_by=self.request.user, student=student)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

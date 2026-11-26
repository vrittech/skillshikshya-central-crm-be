from rest_framework import serializers
from apps.branch.models import Branch
from apps.student.models import BranchMove, Student, StudentDocument, Attendance
from apps.lead.models import Payment


class StudentListSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    assigned_counselor_name = serializers.CharField(source='assigned_counselor.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = Student
        fields = [
            'id', 'student_id', 'full_name', 'phone', 'email',
            'gender', 'status', 'enrolled_at', 'branch_name',
            'source_name', 'assigned_counselor_name', 'created_by_name',
            'created_at',
        ]


class StudentSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    assigned_counselor_name = serializers.CharField(source='assigned_counselor.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    updated_by_name = serializers.CharField(source='updated_by.name', read_only=True)
    document_count = serializers.SerializerMethodField()
    total_paid = serializers.SerializerMethodField()
    recent_payments = serializers.SerializerMethodField()
    enrollment_count = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = '__all__'
        read_only_fields = [
            'student_id', 'created_at', 'updated_at',
            'created_by', 'updated_by',
        ]

    def get_document_count(self, obj):
        return obj.documents.count()

    def get_total_paid(self, obj):
        from django.db.models import Sum
        result = Payment.objects.filter(
            enrollment__student=obj,
            status='successful',
        ).aggregate(Sum('amount'))
        return result['amount__sum'] or 0

    def get_recent_payments(self, obj):
        payments = Payment.objects.filter(
            enrollment__student=obj,
        ).select_related('enrollment__course').order_by('-payment_date')[:5]
        return [{
            'id': p.id,
            'amount': str(p.amount),
            'payment_date': p.payment_date,
            'payment_method': p.payment_method,
            'status': p.status,
            'course': p.enrollment.course.name if p.enrollment else None,
            'transaction_reference': p.transaction_reference,
        } for p in payments]

    def get_enrollment_count(self, obj):
        return obj.enrollments.count()


class StudentDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source='uploaded_by.name', read_only=True)

    class Meta:
        model = StudentDocument
        fields = '__all__'
        read_only_fields = ['student', 'uploaded_by', 'uploaded_at']


class AttendanceSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    marked_by_name = serializers.CharField(source='marked_by.name', read_only=True)
    student_name = serializers.CharField(source='student.full_name', read_only=True)

    class Meta:
        model = Attendance
        fields = '__all__'
        read_only_fields = ['student', 'marked_by', 'created_at']


class MoveBranchSerializer(serializers.Serializer):
    """Body of Move branch: the branch to move to and why."""

    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.all())
    reason = serializers.CharField(max_length=1000)


class BranchMoveSerializer(serializers.ModelSerializer):
    from_branch_name = serializers.CharField(source='from_branch.name', read_only=True, default=None)
    to_branch_name = serializers.CharField(source='to_branch.name', read_only=True)
    moved_by_name = serializers.CharField(source='moved_by.name', read_only=True)

    class Meta:
        model = BranchMove
        fields = [
            'id', 'from_branch', 'from_branch_name', 'to_branch', 'to_branch_name',
            'reason', 'moved_by', 'moved_by_name', 'moved_at',
        ]

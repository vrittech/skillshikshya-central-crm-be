from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='users',
    )
    daily_call_target_min = models.IntegerField(null=True, blank=True)
    monthly_enrollment_target = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    call_seconds_balance = models.IntegerField(default=0)
    designation = models.CharField(max_length=150, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    shift_start = models.TimeField(null=True, blank=True)
    shift_end = models.TimeField(null=True, blank=True)
    custom_weekly_offs = models.BooleanField(default=False)

    work_sun = models.BooleanField(default=False)
    work_mon = models.BooleanField(default=False)
    work_tue = models.BooleanField(default=False)
    work_wed = models.BooleanField(default=False)
    work_thu = models.BooleanField(default=False)
    work_fri = models.BooleanField(default=False)
    work_sat = models.BooleanField(default=False)

    exclude_from_calls = models.BooleanField(default=False)
    exclude_from_enrollment = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        ordering = ['-created_at']

    def __str__(self):
        return self.name





NO_BRANCH_MESSAGE = 'Your account has no branch yet. Ask an admin to add one.'


def has_no_branch(user):
    """Everyone but staff works inside one branch; without one they can't sign in."""
    return not (user.is_staff or user.is_superuser) and user.branch_id is None

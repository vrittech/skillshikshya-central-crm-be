from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Batch, Course, CourseBatch

User = get_user_model()


class CourseBatchDetailsTests(APITestCase):
    """A batch has seats, a status, a mode (online or physical) and a trainer."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.slot = Batch.objects.create(name='Morning')

    def _create(self, **extra):
        body = {'name': 'Python Nov', 'course': self.course.id, 'batch': self.slot.id, **extra}
        return self.client.post('/api/course-batches/', body, format='json')

    def test_new_batch_is_upcoming_physical_with_no_seat_limit(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'upcoming')
        self.assertEqual(response.data['status_display'], 'Upcoming')
        self.assertEqual(response.data['mode'], 'physical')
        self.assertIsNone(response.data['capacity'])
        self.assertIsNone(response.data['trainer'])

    def test_seats_mode_status_and_trainer_are_saved(self):
        response = self._create(capacity=25, mode='online', status='running', trainer='Ram')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        batch = CourseBatch.objects.get(pk=response.data['id'])
        self.assertEqual(
            (batch.capacity, batch.mode, batch.status, batch.trainer),
            (25, 'online', 'running', 'Ram'),
        )
        self.assertEqual(response.data['mode_display'], 'Online')

    def test_unknown_status_is_refused(self):
        response = self._create(status='finished')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)

    def test_negative_seats_are_refused(self):
        response = self._create(capacity=-1)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('capacity', response.data)

    def test_list_filters_by_status_and_mode(self):
        CourseBatch.objects.create(name='A', course=self.course, status='running', mode='online')
        CourseBatch.objects.create(name='B', course=self.course, status='closed')
        CourseBatch.objects.create(name='C', course=self.course, status='upcoming')

        response = self.client.get('/api/course-batches/?status=running&status=upcoming')
        self.assertEqual(sorted(b['name'] for b in response.data['results']), ['A', 'C'])

        response = self.client.get('/api/course-batches/?mode=online')
        self.assertEqual([b['name'] for b in response.data['results']], ['A'])


class MarkStartedBatchesRunningTests(APITestCase):
    """The migration marks batches that already started as running, and closes none."""

    def test_started_batches_become_running(self):
        course = Course.objects.create(name='Python', full_amount=30000)
        today = timezone.localdate()
        started = CourseBatch.objects.create(
            name='Started', course=course, start_date=today - timedelta(days=10),
        )
        later = CourseBatch.objects.create(
            name='Later', course=course, start_date=today + timedelta(days=10),
        )
        no_date = CourseBatch.objects.create(name='No date', course=course)

        migration = import_module('apps.course.migrations.0005_batch_seats_status_mode_trainer')
        migration.mark_started_batches_running(django_apps, None)

        for batch, expected in ((started, 'running'), (later, 'upcoming'), (no_date, 'upcoming')):
            batch.refresh_from_db()
            self.assertEqual(batch.status, expected, batch.name)

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.branch.models import Branch
from apps.course.models import Course
from apps.lead.models import Enrollment, Lead
from apps.student.models import Student

User = get_user_model()


class LeadConversionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(
            full_name='Fresh Lead', phone='9800000077',
        )

    def test_student_from_a_lead_is_refused(self):
        # A lead becomes a student only through Confirm enrollment.
        response = self.client.post('/api/students/', {
            'full_name': 'Converted Student',
            'phone': '9800000077',
            'lead': self.lead.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Confirm enrollment', str(response.data['lead']))
        self.assertFalse(Student.objects.exists())
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_converted)

    def test_standalone_student_does_not_touch_leads(self):
        other_lead = Lead.objects.create(full_name='Untouched', phone='1')
        response = self.client.post('/api/students/', {
            'full_name': 'No Lead Student',
            'phone': '9800000080',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        other_lead.refresh_from_db()
        self.assertFalse(other_lead.is_converted)
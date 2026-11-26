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
        branch = Branch.objects.create(name='Kathmandu', code='KTM')
        response = self.client.post('/api/students/', {
            'full_name': 'No Lead Student',
            'phone': '9800000080',
            'branch': branch.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        other_lead.refresh_from_db()
        self.assertFalse(other_lead.is_converted)

class BranchMoveTests(APITestCase):
    """A student moves branch with a reason; open enrollments go along, money stays."""

    def setUp(self):
        from datetime import date

        from django.contrib.auth.models import Group
        from django.utils import timezone

        from apps.course.models import CourseBatch
        from apps.lead import services
        from apps.lead.models import Payment

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(email='a@test.com', name='Admin', password='x', is_staff=True)
        manager = Group.objects.get(name='Branch Manager')
        self.ktm_manager = User.objects.create_user(email='km@test.com', name='KTM M', password='x', branch=self.ktm)
        self.ktm_manager.groups.add(manager)
        self.pkr_manager = User.objects.create_user(email='pm@test.com', name='PKR M', password='x', branch=self.pkr)
        self.pkr_manager.groups.add(manager)
        self.ktm_desk = User.objects.create_user(email='kd@test.com', name='KTM D', password='x', branch=self.ktm)
        self.ktm_desk.groups.add(Group.objects.get(name='Front Desk'))

        course = Course.objects.create(name='Python', full_amount=30000)
        self.ktm_batch = CourseBatch.objects.create(name='Python KTM', course=course, capacity=5, branch=self.ktm)
        self.pkr_batch = CourseBatch.objects.create(name='Python PKR', course=course, capacity=5, branch=self.pkr)
        self.lead = Lead.objects.create(full_name='Sita', phone='9800000990', branch=self.ktm)
        self.student = Student.objects.create(full_name='Sita', phone='9800000990', branch=self.ktm, lead=self.lead)
        fee = {'enrollment_date': date.today(), 'listed_fee': 30000, 'agreed_amount': 30000}
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, student=self.student, course=course, branch=self.ktm, status='confirmed', **fee,
        )
        services.join_batch(self.enrollment, self.ktm_batch, user=self.admin)
        self.done = Enrollment.objects.create(
            lead=self.lead, student=self.student, course=course, branch=self.ktm, status='completed', **fee,
        )
        self.payment = Payment.objects.create(
            enrollment=self.enrollment, amount=5000, payment_date=timezone.now(),
            payment_method='cash', branch=self.ktm,
        )

    def _move(self, user, branch=None, reason='Family moved to Pokhara'):
        self.client.force_authenticate(user)
        body = {'branch': (branch or self.pkr).id, 'reason': reason}
        return self.client.post(f'/api/students/{self.student.id}/move-branch/', body, format='json')

    def _enrollment_url(self, action=''):
        return f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/{action}'

    def test_open_enrollments_move_and_money_stays(self):
        from apps.lead.models import EnrollmentHistory, Placement

        response = self._move(self.ktm_manager)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['branch'], self.pkr.id)
        self.student.refresh_from_db()
        self.enrollment.refresh_from_db()
        self.done.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.student.branch, self.pkr)
        self.assertEqual(self.enrollment.branch, self.pkr)
        self.assertIsNone(self.enrollment.current_batch)  # a batch belongs to one branch
        self.assertEqual(self.enrollment.batch, self.ktm_batch)  # money batch unchanged
        self.assertEqual(self.done.branch, self.ktm)  # finished enrollments stay
        self.assertEqual(self.payment.branch, self.ktm)  # money stays where it was taken
        move = self.student.branch_moves.get()
        self.assertEqual((move.from_branch, move.to_branch, move.moved_by), (self.ktm, self.pkr, self.ktm_manager))
        self.assertEqual(move.reason, 'Family moved to Pokhara')
        history = self.enrollment.history.get(action=EnrollmentHistory.Action.MOVE_BRANCH)
        self.assertIn('Kathmandu to Pokhara', history.reason)
        self.assertEqual(history.before['batch'], self.ktm_batch.coursebatch_id)
        self.assertEqual(self.enrollment.placements.order_by('-id').first().kind, Placement.Kind.LEAVE)

    def test_new_branch_works_the_student_after_the_move(self):
        self._move(self.admin)
        self.client.force_authenticate(self.pkr_manager)
        self.assertEqual(self.client.get(f'/api/students/{self.student.id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/leads/{self.lead.id}/').status_code, 200)
        self.assertEqual(self.client.get(self._enrollment_url()).status_code, 200)
        payments = self.client.get(self._enrollment_url('payments/')).data['results']
        self.assertEqual([p['id'] for p in payments], [self.payment.id])  # also the old branch's
        self.assertEqual(self.client.get('/api/payments/').data['count'], 0)  # money lists: where taken
        response = self.client.post(self._enrollment_url('join-batch/'), {'batch': self.ktm_batch.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('batch of Kathmandu', str(response.data))
        response = self.client.post(self._enrollment_url('join-batch/'), {'batch': self.pkr_batch.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        # The old branch no longer sees the student, but keeps its money.
        self.client.force_authenticate(self.ktm_manager)
        self.assertEqual(self.client.get(f'/api/students/{self.student.id}/').status_code, 404)
        self.assertEqual(self.client.get('/api/payments/').data['count'], 1)

    def test_history_lists_every_move(self):
        self._move(self.admin)
        self._move(self.admin, branch=self.ktm, reason='Came back')
        moves = self.client.get(f'/api/students/{self.student.id}/branch-moves/').data
        self.assertEqual(
            [(m['from_branch_name'], m['to_branch_name'], m['reason']) for m in moves],
            [('Pokhara', 'Kathmandu', 'Came back'), ('Kathmandu', 'Pokhara', 'Family moved to Pokhara')],
        )
        self.assertEqual(moves[0]['moved_by_name'], 'Admin')

    def test_who_can_move(self):
        self.assertEqual(self._move(self.ktm_desk).status_code, 403)  # can edit students, not move them
        self.assertEqual(self._move(self.pkr_manager, branch=self.ktm).status_code, 404)  # not their student
        self.student.refresh_from_db()
        self.assertEqual(self.student.branch, self.ktm)
        self.assertEqual(self._move(self.ktm_manager).status_code, 200)

    def test_move_refusals(self):
        self.assertEqual(self._move(self.admin, branch=self.ktm).status_code, 400)  # already there
        self.assertEqual(self._move(self.admin, reason='   ').status_code, 400)
        Branch.objects.filter(pk=self.pkr.pk).update(is_active=False)
        self.assertEqual(self._move(self.admin).status_code, 400)
        self.client.force_authenticate(self.admin)
        url = f'/api/students/{self.student.id}/move-branch/'
        self.assertEqual(self.client.post(url, {'branch': 9999, 'reason': 'x'}, format='json').status_code, 400)
        self.assertFalse(self.student.branch_moves.exists())

    def test_branch_changes_only_through_move(self):
        self.client.force_authenticate(self.admin)
        url = f'/api/students/{self.student.id}/'
        response = self.client.patch(url, {'branch': self.pkr.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Move branch', str(response.data))
        self.assertEqual(self.client.patch(url, {'full_name': 'Sita K'}, format='json').status_code, 200)
        response = self.client.patch(self._enrollment_url(), {'branch': self.pkr.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.branch, self.ktm)
        # New students: a branch user's go to their branch; staff must pick one.
        self.client.force_authenticate(self.ktm_desk)
        response = self.client.post('/api/students/', {
            'full_name': 'Ram', 'phone': '9800000991', 'branch': self.pkr.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Student.objects.get(pk=response.data['id']).branch, self.ktm)
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/students/', {'full_name': 'Hari', 'phone': '9800000992'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_seat_must_be_in_the_enrollments_branch(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._enrollment_url('move/'), {'batch': self.pkr_batch.id, 'reason': 'Closer'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('move the student to that branch first', str(response.data))

    def test_student_belongs_where_they_enrolled(self):
        from datetime import date

        from apps.lead.services import student_for_lead

        lead = Lead.objects.create(full_name='Gita', phone='9800000993', branch=self.ktm)
        enrollment = Enrollment.objects.create(
            lead=lead, course=self.ktm_batch.course, branch=self.pkr, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000,
        )
        student, created = student_for_lead(lead, enrollment=enrollment, user=self.admin)
        self.assertTrue(created)
        self.assertEqual(student.branch, self.pkr)

    def test_move_rows_cannot_be_changed(self):
        from django.core.exceptions import ValidationError

        self._move(self.admin)
        move = self.student.branch_moves.get()
        move.reason = 'Other'
        with self.assertRaises(ValidationError):
            move.save()
        with self.assertRaises(ValidationError):
            move.delete()

    def test_enrollment_batch_must_be_of_its_branch(self):
        from datetime import date

        self.client.force_authenticate(self.admin)
        lead = Lead.objects.create(full_name='Gita', phone='9800000994', branch=self.ktm)
        body = {
            'course': self.ktm_batch.course_id, 'enrollment_date': str(date.today()),
            'listed_fee': '30000', 'agreed_amount': '30000', 'batch': self.pkr_batch.id,
        }
        response = self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Enrollment.objects.get(pk=response.data['id']).branch, self.pkr)  # follows the batch
        body['branch'] = self.ktm.id
        response = self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Pick a batch of Kathmandu', str(response.data))

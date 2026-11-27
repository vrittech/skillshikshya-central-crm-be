"""Lead flows end to end, through the HTTP API, the way staff use them.

These tests drive real URLs as users with the default roles (account
migration 0010), across two branches. They cover flows that cross features
(a lead, its enquiries, calls, notes, follow-ups, documents and history),
the read and write rights of every lead address for every default role, the
branch walls, the public intake, and signing in.

A test marked expectedFailure shows a bug: the `# BUG:` line above it says what.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from botocore.exceptions import ClientError
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from apps.account.models import User
from apps.branch.models import Branch
from apps.course.models import Course
from apps.global_config.permissions import OPEN, STAFF
from apps.global_config.testing import give_rights
from apps.lead.enquiries import add_enquiry
from apps.lead.models import (
    Enrollment, FollowUp, Lead, LeadActivity, LeadCall, LeadCourseInterest, LeadDocument,
    LeadSource, LeadStatus, Notification, Payment, VoiceNote,
)

ROLES = ['Branch Manager', 'Counselor', 'Accountant', 'Front Desk', 'Trainer']


def ids(response):
    return [row['id'] for row in response.data['results']]


class FlowTestCase(APITestCase):
    """Two branches, an admin, a shared course, and helpers to make people and leads."""

    def setUp(self):
        cache.clear()  # sign in and the intake are rate limited
        self.addCleanup(cache.clear)
        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(
            email='admin@flow.test', name='Admin', password='pass', is_staff=True,
        )
        self.python = Course.objects.create(name='Python', full_amount=30000)
        self.java = Course.objects.create(name='Java', full_amount=20000)
        self._people = 0
        self._numbers = 0

    def _user(self, role=None, branch='ktm', **extra):
        """A user in a branch ('ktm' or 'pkr'), with a default role."""
        self._people += 1
        user = User.objects.create_user(
            email=f'user{self._people}@flow.test',
            name=extra.pop('name', f'{role or "User"} {self._people}'),
            password='pass', branch=getattr(self, branch) if branch else None, **extra,
        )
        if role:
            user.groups.add(Group.objects.get(name=role))
        return user

    def _as(self, user):
        """Act as this user (a fresh copy, so rights given since are seen)."""
        user = User.objects.get(pk=user.pk)
        self.client.force_authenticate(user)
        return user

    def _phone(self):
        self._numbers += 1
        return f'98{self._numbers:08d}'

    def _lead(self, branch, **fields):
        fields.setdefault('full_name', 'Lead')
        fields.setdefault('phone', self._phone())
        return Lead.objects.create(branch=branch, **fields)

    def _now(self):
        return timezone.now().replace(microsecond=0)


class LeadWorkFlowTests(FlowTestCase):
    """A counselor works a new lead; every step shows in the lead's history."""

    def test_counselor_works_a_new_lead_from_first_call_to_a_done_follow_up(self):
        new = LeadStatus.objects.create(name='New', sort_order=1)
        contacted = LeadStatus.objects.create(name='Contacted', sort_order=2)
        facebook = LeadSource.objects.create(name='Facebook Ads')
        me = self._as(self._user('Counselor', name='Mina'))
        sita = self._user('Counselor', name='Sita')

        response = self.client.post('/api/leads/', {
            'full_name': 'Hari Gurung', 'phone': '980-111-0001', 'branch': self.ktm.id,
            'primary_course': self.python.id, 'status': new.id, 'source': facebook.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        base = f'/api/leads/{response.data["id"]}/'
        enquiry = LeadCourseInterest.objects.get(lead_id=response.data['id'])
        self.assertEqual((enquiry.is_primary, enquiry.branch, enquiry.source), (True, self.ktm, facebook))

        self.assertEqual(self.client.patch(base, {'status': contacted.id}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(base, {'assigned_counselor': sita.id}, format='json').status_code, 200)
        self.assertTrue(Notification.objects.filter(recipient=sita, link=f'/leads/{response.data["id"]}').exists())

        now = self._now()
        call = self.client.post(f'{base}calls/', {
            'call_timestamp': now.isoformat(), 'caller': 'Mina', 'phone_number': '9801110001',
            'outcome': 'Interested',
        }, format='json')
        self.assertEqual(call.status_code, 201, call.data)
        note = self.client.post(f'{base}activities/', {
            'activity_type': 'note', 'title': 'Asked about fees', 'activity_at': now.isoformat(),
        }, format='json')
        self.assertEqual(note.status_code, 201, note.data)
        stage = self.client.post(f'{base}course-interests/{enquiry.id}/stage/', {'stage': 'contacted'}, format='json')
        self.assertEqual(stage.status_code, 200, stage.data)

        followup = self.client.post(f'{base}followups/', {
            'scheduled_at': (now + timedelta(days=1)).isoformat(), 'assigned_to': me.id,
            'purpose': 'Send the schedule',
        }, format='json')
        self.assertEqual(followup.status_code, 201, followup.data)
        self.assertEqual(self.client.get(base).data['next_followup']['id'], followup.data['id'])
        done = self.client.patch(f'{base}followups/{followup.data["id"]}/', {'status': 'completed'}, format='json')
        self.assertEqual(done.status_code, 200, done.data)
        document = self.client.post(f'{base}documents/', {
            'document_type': 'Citizenship', 'file': 'https://files.example.com/hari.pdf',
        }, format='json')
        self.assertEqual(document.status_code, 201, document.data)

        lead = self.client.get(base).data
        self.assertIsNone(lead['next_followup'])
        self.assertEqual(lead['call_count'], 1)
        self.assertEqual(
            (lead['branch'], lead['assigned_counselor'], lead['status']), (self.ktm.id, sita.id, contacted.id),
        )
        self.assertIsNotNone(lead['last_contact_at'])

        items = self.client.get(f'{base}timeline/').data['results']
        times = [i['at'] for i in items]
        self.assertEqual(times, sorted(times, reverse=True))
        titles = {i['title'] for i in items}
        for title in (
            'Lead added', 'Status changed', 'Counselor changed', 'Enquiry added', 'Enquiry contacted',
            'Outbound call', 'Asked about fees', 'Follow-up set', 'Follow-up done', 'Citizenship',
        ):
            self.assertIn(title, titles)
        changed = next(i for i in items if i['title'] == 'Counselor changed')
        self.assertEqual((changed['after'], changed['by']), ({'Counselor': 'Sita'}, 'Mina'))


class RoleRouteWalkTests(FlowTestCase):
    """Every lead address, for every default role: the right decides, nothing else."""

    def setUp(self):
        super().setUp()
        self.lead = self._lead(self.ktm, full_name='Walk Lead')
        self.enquiry = add_enquiry(self.lead, course=self.python, user=self.admin, branch=self.ktm)
        self.followup = FollowUp.objects.create(
            lead=self.lead, scheduled_at=timezone.now() + timedelta(days=1), branch=self.ktm,
        )
        self.call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=timezone.now(), caller='Admin', phone_number=self.lead.phone,
            branch=self.ktm,
        )
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=self.python, enrollment_date=date.today(), listed_fee=30000,
            agreed_amount=30000, branch=self.ktm,
        )

    def _reads(self):
        lead = f'/api/leads/{self.lead.id}'
        enrollment = f'{lead}/enrollments/{self.enrollment.id}'
        return [
            ('/api/leads/', 'lead.view_lead'),
            (f'{lead}/', 'lead.view_lead'),
            (f'{lead}/timeline/', 'lead.view_lead'),
            ('/api/leads/summary/', 'lead.view_lead'),
            (f'/api/leads/phone-match/?phone={self.lead.phone}', 'lead.view_lead'),
            ('/api/leads/export_csv/', 'lead.view_lead'),
            ('/api/leads/import-template/', 'lead.view_lead'),
            ('/api/leads/recycle_bin/', 'lead.delete_lead'),
            (f'{lead}/calls/', 'lead.view_leadcall'),
            (f'{lead}/calls/{self.call.id}/', 'lead.view_leadcall'),
            (f'{lead}/activities/', 'lead.view_leadactivity'),
            (f'{lead}/followups/', 'lead.view_followup'),
            (f'{lead}/followups/{self.followup.id}/', 'lead.view_followup'),
            (f'{lead}/documents/', 'lead.view_leaddocument'),
            (f'{lead}/voice-notes/', 'lead.view_voicenote'),
            (f'{lead}/course-interests/', 'lead.view_leadcourseinterest'),
            (f'{lead}/course-interests/{self.enquiry.id}/history/', 'lead.view_leadcourseinterest'),
            (f'{lead}/enrollments/', 'lead.view_enrollment'),
            (f'{enrollment}/', 'lead.view_enrollment'),
            (f'{enrollment}/history/', 'lead.view_enrollment'),
            (f'{enrollment}/reminders/', 'lead.view_enrollment'),
            (f'{enrollment}/payments/', 'lead.view_payment'),
            (f'{enrollment}/adjustments/', 'lead.view_adjustment'),
            ('/api/followups/summary/', 'lead.view_followup'),
            ('/api/calls/summary/', 'lead.view_leadcall'),
            ('/api/payments/summary/', 'lead.view_payment'),
            ('/api/users/export_csv/', 'account.view_user'),
            ('/api/users/me/', OPEN),
            ('/api/groups/', OPEN),
            ('/api/notifications/', OPEN),
            ('/api/lead-sources/', OPEN),
            ('/api/permissions/', STAFF),
        ]

    def _writes(self):
        # Rights are checked before the record is looked up or the body read:
        # with the right, a missing id answers 404 and an empty body 400.
        lead = f'/api/leads/{self.lead.id}'
        gone = '/api/leads/999999'
        return [
            ('post', '/api/leads/', 'lead.add_lead'),
            ('patch', f'{gone}/', 'lead.change_lead'),
            ('delete', f'{gone}/', 'lead.delete_lead'),
            ('post', f'{gone}/archive/', 'lead.change_lead'),
            ('post', f'{gone}/restore/', 'lead.change_lead'),
            ('post', f'{gone}/purge/', STAFF),
            ('post', '/api/leads/import/preview/', 'lead.add_lead'),
            ('post', f'{lead}/calls/', 'lead.add_leadcall'),
            ('patch', f'{lead}/calls/999999/', 'lead.change_leadcall'),
            ('post', f'{lead}/activities/', 'lead.add_leadactivity'),
            ('patch', f'{lead}/activities/999999/', 'lead.change_leadactivity'),
            ('post', f'{lead}/followups/', 'lead.add_followup'),
            ('patch', f'{lead}/followups/999999/', 'lead.change_followup'),
            ('delete', f'{lead}/followups/999999/', 'lead.delete_followup'),
            ('post', f'{lead}/documents/', 'lead.add_leaddocument'),
            ('delete', f'{lead}/documents/999999/', 'lead.delete_leaddocument'),
            ('post', f'{gone}/course-interests/', 'lead.add_leadcourseinterest'),
            ('patch', f'{lead}/course-interests/999999/', 'lead.change_leadcourseinterest'),
            ('post', f'{lead}/course-interests/999999/stage/', 'lead.change_leadcourseinterest'),
            ('post', f'{lead}/course-interests/999999/lose/', 'lead.change_leadcourseinterest'),
            ('post', f'{lead}/voice-notes/', 'lead.change_leadcourseinterest'),
        ]

    @staticmethod
    def _allowed(user, right):
        if right == OPEN:
            return True
        if right == STAFF:
            return False
        return user.has_perm(right)

    def test_each_default_role_reads_each_lead_address_by_its_view_right(self):
        for role in ROLES:
            user = self._as(self._user(role))
            for url, right in self._reads():
                with self.subTest(role=role, url=url):
                    code = self.client.get(url).status_code
                    self.assertEqual(code, 200 if self._allowed(user, right) else 403, right)

    def test_each_default_role_writes_each_lead_address_by_its_right(self):
        for role in ROLES:
            user = self._as(self._user(role))
            for method, url, right in self._writes():
                with self.subTest(role=role, method=method, url=url):
                    code = getattr(self.client, method)(url, {}, format='multipart').status_code
                    self.assertEqual(code == 403, not self._allowed(user, right), f'{right}: {code}')


class TimelineRightsTests(FlowTestCase):
    """History shows only the kinds of entry the user can read."""

    def setUp(self):
        super().setUp()
        counselor = self._user('Counselor')
        self.lead = self._lead(self.ktm, full_name='Full History')
        now = timezone.now()
        enquiry = add_enquiry(self.lead, course=self.python, user=counselor)
        LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, caller='C', phone_number=self.lead.phone, branch=self.ktm,
        )
        LeadActivity.objects.create(
            lead=self.lead, activity_type='note', title='A note', activity_at=now, branch=self.ktm,
        )
        FollowUp.objects.create(lead=self.lead, scheduled_at=now + timedelta(days=1), branch=self.ktm)
        LeadDocument.objects.create(
            lead=self.lead, document_type='ID', file='https://files.example.com/id.pdf', branch=self.ktm,
        )
        VoiceNote.objects.create(
            lead=self.lead, enquiry=enquiry, file_key='voice-notes/t.webm', content_type='audio/webm',
            size_bytes=1, branch=self.ktm, created_by=counselor,
        )
        enrollment = Enrollment.objects.create(
            lead=self.lead, course=self.python, enrollment_date=date.today(), listed_fee=30000,
            agreed_amount=30000, status='confirmed', branch=self.ktm,
        )
        Payment.objects.create(
            enrollment=enrollment, amount=Decimal('5000'), payment_date=now, payment_method='cash',
            branch=self.ktm,
        )

    def _kinds(self, role, **params):
        self._as(self._user(role))
        response = self.client.get(f'/api/leads/{self.lead.id}/timeline/', params)
        self.assertEqual(response.status_code, 200)
        return {i['kind'] for i in response.data['results']}

    def test_branch_manager_sees_every_kind(self):
        self.assertEqual(self._kinds('Branch Manager'), {
            'lead', 'enquiry', 'call', 'note', 'follow_up', 'voice_note', 'enrollment', 'money', 'document',
        })

    def test_front_desk_sees_contact_work_but_no_money_documents_or_recordings(self):
        self.assertEqual(self._kinds('Front Desk'), {'lead', 'enquiry', 'call', 'note', 'follow_up'})
        self.assertEqual(self._kinds('Front Desk', kind='money'), set())

    def test_accountant_sees_money_but_no_contact_work(self):
        self.assertEqual(self._kinds('Accountant'), {'lead', 'enrollment', 'money'})


class BranchWallTests(FlowTestCase):
    """A branch user reads, adds and changes only their branch's records."""

    def test_new_lead_from_a_branch_user_belongs_to_their_branch(self):
        self._as(self._user('Counselor'))
        response = self.client.post('/api/leads/', {'full_name': 'Walk In', 'phone': self._phone()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Lead.objects.get(pk=response.data['id']).branch, self.ktm)
        self.assertEqual(self.client.get(f'/api/leads/{response.data["id"]}/').status_code, 200)

    def test_branch_user_cannot_add_a_lead_to_another_branch(self):
        self._as(self._user('Counselor'))
        self.client.post('/api/leads/', {
            'full_name': 'Planted', 'phone': self._phone(), 'branch': self.pkr.id,
        }, format='json')
        self.assertFalse(Lead.objects.filter(branch=self.pkr).exists())

    def test_branch_user_cannot_move_a_lead_to_another_branch(self):
        lead = self._lead(self.ktm)
        self._as(self._user('Counselor'))
        self.client.patch(f'/api/leads/{lead.id}/', {'branch': self.pkr.id}, format='json')
        lead.refresh_from_db()
        self.assertEqual(lead.branch, self.ktm)

    def test_lead_counselor_must_work_in_the_lead_branch(self):
        pkr_counselor = self._user('Counselor', branch='pkr')
        self._as(self._user('Counselor'))
        response = self.client.post('/api/leads/', {
            'full_name': 'Ram', 'phone': self._phone(), 'branch': self.ktm.id,
            'assigned_counselor': pkr_counselor.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_lead_counselor_must_be_an_active_user(self):
        gone = self._user('Counselor', is_active=False)
        self._as(self._user('Counselor'))
        response = self.client.post('/api/leads/', {
            'full_name': 'Ram', 'phone': self._phone(), 'branch': self.ktm.id, 'assigned_counselor': gone.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_follow_up_cannot_go_to_a_counselor_of_another_branch(self):
        lead = self._lead(self.ktm)
        pkr_counselor = self._user('Counselor', branch='pkr')
        self._as(self._user('Counselor'))
        response = self.client.post(f'/api/leads/{lead.id}/followups/', {
            'scheduled_at': (self._now() + timedelta(days=1)).isoformat(), 'assigned_to': pkr_counselor.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_follow_up_stays_in_its_branch_when_a_branch_user_edits_it(self):
        lead = self._lead(self.ktm)
        self._as(self._user('Counselor'))
        created = self.client.post(f'/api/leads/{lead.id}/followups/', {
            'scheduled_at': (self._now() + timedelta(days=1)).isoformat(),
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        url = f'/api/leads/{lead.id}/followups/{created.data["id"]}/'
        self.client.patch(url, {'branch': self.pkr.id}, format='json')
        self.assertEqual(FollowUp.objects.get(pk=created.data['id']).branch, self.ktm)

    def test_note_author_stays_who_wrote_it(self):
        lead = self._lead(self.ktm)
        manager = self._as(self._user('Branch Manager'))
        someone_else = self._user('Counselor')
        note = self.client.post(f'/api/leads/{lead.id}/activities/', {
            'activity_type': 'note', 'title': 'Promised a discount', 'activity_at': self._now().isoformat(),
        }, format='json')
        self.assertEqual(note.status_code, 201, note.data)
        self.assertEqual(note.data['performed_by'], manager.id)
        self.client.patch(
            f'/api/leads/{lead.id}/activities/{note.data["id"]}/', {'performed_by': someone_else.id}, format='json',
        )
        self.assertEqual(LeadActivity.objects.get(pk=note.data['id']).performed_by_id, manager.id)

    def test_lead_detail_shows_only_own_branch_follow_up_and_calls(self):
        lead = self._lead(self.pkr, full_name='Shared Person')
        pkr_counselor = self._user('Counselor', branch='pkr', name='Pkr Counselor')
        add_enquiry(lead, course=self.java, user=pkr_counselor)
        FollowUp.objects.create(
            lead=lead, scheduled_at=timezone.now() + timedelta(days=1), purpose='Pokhara: offer 20% off',
            assigned_to=pkr_counselor, branch=self.pkr,
        )
        LeadCall.objects.create(
            lead=lead, call_timestamp=timezone.now(), caller='Pkr', phone_number=lead.phone, branch=self.pkr,
        )
        ktm_counselor = self._as(self._user('Counselor'))
        add_enquiry(lead, course=self.python, user=ktm_counselor)  # now Kathmandu sees the lead

        detail = self.client.get(f'/api/leads/{lead.id}/').data
        self.assertEqual(self.client.get(f'/api/leads/{lead.id}/followups/').data['results'], [])
        self.assertIsNone(detail['next_followup'])
        self.assertEqual(detail['call_count'], 0)

    def test_course_of_another_branch_cannot_be_picked(self):
        pkr_course = Course.objects.create(name='Pokhara Only', full_amount=10000, branch=self.pkr)
        self._as(self._user('Counselor'))
        listed = [c['id'] for c in self.client.get('/api/courses/', {'page_size': 100}).data['results']]
        self.assertNotIn(pkr_course.id, listed)
        response = self.client.post('/api/leads/', {
            'full_name': 'Ram', 'phone': self._phone(), 'branch': self.ktm.id, 'primary_course': pkr_course.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_new_lead_source_from_a_branch_user_belongs_to_their_branch(self):
        self._as(self._user('Counselor'))
        response = self.client.post('/api/lead-sources/', {'name': 'Kathmandu Fair'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(LeadSource.objects.get(pk=response.data['id']).branch, self.ktm)

    def test_branch_users_see_shared_and_own_lead_sources_only(self):
        LeadSource.objects.create(name='Shared Source')
        LeadSource.objects.create(name='Ktm Source', branch=self.ktm)
        LeadSource.objects.create(name='Pkr Source', branch=self.pkr)
        self._as(self._user('Counselor'))
        names = {s['name'] for s in self.client.get('/api/lead-sources/', {'page_size': 100}).data['results']}
        self.assertTrue({'Shared Source', 'Ktm Source'} <= names)
        self.assertNotIn('Pkr Source', names)

    def test_other_branch_records_on_a_shared_lead_are_not_found(self):
        lead = self._lead(self.pkr)
        pkr_counselor = self._user('Counselor', branch='pkr')
        pkr_enquiry = add_enquiry(lead, course=self.java, user=pkr_counselor)
        pkr_followup = FollowUp.objects.create(
            lead=lead, scheduled_at=timezone.now() + timedelta(days=1), branch=self.pkr, assigned_to=pkr_counselor,
        )
        pkr_call = LeadCall.objects.create(
            lead=lead, call_timestamp=timezone.now(), caller='Pkr', phone_number=lead.phone, branch=self.pkr,
        )
        manager = self._as(self._user('Branch Manager'))
        add_enquiry(lead, course=self.python, user=manager)
        base = f'/api/leads/{lead.id}'
        self.assertEqual(self.client.get(f'{base}/').status_code, 200)

        for method, url, body in [
            ('get', f'{base}/followups/{pkr_followup.id}/', None),
            ('patch', f'{base}/followups/{pkr_followup.id}/', {'status': 'completed'}),
            ('get', f'{base}/calls/{pkr_call.id}/', None),
            ('patch', f'{base}/course-interests/{pkr_enquiry.id}/', {'counselor': manager.id}),
            ('post', f'{base}/course-interests/{pkr_enquiry.id}/lose/', {'reason': 'timing'}),
            ('get', f'{base}/course-interests/{pkr_enquiry.id}/history/', None),
        ]:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, body, format='json').status_code, 404)
        pkr_followup.refresh_from_db()
        pkr_enquiry.refresh_from_db()
        self.assertEqual((pkr_followup.status, pkr_enquiry.stage), ('pending', 'new'))

    def test_export_leaves_out_another_branch_lead_asked_for_by_id(self):
        mine = self._lead(self.ktm, full_name='Mine Kathmandu')
        theirs = self._lead(self.pkr, full_name='Theirs Pokhara')
        self._as(self._user('Counselor'))
        response = self.client.get('/api/leads/export_csv/', {'ids': f'{mine.id},{theirs.id}'})
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn('Mine Kathmandu', text)
        self.assertNotIn('Theirs Pokhara', text)

    def test_staff_branch_pick_shows_what_that_branch_sees(self):
        ktm_user = self._user('Branch Manager')
        mine = self._lead(self.ktm)
        self._lead(self.pkr)
        shared = self._lead(self.pkr)
        add_enquiry(shared, course=self.python, user=ktm_user)
        for lead, branch in ((mine, self.ktm), (shared, self.pkr)):
            FollowUp.objects.create(lead=lead, scheduled_at=timezone.now() + timedelta(days=1), branch=branch)

        self._as(ktm_user)
        leads, followups = ids(self.client.get('/api/leads/')), ids(self.client.get('/api/followups/'))
        self._as(self.admin)
        picked = {'branch_id': self.ktm.id}
        self.assertEqual(sorted(leads), sorted([mine.id, shared.id]))
        self.assertEqual(sorted(ids(self.client.get('/api/leads/', picked))), sorted(leads))
        self.assertEqual(sorted(ids(self.client.get('/api/followups/', picked))), sorted(followups))


class RecycleBinFlowTests(FlowTestCase):
    """Delete to the recycle bin, restore, and purge: who may do which."""

    def test_branch_manager_bins_and_restores_but_only_staff_purge(self):
        lead = self._lead(self.ktm, full_name='Binned')
        manager, counselor = self._user('Branch Manager'), self._user('Counselor')

        self._as(manager)
        self.assertEqual(self.client.delete(f'/api/leads/{lead.id}/').status_code, 204)
        self.assertEqual(ids(self.client.get('/api/leads/recycle_bin/')), [lead.id])
        self.assertNotIn(lead.id, ids(self.client.get('/api/leads/')))
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/purge/').status_code, 403)

        self._as(counselor)
        self.assertEqual(self.client.get('/api/leads/recycle_bin/').status_code, 403)
        self.assertEqual(self.client.get(f'/api/leads/{lead.id}/').status_code, 404)

        self._as(manager)
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/restore/').status_code, 200)
        self.assertIn(lead.id, ids(self.client.get('/api/leads/')))

        self._as(self.admin)
        self.assertEqual(self.client.delete(f'/api/leads/{lead.id}/').status_code, 204)
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/purge/').status_code, 204)
        self.assertFalse(Lead._base_manager.filter(pk=lead.pk).exists())

    def test_archived_lead_comes_back_with_restore(self):
        lead = self._lead(self.ktm)
        self._as(self._user('Front Desk'))
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/archive/').status_code, 200)
        self.assertNotIn(lead.id, ids(self.client.get('/api/leads/')))
        self.assertIn(lead.id, ids(self.client.get('/api/leads/', {'include_archived': 'true'})))
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/restore/').status_code, 200)
        self.assertIn(lead.id, ids(self.client.get('/api/leads/')))

    def test_another_branch_neither_sees_nor_restores_a_binned_lead(self):
        lead = self._lead(self.pkr)
        Lead.objects.filter(pk=lead.pk).update(is_deleted=True, deleted_at=timezone.now())
        self._as(self._user('Branch Manager'))
        self.assertEqual(ids(self.client.get('/api/leads/recycle_bin/')), [])
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/restore/').status_code, 404)
        lead.refresh_from_db()
        self.assertTrue(lead.is_deleted)


class DuplicatePhoneTests(FlowTestCase):
    """A number already on a lead, in any branch, typed any way, stops a new lead."""

    def test_a_known_number_typed_another_way_is_refused(self):
        known = self._lead(self.pkr, phone='+977 980-123-4567', secondary_phone='01-4412345')
        self._as(self._user('Counselor'))
        for field, typed in [
            ('phone', '9801234567'), ('phone', '980 123 4567'), ('phone', '09801234567'),
            ('phone', '+977-9801234567'), ('secondary_phone', '00977 9801234567'), ('phone', '+977 1 4412345'),
        ]:
            body = {'full_name': 'Same Person', 'branch': self.ktm.id, 'phone': self._phone(), field: typed}
            with self.subTest(field=field, typed=typed):
                response = self.client.post('/api/leads/', body, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual([m['id'] for m in response.data['phone_match']], [known.id])
        self.assertEqual(Lead.objects.count(), 1)

        response = self.client.post('/api/leads/', {
            'full_name': 'Same Person', 'branch': self.ktm.id, 'phone': '9801234567', 'allow_duplicate_phone': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)


class BadInputTests(FlowTestCase):
    """Bad values are refused with a 400 that names the field."""

    def test_new_lead_refuses_missing_and_bad_fields(self):
        self._as(self._user('Counselor'))
        for field, body in [
            ('full_name', {'phone': self._phone()}),
            ('full_name', {'full_name': 'x' * 256}),
            ('email', {'full_name': 'A', 'email': 'not-an-email'}),
            ('phone', {'full_name': 'A', 'phone': '9' * 21}),
            ('primary_course', {'full_name': 'A', 'primary_course': 999999}),
            ('assigned_counselor', {'full_name': 'A', 'assigned_counselor': 999999}),
        ]:
            with self.subTest(field=field, body=body):
                response = self.client.post('/api/leads/', {'branch': self.ktm.id, **body}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.assertFalse(Lead.objects.exists())

    def test_expected_revenue_cannot_be_negative(self):
        self._as(self._user('Counselor'))
        response = self.client.post('/api/leads/', {
            'full_name': 'A', 'phone': self._phone(), 'branch': self.ktm.id, 'expected_revenue': '-5000',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_call_duration_cannot_be_negative(self):
        lead = self._lead(self.ktm)
        self._as(self._user('Counselor'))
        response = self.client.post(f'/api/leads/{lead.id}/calls/', {
            'call_timestamp': self._now().isoformat(), 'caller': 'Mina', 'phone_number': lead.phone,
            'duration_seconds': -600,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_document_can_hold_the_address_the_upload_gives(self):
        lead = self._lead(self.ktm)
        self._as(self._user('Counselor'))
        response = self.client.post(f'/api/leads/{lead.id}/documents/', {
            'document_type': 'ID', 'file': '/api/files/uploads/0f2c.pdf',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)


class FollowUpEdgeTests(FlowTestCase):
    """Completing, completing again and reopening a follow-up."""

    def setUp(self):
        super().setUp()
        self.lead = self._lead(self.ktm)
        self.url = f'/api/leads/{self.lead.id}/followups/'
        self.counselor = self._as(self._user('Counselor'))
        response = self.client.post(self.url, {
            'scheduled_at': (self._now() + timedelta(days=1)).isoformat(), 'assigned_to': self.counselor.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.followup = f'{self.url}{response.data["id"]}/'

    def _patch(self, body):
        response = self.client.patch(self.followup, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_completing_twice_keeps_the_first_done_time(self):
        first = self._patch({'status': 'completed'})['completed_at']
        self.lead.refresh_from_db()
        contacted = self.lead.last_contact_at
        self.assertEqual(self._patch({'status': 'completed', 'notes': 'Called again'})['completed_at'], first)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.last_contact_at, contacted)

    def test_a_reopened_follow_up_is_not_shown_as_done(self):
        self._patch({'status': 'completed'})
        self._patch({'status': 'pending'})
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.next_followup_at)
        titles = [i['title'] for i in self.client.get(f'/api/leads/{self.lead.id}/timeline/').data['results']]
        self.assertNotIn('Follow-up done', titles)


class ListParamTests(FlowTestCase):
    """Search, filter, ordering and paging params never break a list or cross a branch."""

    def test_odd_lead_list_params_answer_cleanly_and_stay_in_the_branch(self):
        self._lead(self.ktm, full_name='Ktm Lead')
        theirs = self._lead(self.pkr, full_name='Pkr Lead')
        self._as(self._user('Counselor'))
        self.client.raise_request_exception = False
        for params in [
            {'search': "' OR 1=1 --"}, {'search': '%'}, {'search': 'Pkr Lead'},
            {'ordering': 'assigned_counselor__password'}, {'ordering': '-lead_score,full_name,nope'},
            {'page_size': '100000'}, {'branch_id': self.pkr.id}, {'branch': self.pkr.id},
            {'branch__in': 'a,b'}, {'created_at__gte': 'yesterday'}, {'enrollment_status': 'bogus'},
            {'enrollment_status': 'pending', 'branch_id': self.pkr.id},
        ]:
            with self.subTest(params=params):
                response = self.client.get('/api/leads/', params)
                self.assertIn(response.status_code, (200, 400))
                if response.status_code == 200:
                    self.assertNotIn(theirs.id, ids(response))
                    self.assertLessEqual(response.data['page_size'], 100)
        for url in ('/api/followups/', '/api/followups/summary/', '/api/calls/summary/', '/api/leads/summary/'):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url, {'due': 'never', 'search': '%'}).status_code, (200, 400))

    def test_staff_bad_branch_pick_does_not_crash_lead_counts(self):
        self._as(self.admin)
        self.client.raise_request_exception = False
        self.assertEqual(self.client.get('/api/leads/summary/', {'branch_id': 'abc'}).status_code, 200)
        response = self.client.get('/api/leads/', {'enrollment_status': 'pending', 'branch_id': 'abc'})
        self.assertEqual(response.status_code, 200)


class StoredFileTests(FlowTestCase):
    """/api/files/: a document of another branch stays hidden."""

    class Bucket:
        def __init__(self, files):
            self.files = files

        def get_object(self, Bucket, Key):
            if Key not in self.files:
                raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            body = self.files[Key]
            return {'Body': mock.Mock(read=lambda: body), 'ContentType': 'application/pdf'}

    def setUp(self):
        super().setUp()
        bucket = self.Bucket({'uploads/pkr-id.pdf': b'pokhara citizenship'})
        for patcher in (
            mock.patch('apps.global_config.views.get_r2_client', return_value=bucket),
            mock.patch('apps.global_config.views.r2_config', return_value=mock.Mock(bucket_name='b')),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        pkr_lead = self._lead(self.pkr)
        LeadDocument.objects.create(
            lead=pkr_lead, document_type='ID', file='/api/files/uploads/pkr-id.pdf', branch=self.pkr,
        )

    def test_own_document_pointing_at_another_branch_file_does_not_unlock_it(self):
        ktm_lead = self._lead(self.ktm)
        self._as(self._user('Counselor'))
        self.assertEqual(self.client.get('/api/files/uploads/pkr-id.pdf').status_code, 404)
        added = self.client.post(f'/api/leads/{ktm_lead.id}/documents/', {
            'document_type': 'ID', 'file': 'https://anything.example.com/uploads/pkr-id.pdf',
        }, format='json')
        self.assertEqual(added.status_code, 201, added.data)
        self.assertEqual(self.client.get('/api/files/uploads/pkr-id.pdf').status_code, 404)


class IntakeFlowTests(APITestCase):
    """The public intake: token rules, repeat submissions and bad values."""

    URL = '/api/public/lead-intake/'

    def setUp(self):
        from apps.course import matching

        cache.clear()
        self.addCleanup(cache.clear)
        before = matching._CACHE.get('public', 'unset')
        matching._CACHE['public'] = []  # no public course catalog (and no network) in tests

        def restore():
            if before == 'unset':
                matching._CACHE.pop('public', None)
            else:
                matching._CACHE['public'] = before

        self.addCleanup(restore)
        Course.objects.create(name='Python', full_amount=30000)

    def _post(self, token='t', format='json', **body):
        if token:
            self.client.credentials(HTTP_X_INTAKE_TOKEN=token)
        return self.client.post(self.URL, {'name': 'Web Visitor', 'phone_number': '9800000555', **body}, format=format)

    @override_settings(DEBUG=True, LEAD_INTAKE_TOKEN='')
    def test_open_without_a_token_only_while_debug_is_on(self):
        self.assertEqual(self._post(token=None).status_code, 201)

    @override_settings(DEBUG=True, LEAD_INTAKE_TOKEN='t')
    def test_a_set_token_is_needed_even_while_debug_is_on(self):
        self.assertEqual(self._post(token=None).status_code, 403)
        self.assertEqual(self._post(token='T').status_code, 403)
        self.assertEqual(self._post(token='t').status_code, 201)

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_a_repeat_submission_adds_to_the_same_lead_and_staff_see_it(self):
        first = self._post(course_interests='Python')
        again = self._post(phone_number='+977 980-000-0555', course_interests='Python')
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.data['id'], first.data['id'])
        lead = Lead.objects.get()
        self.assertEqual(lead.course_interests.count(), 1)
        self.assertEqual(lead.primary_course.name, 'Python')
        admin = User.objects.create_user(email='intake@flow.test', name='A', password='pass', is_staff=True)
        self.client.credentials()
        self.client.force_authenticate(admin)
        self.assertEqual(ids(self.client.get('/api/leads/')), [lead.id])

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_a_repeat_submission_adds_no_second_enquiry_for_a_course_name_kept_as_text(self):
        self.assertEqual(self._post(course_interests='Underwater Basket Weaving').status_code, 201)
        self.assertEqual(self._post(course_interests='Underwater Basket Weaving').status_code, 200)
        self.assertEqual(Lead.objects.get().course_interests.count(), 1)

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_a_too_long_phone_is_refused_not_crashed(self):
        self.client.raise_request_exception = False
        response = self._post(phone_number='9800000556 call after 5pm please')
        self.assertEqual(response.status_code, 400)

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_has_laptop_false_from_a_form_is_saved_as_false(self):
        response = self._post(format='multipart', has_laptop='false')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIs(Lead.objects.get().custom_data['has_laptop'], False)


class SignInFlowTests(FlowTestCase):
    """Cookies, deactivated users and the sign-in rate limit."""

    def _sign_in(self, client, email, password='pass', **extra):
        return client.post('/api/auth/login/', {'email': email, 'password': password}, format='json', **extra)

    def test_sign_in_sets_http_only_cookies_and_me_shows_role_rights(self):
        user = self._user('Front Desk')
        response = self._sign_in(self.client, user.email)
        self.assertEqual(response.status_code, 200, response.data)
        for name in ('access_token', 'refresh_token'):
            self.assertTrue(response.cookies[name]['httponly'], name)
        me = self.client.get('/api/users/me/').data
        self.assertEqual((me['id'], me['branch'], me['is_staff']), (user.id, self.ktm.id, False))
        self.assertIn('lead.add_lead', me['user_permissions'])
        self.assertNotIn('lead.delete_lead', me['user_permissions'])

    def test_a_deactivated_user_is_signed_out_at_once(self):
        user = self._user('Counselor')
        self.assertEqual(self._sign_in(self.client, user.email).status_code, 200)
        admin = APIClient()
        admin.force_authenticate(self.admin)
        self.assertEqual(admin.patch(f'/api/users/{user.id}/', {'is_active': False}, format='json').status_code, 200)

        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)
        self.assertEqual(self.client.get('/api/leads/').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/refresh/', {}, format='json').status_code, 401)
        self.assertEqual(self._sign_in(APIClient(), user.email).status_code, 400)

    def test_sign_in_limit_counts_one_email_however_it_is_typed(self):
        user = self._user('Counselor')
        with mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', {'login': '2/minute'}):
            codes = [
                self._sign_in(self.client, typed, 'wrong').status_code
                for typed in (user.email, user.email.upper(), f'  {user.email}  ')
            ]
        self.assertEqual(codes, [400, 400, 429])

    def test_sign_in_limit_is_per_address(self):
        user = self._user('Counselor')
        with mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', {'login': '2/minute'}):
            for _ in range(2):
                self._sign_in(self.client, user.email, 'wrong', REMOTE_ADDR='10.0.0.1')
            blocked = self._sign_in(APIClient(), user.email, REMOTE_ADDR='10.0.0.1').status_code
            other = self._sign_in(APIClient(), user.email, REMOTE_ADDR='10.0.0.2').status_code
        self.assertEqual((blocked, other), (429, 200))


class UserAdminTests(FlowTestCase):
    """Editing users can't hand out admin rights or reach admins."""

    def test_user_edits_cannot_make_staff_or_superusers(self):
        manager = give_rights(self._user(), 'account.view_user', 'account.add_user', 'account.change_user')
        clerk = self._user('Front Desk')
        self._as(manager)
        response = self.client.patch(f'/api/users/{clerk.id}/', {
            'name': 'Clerk Two', 'is_staff': True, 'is_superuser': True,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        clerk.refresh_from_db()
        self.assertEqual((clerk.name, clerk.is_staff, clerk.is_superuser), ('Clerk Two', False, False))

        response = self.client.post('/api/auth/register/', {
            'email': 'new@flow.test', 'name': 'New', 'password': 'Secret123!',
            'is_staff': True, 'is_superuser': True, 'branch': self.ktm.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        new = User.objects.get(email='new@flow.test')
        self.assertEqual((new.is_staff, new.is_superuser, new.branch), (False, False, self.ktm))

    def test_branch_user_list_shows_only_their_branch(self):
        manager = self._user('Branch Manager')
        pkr_user = self._user('Counselor', branch='pkr')
        self._as(manager)
        listed = ids(self.client.get('/api/users/', {'page_size': 100}))
        self.assertIn(manager.id, listed)
        self.assertNotIn(pkr_user.id, listed)
        self.assertNotIn(self.admin.id, listed)
        self.assertEqual(self.client.get(f'/api/users/{pkr_user.id}/').status_code, 404)

    def test_branch_user_cannot_delete_a_superuser(self):
        manager = give_rights(self._user(), 'account.view_user', 'account.delete_user')
        boss = User.objects.create_superuser(email='boss@flow.test', name='Boss', password='pass', branch=self.ktm)
        self._as(manager)
        self.client.delete(f'/api/users/{boss.id}/')
        self.assertTrue(User.objects.filter(pk=boss.pk).exists())

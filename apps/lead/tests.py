from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Course
from apps.lead.models import Lead

User = get_user_model()


class PrimaryCourseNestedTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python Advanced', full_amount=30000)
        self.lead = Lead.objects.create(
            full_name='Test Lead', phone='9800000055', primary_course=self.course,
        )

    def test_detail_returns_nested_primary_course(self):
        response = self.client.get(f'/api/leads/{self.lead.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pc = response.data['primary_course']
        self.assertEqual(pc, {'id': self.course.id, 'name': 'Python Advanced', 'price': '30000.00'})
        self.assertNotIn('primary_course_name', response.data)

    def test_null_primary_course(self):
        lead = Lead.objects.create(full_name='No Course', phone='9800000056')
        response = self.client.get(f'/api/leads/{lead.id}/')
        self.assertIsNone(response.data['primary_course'])

    def test_patch_accepts_nested_object(self):
        other = Course.objects.create(name='Flutter', full_amount=25000)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': {'id': other.id}}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course']['id'], other.id)

    def test_patch_still_accepts_bare_int(self):
        bare = Course.objects.create(name='DevOps', full_amount=None)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': bare.id}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course'], {
            'id': bare.id, 'name': 'DevOps', 'price': None,
        })

    def test_patch_invalid_course_rejected(self):
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': 99999}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

class LeadSourceDeletionTests(APITestCase):
    def setUp(self):
        from apps.lead.models import LeadSource
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.protected = LeadSource.objects.create(
            name='Protected Source', is_protected=True,
        )
        self.used = LeadSource.objects.create(name='Used Source')
        Lead.objects.create(full_name='Someone', phone='1', source=self.used)
        self.free = LeadSource.objects.create(name='Free Source')

    def test_protected_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.protected.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('protected', response.data['detail'])
        self.assertTrue(
            __import__('apps.lead.models', fromlist=['LeadSource'])
            .LeadSource.objects.filter(pk=self.protected.id).exists()
        )

    def test_used_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.used.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('used in leads', response.data['detail'])

    def test_unused_unprotected_source_deletes(self):
        response = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_is_protected_toggle_via_patch(self):
        response = self.client.patch(
            f'/api/lead-sources/{self.free.id}/',
            {'is_protected': True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_protected'])
        # now deletion must be blocked
        blocked = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)

    def test_model_level_delete_raises_protected_error(self):
        from django.db import transaction
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.protected.delete()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.used.delete()


class GlobalCallListTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from apps.branch.models import Branch
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='c9@test.com', name='Counselor Nine', password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Biratnagar', code='BRT')
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        from apps.student.models import Student
        self.student = Student.objects.create(
            full_name='Call Person', phone='9811111111', lead=self.lead,
        )
        now = tz.now()
        self.recent_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='outbound',
            caller='Counselor Nine', phone_number='9811111111',
            duration_seconds=270, outcome='interested',
            created_by=self.counselor, branch=self.branch,
        )
        self.old_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now - timedelta(days=10),
            direction='inbound', caller='Someone Else',
            phone_number='9822222222', duration_seconds=60,
            created_by=self.admin, branch=self.branch,
        )

    def test_list_returns_all_calls_newest_first(self):
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data['results']]
        self.assertEqual(ids, [self.recent_call.id, self.old_call.id])
        row = response.data['results'][0]
        self.assertEqual(row['lead_name'], 'Call Person')
        self.assertTrue(row['lead_reference_number'].startswith('LD-'))

    def test_filter_direction(self):
        response = self.client.get('/api/calls/?direction=inbound')
        self.assertEqual([r['id'] for r in response.data['results']], [self.old_call.id])

    def test_filter_created_by_user(self):
        response = self.client.get(f'/api/calls/?created_by={self.counselor.id}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def test_filter_by_student(self):
        response = self.client.get(f'/api/calls/?student={self.student.id}')
        self.assertEqual(response.data['count'], 2)

    def test_filter_date_range(self):
        from django.utils import timezone as tz
        today = tz.now().date().isoformat()
        response = self.client.get(f'/api/calls/?call_timestamp__gte={today}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def test_search(self):
        response = self.client.get('/api/calls/?search=Counselor Nine')
        self.assertEqual(response.data['count'], 1)
        search_lead = self.client.get('/api/calls/?search=Call Person')
        self.assertEqual(search_lead.data['count'], 2)

    def test_branch_scoping_for_non_staff(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_admin = User.objects.create_user(
            email='br@test.com', name='BR User', password='x',
            branch=other_branch,
        )
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=None)
        self.client.force_authenticate(other_admin)
        response = self.client.get('/api/calls/')
        self.assertNotIn(self.recent_call.id, [r['id'] for r in response.data['results']])



class GlobalCallBranchScopingTests(APITestCase):
    """US-6: branch privacy for /api/calls/."""

    def setUp(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        from django.utils import timezone as tz

        self.biratnagar = Branch.objects.create(name='Biratnagar', code='BRT')
        self.dharan = Branch.objects.create(name='Dharan', code='DHR')

        self.staff = User.objects.create_user(
            email='staff@test.com', name='Staff', password='pass', is_staff=True,
        )
        self.dharan_user = User.objects.create_user(
            email='dharan@test.com', name='Dharan User', password='pass',
            branch=self.dharan,
        )
        self.biratnagar_user = User.objects.create_user(
            email='birat@test.com', name='Birat User', password='pass',
            branch=self.biratnagar,
        )
        self.lead = Lead.objects.create(full_name='Branch Lead', phone='9833333333')
        now = tz.now()
        self.dharan_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='outbound',
            caller='Dharan User', phone_number='9833333333', duration_seconds=120,
            created_by=self.dharan_user, branch=self.dharan,
        )
        self.biratnagar_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='inbound',
            caller='Birat User', phone_number='9844444444', duration_seconds=60,
            created_by=self.biratnagar_user, branch=self.biratnagar,
        )

    def _ids(self, response):
        return [row['id'] for row in response.data['results']]

    def test_non_staff_sees_only_own_branch_calls(self):
        self.client.force_authenticate(self.dharan_user)
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), [self.dharan_call.id])

    def test_non_staff_cannot_override_branch_with_query_param(self):
        self.client.force_authenticate(self.dharan_user)
        response = self.client.get(f'/api/calls/?branch_id={self.biratnagar.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), [self.dharan_call.id])

    def test_staff_sees_all_branches_by_default(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get('/api/calls/')
        self.assertEqual(
            sorted(self._ids(response)),
            sorted([self.dharan_call.id, self.biratnagar_call.id]),
        )

    def test_staff_can_narrow_to_one_branch(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(f'/api/calls/?branch_id={self.biratnagar.id}')
        self.assertEqual(self._ids(response), [self.biratnagar_call.id])

    def test_non_staff_without_branch_currently_sees_all_calls(self):
        """Documents a known gap from the US-6 write-up. Not fixed in this PR."""
        no_branch = User.objects.create_user(
            email='nobranch@test.com', name='No Branch', password='pass',
        )
        self.client.force_authenticate(no_branch)
        response = self.client.get('/api/calls/')
        self.assertEqual(response.data['count'], 2)


class GlobalCallSummaryTests(APITestCase):
    """Tests for the /api/calls/summary/ endpoint."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.timezone = timezone
        self.LeadCall = LeadCall

        self.admin = User.objects.create_user(
            email='summary@test.com',
            name='Summary Admin',
            password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)

        self.branch = Branch.objects.create(
            name='Kathmandu',
            code='KTM',
        )

        self.lead = Lead.objects.create(
            full_name='Summary Lead',
            phone='9855555555',
        )

        now = timezone.now()

        # Two calls by Alice and one by Bob, all on the same day.
        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=now,
            direction='outbound',
            caller='Alice',
            phone_number='9855555555',
            duration_seconds=270,
            outcome='interested',
            created_by=self.admin,
            branch=self.branch,
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=now,
            direction='inbound',
            caller='Bob',
            phone_number='9855555555',
            duration_seconds=60,
            created_by=self.admin,
            branch=self.branch,
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=now,
            direction='outbound',
            caller='Alice',
            phone_number='9855555555',
            duration_seconds=0,
            created_by=self.admin,
            branch=self.branch,
        )

        # An older call to test date filtering and daily grouping.
        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=now - timedelta(days=10),
            direction='inbound',
            caller='Charlie',
            phone_number='9855555555',
            duration_seconds=30,
            created_by=self.admin,
            branch=self.branch,
        )

    def test_summary_returns_correct_totals(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 4)
        self.assertEqual(response.data['inbound_calls'], 2)
        self.assertEqual(response.data['outbound_calls'], 2)
        self.assertEqual(response.data['talk_seconds'], 360)
        self.assertEqual(response.data['avg_duration_seconds'], 90)
        self.assertEqual(response.data['connected_calls'], 3)
        self.assertEqual(response.data['connect_rate'], 75.0)

    def test_summary_groups_calls_by_day(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        daily_counts = [
            row['count'] for row in response.data['calls_per_day']
        ]

        self.assertEqual(sorted(daily_counts), [1, 3])

    def test_summary_returns_top_callers(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        top_callers = response.data['top_callers']

        self.assertEqual(top_callers[0]['caller'], 'Alice')
        self.assertEqual(top_callers[0]['count'], 2)


    def test_summary_resolves_numeric_caller_id_to_user_name(self):
        # Create a user whose ID will be stored in the caller field.
        caller_user = User.objects.create_user(
            email='caller@test.com',
            name='DevilGod',
            password='pass',
        )

        self.LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.timezone.now(),
            direction='outbound',
            caller=str(caller_user.pk),
            phone_number='9855555555',
            duration_seconds=120,
            outcome='interested',
            created_by=self.admin,
            branch=self.branch,
        )

        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        matching_caller = next(
            caller for caller in response.data['top_callers']
            if caller['caller_id'] == str(caller_user.pk)
        )

        self.assertEqual(matching_caller['caller'], 'DevilGod')
        self.assertEqual(matching_caller['count'], 1)
   
    def test_summary_respects_direction_filter(self):
        response = self.client.get(
            '/api/calls/summary/?direction=inbound'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound_calls'], 2)
        self.assertEqual(response.data['outbound_calls'], 0)

    def test_summary_respects_date_from_filter(self):
        from django.utils import timezone
        from apps.lead.filters import NEPAL_TZ

        today = timezone.now().astimezone(NEPAL_TZ).date()

        response = self.client.get(
            f'/api/calls/summary/?date_from={today.isoformat()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 3)

    def test_summary_handles_empty_results(self):
        response = self.client.get(
            '/api/calls/summary/?caller=Nobody'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['talk_seconds'], 0)
        self.assertEqual(response.data['avg_duration_seconds'], 0)
        self.assertEqual(response.data['connect_rate'], 0)
        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])

class GlobalCallSummaryEdgeTests(APITestCase):
    """Nepal-time buckets, end-of-day filters, branch scoping, query count."""

    def setUp(self):
        from apps.branch.models import Branch

        self.dharan = Branch.objects.create(name='Dharan', code='DHR')
        self.biratnagar = Branch.objects.create(name='Biratnagar', code='BRT')
        self.staff = User.objects.create_user(
            email='edge-staff@test.com', name='Edge Staff', password='pass', is_staff=True,
        )
        self.dharan_user = User.objects.create_user(
            email='edge-dharan@test.com', name='Edge Dharan', password='pass',
            branch=self.dharan,
        )
        self.lead = Lead.objects.create(full_name='Edge Lead', phone='9866666666')
        self.client.force_authenticate(self.staff)

    def _call(self, when, branch=None, seconds=60, direction='outbound'):
        from apps.lead.models import LeadCall
        return LeadCall.objects.create(
            lead=self.lead, call_timestamp=when, direction=direction,
            caller='Alice', phone_number='9866666666', duration_seconds=seconds,
            created_by=self.staff, branch=branch or self.dharan,
        )

    def test_call_after_midnight_npt_counts_on_the_nepal_day(self):
        from datetime import datetime
        from apps.lead.tasks import NEPAL_TZ

        # 01:00 NPT on the 15th is 19:15 UTC on the 14th.
        self._call(datetime(2026, 11, 15, 1, 0, tzinfo=NEPAL_TZ))
        response = self.client.get('/api/calls/summary/')
        days = {row['date']: row['count'] for row in response.data['calls_per_day']}
        self.assertEqual(days, {'2026-11-15': 1})

    def test_date_to_includes_the_whole_last_day(self):
        from datetime import datetime
        from apps.lead.tasks import NEPAL_TZ

        self._call(datetime(2026, 11, 13, 23, 59, 30, tzinfo=NEPAL_TZ))
        late = self._call(datetime(2026, 11, 14, 23, 59, 30, tzinfo=NEPAL_TZ))
        self._call(datetime(2026, 11, 15, 0, 0, 30, tzinfo=NEPAL_TZ))

        query = '?date_from=2026-11-14&date_to=2026-11-14'
        summary = self.client.get(f'/api/calls/summary/{query}')
        listing = self.client.get(f'/api/calls/{query}')
        self.assertEqual(summary.data['total_calls'], 1)
        self.assertEqual([r['id'] for r in listing.data['results']], [late.id])

    def test_summary_total_matches_list_count_for_same_filters(self):
        from datetime import datetime
        from apps.lead.tasks import NEPAL_TZ

        directions = ['outbound', 'inbound', 'outbound', 'inbound']
        for hour, direction in zip((1, 9, 15, 22), directions):
            self._call(datetime(2026, 11, 14, hour, 0, tzinfo=NEPAL_TZ), direction=direction)

        query = '?direction=outbound&date_from=2026-11-14&date_to=2026-11-14'
        summary = self.client.get(f'/api/calls/summary/{query}')
        listing = self.client.get(f'/api/calls/{query}')
        self.assertEqual(listing.data['count'], 2)
        self.assertEqual(summary.data['total_calls'], listing.data['count'])

    def test_non_staff_summary_only_counts_own_branch(self):
        from django.utils import timezone

        now = timezone.now()
        self._call(now, branch=self.dharan, seconds=120)
        self._call(now, branch=self.biratnagar, seconds=60)

        self.client.force_authenticate(self.dharan_user)
        for url in (
            '/api/calls/summary/',
            f'/api/calls/summary/?branch_id={self.biratnagar.id}',
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['total_calls'], 1)
            self.assertEqual(response.data['talk_seconds'], 120)

    def test_summary_query_count_is_fixed(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.utils import timezone

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get('/api/calls/summary/')
            self.assertEqual(response.status_code, 200)
            return len(ctx)

        now = timezone.now()
        for _ in range(2):
            self._call(now)
        few = count_queries()
        for _ in range(30):
            self._call(now)
        self.assertEqual(count_queries(), few)

class GlobalCallSearchTests(APITestCase):
    def test_search_finds_calls_by_logging_user_name(self):
        from django.utils import timezone
        from apps.lead.models import LeadCall

        sita = User.objects.create_user(
            email='sita@test.com', name='Sita Rai', password='pass', is_staff=True,
        )
        lead = Lead.objects.create(full_name='Search Lead', phone='9888888888')
        call = LeadCall.objects.create(
            lead=lead, call_timestamp=timezone.now(), direction='outbound',
            caller=str(sita.pk), phone_number='9888888888', duration_seconds=30,
            created_by=sita,
        )
        self.client.force_authenticate(sita)
        response = self.client.get('/api/calls/?search=Sita')
        self.assertEqual([r['id'] for r in response.data['results']], [call.id])
    
class GlobalCallExportTests(APITestCase):
    """US-4: CSV export at /api/calls/export/."""

    HEADER = [
        'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
        'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome',
        'Notes', 'Recording',
    ]

    def setUp(self):
        from apps.branch.models import Branch

        self.dharan = Branch.objects.create(name='Dharan', code='DHR')
        self.biratnagar = Branch.objects.create(name='Biratnagar', code='BRT')

        self.staff = User.objects.create_user(
            email='export-staff@test.com',
            name='Sita Rai',
            password='pass',
            is_staff=True,
        )

        self.dharan_user = User.objects.create_user(
            email='export-dharan@test.com',
            name='Export Dharan',
            password='pass',
            branch=self.dharan,
        )

        self.lead = Lead.objects.create(
            full_name='नेपाली नाम',
            phone='9877777777',
        )

        self.client.force_authenticate(self.staff)

    def _call(
        self,
        when=None,
        branch=None,
        seconds=60,
        direction='outbound',
        notes=None,
        caller='Alice',
    ):
        from django.utils import timezone
        from apps.lead.models import LeadCall

        return LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=when or timezone.now(),
            direction=direction,
            caller=caller,
            phone_number='9877777777',
            duration_seconds=seconds,
            notes=notes,
            created_by=self.staff,
            branch=branch or self.dharan,
        )

    def _rows(self, response):
        import csv
        import io

        return list(
            csv.reader(
                io.StringIO(response.content.decode('utf-8-sig'))
            )
        )

    def test_headers_filename_and_columns(self):
        self._call()

        response = self.client.get('/api/calls/export/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response['Content-Type'].startswith('text/csv')
        )
        self.assertRegex(
            response['Content-Disposition'],
            r'filename="calls-\d{4}-\d{2}-\d{2}\.csv"',
        )
        self.assertEqual(self._rows(response)[0], self.HEADER)

    def test_devanagari_names_survive_and_bom_is_present(self):
        self._call()

        response = self.client.get('/api/calls/export/')

        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(self._rows(response)[1][2], 'नेपाली नाम')

    def test_formula_like_notes_are_neutralised(self):
        for note in ['=1+1', '+1', '-2', '@SUM(A1)']:
            self._call(notes=note)

        cells = [
            row[10]
            for row in self._rows(
                self.client.get('/api/calls/export/')
            )[1:]
        ]

        self.assertEqual(len(cells), 4)

        for cell in cells:
            self.assertTrue(cell.startswith("'"), cell)

    def test_row_values_use_nepal_time_and_readable_names(self):
        from datetime import datetime
        from apps.lead.tasks import NEPAL_TZ

        self._call(
            when=datetime(2026, 11, 15, 1, 0, tzinfo=NEPAL_TZ),
            seconds=259,
            caller=str(self.staff.pk),
        )

        row = self._rows(
            self.client.get('/api/calls/export/')
        )[1]

        self.assertEqual(row[0], '2026-11-15 01:00:00')
        self.assertEqual(row[1], 'Outbound')
        self.assertEqual(row[5], 'Sita Rai')
        self.assertEqual(row[6], 'Sita Rai')
        self.assertEqual(row[7], '04:19')
        self.assertEqual(row[8], '259')

    def test_export_matches_list_for_same_filters(self):
        for direction in ('outbound', 'inbound', 'outbound'):
            self._call(direction=direction)

        query = '?direction=outbound'

        listing = self.client.get(f'/api/calls/{query}')
        export = self.client.get(f'/api/calls/export/{query}')

        self.assertEqual(listing.data['count'], 2)
        self.assertEqual(
            len(self._rows(export)) - 1,
            listing.data['count'],
        )

    def test_export_includes_every_page_of_results(self):
        for _ in range(25):
            self._call()

        listing = self.client.get('/api/calls/')
        export = self.client.get('/api/calls/export/')

        self.assertEqual(len(listing.data['results']), 10)
        self.assertEqual(len(self._rows(export)) - 1, 25)

    def test_non_staff_export_only_contains_own_branch(self):
        self._call(branch=self.dharan, notes='dharan-note')
        self._call(branch=self.biratnagar, notes='birat-note')

        self.client.force_authenticate(self.dharan_user)

        for url in (
            '/api/calls/export/',
            f'/api/calls/export/?branch_id={self.biratnagar.id}',
        ):
            response = self.client.get(url)

            self.assertEqual(response.status_code, 200)

            notes = [
                row[10]
                for row in self._rows(response)[1:]
            ]

            self.assertEqual(notes, ['dharan-note'])

    def test_export_query_count_is_fixed(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get('/api/calls/export/')

            self.assertEqual(response.status_code, 200)
            return len(ctx)

        for _ in range(2):
            self._call(caller=str(self.staff.pk))

        few = count_queries()

        for _ in range(40):
            self._call(caller=str(self.staff.pk))

        self.assertEqual(count_queries(), few)

    def test_leading_whitespace_formulas_are_neutralised(self):
        for note in [' =1+1', '\n=1+1']:
            self._call(notes=note)

        cells = [
            row[10]
            for row in self._rows(
                self.client.get('/api/calls/export/')
            )[1:]
        ]

        self.assertEqual(len(cells), 2)

        for cell in cells:
            self.assertTrue(cell.startswith("'"), cell)


    def test_real_phone_numbers_are_not_prefixed(self):
        from apps.lead.models import LeadCall

        call = self._call()
        LeadCall.objects.filter(pk=call.pk).update(
            phone_number='+9779800000000'
        )

        row = self._rows(
            self.client.get('/api/calls/export/')
        )[1]

        self.assertEqual(row[4], '+9779800000000')
        
class LeadCallBranchOnCreateTests(APITestCase):
    """A call logged through the API must be findable by its branch."""

    def test_logged_call_inherits_lead_branch_and_is_scoped(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        dharan = Branch.objects.create(name='Dharan', code='DHR')
        biratnagar = Branch.objects.create(name='Biratnagar', code='BRT')

        staff = User.objects.create_user(
            email='create-staff@test.com',
            name='Create Staff',
            password='pass',
            is_staff=True,
        )

        dharan_user = User.objects.create_user(
            email='create-dharan@test.com',
            name='Create Dharan',
            password='pass',
            branch=dharan,
        )

        birat_user = User.objects.create_user(
            email='create-birat@test.com',
            name='Create Birat',
            password='pass',
            branch=biratnagar,
        )

        lead = Lead.objects.create(
            full_name='Branch Lead',
            phone='9899999999',
            branch=dharan,
        )

        self.client.force_authenticate(staff)

        response = self.client.post(
            f'/api/leads/{lead.id}/calls/',
            {
                'call_timestamp': '2026-11-15T10:00:00Z',
                'direction': 'outbound',
                'caller': str(staff.pk),
                'phone_number': '9899999999',
                'duration_seconds': 0,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            LeadCall.objects.get(pk=response.data['id']).branch_id,
            dharan.id,
        )

        self.client.force_authenticate(dharan_user)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 1)

        self.client.force_authenticate(birat_user)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 0)
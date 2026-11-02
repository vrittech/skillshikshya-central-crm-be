from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.branch.models import Branch
from apps.callminutes.models import CallMinutesTransaction
from apps.lead.models import Lead, LeadCall

User = get_user_model()


class CallMinutesBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.admin.call_seconds_balance = 100 * 60  # 100 minutes
        self.admin.save()
        self.counselor = User.objects.create_user(
            email='c@test.com', name='Counselor', password='pass',
        )
        self.counselor.call_seconds_balance = 10 * 60  # 10 minutes
        self.counselor.save()
        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.lead = Lead.objects.create(
            full_name='Test Lead', phone='9800000009', branch=self.branch,
        )
        now = timezone.now().isoformat()
        self.call_payload = {
            'call_timestamp': now,
            'caller': 'Counselor',
            'phone_number': '9800000009',
            'duration_seconds': 90,
            'outcome': 'interested',
        }

    def _post_call(self, payload_overrides=None):
        payload = dict(self.call_payload)
        if payload_overrides:
            payload.update(payload_overrides)
        return self.client.post(
            f'/api/leads/{self.lead.id}/calls/', payload, format='json',
        )


class CallDeductionTests(CallMinutesBase):
    def setUp(self):
        super().setUp()
        add_perm = Permission.objects.get(
            codename='add_leadcall', content_type__app_label='lead',
        )
        change_perm = Permission.objects.get(
            codename='change_leadcall', content_type__app_label='lead',
        )
        self.counselor.user_permissions.add(add_perm, change_perm)
        self.client.force_authenticate(self.counselor)

    def test_call_deducts_exact_seconds(self):
        response = self._post_call()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['seconds_used'], 90)
        self.assertEqual(response.data['remaining_seconds'], 510)
        # backward-compatible floored minute fields
        self.assertEqual(response.data['minutes_used'], 1)
        self.assertEqual(response.data['remaining_minutes'], 8)

        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, 510)
        txn = CallMinutesTransaction.objects.get(call=response.data['id'])
        self.assertEqual(txn.amount, -90)
        self.assertEqual(txn.type, 'deduction')
        self.assertEqual(txn.balance_after, 510)

    def test_four_minutes_thirty_seconds_deducts_270(self):
        self.call_payload['duration_seconds'] = 270  # 4:30
        response = self._post_call()
        self.assertEqual(response.data['seconds_used'], 270)
        self.assertEqual(response.data['remaining_seconds'], 330)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, 330)

    def test_partial_minute_not_rounded_up(self):
        # 9 min 30 sec -> charges exactly 570s, not 600s
        self.counselor.call_seconds_balance = 100 * 60
        self.counselor.save()
        response = self._post_call({'duration_seconds': 570})
        self.assertEqual(response.data['seconds_used'], 570)
        self.assertEqual(response.data['remaining_seconds'], 100 * 60 - 570)
        self.assertEqual(response.data['minutes_used'], 9)  # floored display

    def test_zero_duration_call_costs_nothing_but_is_recorded(self):
        count_before = LeadCall.objects.count()
        response = self._post_call({'duration_seconds': 0})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['seconds_used'], 0)
        self.assertEqual(response.data['remaining_seconds'], 600)
        self.assertEqual(LeadCall.objects.count(), count_before + 1)
        self.assertFalse(
            CallMinutesTransaction.objects.filter(amount=0).exists(),
        )

    def test_sub_minute_call_charges_exact_seconds(self):
        response = self._post_call({'duration_seconds': 45})
        self.assertEqual(response.data['seconds_used'], 45)
        self.assertEqual(response.data['remaining_seconds'], 555)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, 555)

    def test_insufficient_balance_blocks_call(self):
        self.counselor.call_seconds_balance = 0
        self.counselor.save()
        count_before = LeadCall.objects.count()

        # a call longer than the balance is rejected...
        response = self._post_call({'duration_seconds': 3600})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Insufficient call minutes', str(response.data))
        self.assertEqual(LeadCall.objects.count(), count_before)

        # ...but an exact-balance call goes through
        ok = self._post_call({'duration_seconds': 0})
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CallMinutesTransaction.objects.count(), 0)

    def test_cannot_delete_call(self):
        # Even staff/superusers must not be able to delete call records.
        self.client.force_authenticate(self.admin)
        response = self._post_call()
        call_id = response.data['id']
        delete = self.client.delete(f'/api/leads/{self.lead.id}/calls/{call_id}/')
        self.assertEqual(delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(LeadCall.objects.filter(pk=call_id).exists())
        self.client.force_authenticate(self.counselor)

    def test_duration_frozen_after_create(self):
        response = self._post_call()
        call_id = response.data['id']
        patch = self.client.patch(
            f'/api/leads/{self.lead.id}/calls/{call_id}/',
            {'duration_seconds': 9999}, format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        call = LeadCall.objects.get(pk=call_id)
        self.assertEqual(call.duration_seconds, 90)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, 510)

    def test_inbound_call_does_not_deduct_balance(self):
        balance_before = self.counselor.call_seconds_balance
        response = self._post_call({'direction': 'inbound', 'duration_seconds': 180})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, balance_before)
        self.assertEqual(response.data['seconds_used'], 0)
        self.assertEqual(response.data['remaining_seconds'], balance_before)
        self.assertFalse(CallMinutesTransaction.objects.filter(call=response.data['id']).exists())

    def test_call_inherits_lead_branch(self):
        response = self._post_call()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        call = LeadCall.objects.get(pk=response.data['id'])
        self.assertEqual(call.branch, self.lead.branch)


class TopUpTests(CallMinutesBase):
    def setUp(self):
        super().setUp()

    def test_admin_can_top_up_in_minutes(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f'/api/users/{self.counselor.id}/call-minutes/add/',
            {'amount': 50, 'note': 'monthly quota'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['balance_minutes'], 60)   # 10 + 50
        self.assertEqual(response.data['balance_seconds'], 3600)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.call_seconds_balance, 3600)
        txn = CallMinutesTransaction.objects.get(user=self.counselor)
        self.assertEqual(txn.amount, 3000)  # stored in seconds
        self.assertEqual(txn.type, 'topup')
        self.assertEqual(txn.created_by, self.admin)

    def test_transaction_serializes_minutes_alongside_seconds(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            f'/api/users/{self.counselor.id}/call-minutes/add/',
            {'amount': 50, 'note': 'quota'}, format='json',
        )
        response = self.client.get(
            f'/api/users/{self.counselor.id}/call-minutes/transactions/'
        )
        row = response.data['results'][0]
        self.assertEqual(row['amount'], 3000)
        self.assertEqual(row['amount_minutes'], 50.0)
        self.assertEqual(row['balance_after'], 3600)
        self.assertEqual(row['balance_after_minutes'], 60.0)

    def test_non_staff_cannot_top_up(self):
        self.client.force_authenticate(self.counselor)
        response = self.client.post(
            f'/api/users/{self.counselor.id}/call-minutes/add/',
            {'amount': 100}, format='json',
        )
        self.assertIn(response.status_code, (403, 401))

    def test_invalid_amount_rejected(self):
        self.client.force_authenticate(self.admin)
        for bad in (0, -5, 'abc', None):
            response = self.client.post(
                f'/api/users/{self.counselor.id}/call-minutes/add/',
                {'amount': bad}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, msg=bad)

    def test_transactions_list_admin_only(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            f'/api/users/{self.counselor.id}/call-minutes/add/',
            {'amount': 50}, format='json',
        )
        response = self.client.get(
            f'/api/users/{self.counselor.id}/call-minutes/transactions/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)


class MyCallMinutesTests(CallMinutesBase):
    def test_counselor_sees_own_balance(self):
        self.client.force_authenticate(self.counselor)
        response = self.client.get('/api/users/me/call-minutes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['balance_seconds'], 600)
        self.assertEqual(response.data['balance_minutes'], 10)
        self.assertEqual(response.data['balance_display'], '10:00')

from django.core.exceptions import ValidationError
from apps.callminutes.utils import format_duration, parse_duration_to_seconds


class DurationParserTests(APITestCase):
    def test_mm_ss_colon(self):
        self.assertEqual(parse_duration_to_seconds('4:19'), 259)

    def test_dot_separator(self):
        self.assertEqual(parse_duration_to_seconds('4.19'), 259)

    def test_leading_zero(self):
        self.assertEqual(parse_duration_to_seconds('04:19'), 259)

    def test_mmss_suffix(self):
        self.assertEqual(parse_duration_to_seconds('4m19s'), 259)

    def test_raw_seconds(self):
        self.assertEqual(parse_duration_to_seconds('259'), 259)

    def test_hms(self):
        self.assertEqual(parse_duration_to_seconds('1:02:30'), 3750)

    def test_single_digit_after_dot_is_seconds(self):
        # "4.5" means 4 minutes 05 seconds (not decimal minutes)
        self.assertEqual(parse_duration_to_seconds('4.5'), 245)

    def test_empty_and_none(self):
        self.assertEqual(parse_duration_to_seconds(''), 0)
        self.assertEqual(parse_duration_to_seconds(None), 0)

    def test_invalid_raises(self):
        for bad in ('abc', '4.99.99', 'foo:bar'):
            with self.assertRaises(ValidationError, msg=bad):
                parse_duration_to_seconds(bad)

    def test_overflow_carries(self):
        # 7.89 => 7min 89sec => 8min 29sec = 509s
        self.assertEqual(parse_duration_to_seconds('7.89'), 509)
        self.assertEqual(parse_duration_to_seconds('7:89'), 509)
        self.assertEqual(parse_duration_to_seconds('4m89s'), 5 * 60 + 29)
        # 1:99:99 => 1h 99m 99s => carry -> 2h 40m 39s = 9639s
        self.assertEqual(parse_duration_to_seconds('1:99:99'), 9639)

    def test_format_duration(self):
        self.assertEqual(format_duration(259), '04:19')
        self.assertEqual(format_duration(0), '00:00')
        self.assertEqual(format_duration(90), '01:30')

    def test_minute_and_second_shorthand(self):
        self.assertEqual(parse_duration_to_seconds('4m'), 240)
        self.assertEqual(parse_duration_to_seconds('4min'), 240)
        self.assertEqual(parse_duration_to_seconds('4 mins'), 240)
        self.assertEqual(parse_duration_to_seconds('45s'), 45)
        self.assertEqual(parse_duration_to_seconds('45sec'), 45)


class FlexibleDurationAPITests(CallDeductionTests):
    def setUp(self):
        super().setUp()

    def _post_with_input(self, duration_input):
        payload = {
            'call_timestamp': timezone.now().isoformat(),
            'caller': 'Counselor',
            'phone_number': '9800000009',
            'duration_input': duration_input,
            'outcome': 'test',
        }
        return self.client.post(
            f'/api/leads/{self.lead.id}/calls/', payload, format='json',
        )

    def test_duration_input_4_19(self):
        self.counselor.call_seconds_balance = 100 * 60
        self.counselor.save()
        response = self._post_with_input('4:19')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['duration_seconds'], 259)
        self.assertEqual(response.data['duration_formatted'], '04:19')
        self.assertEqual(response.data['seconds_used'], 259)
        self.assertEqual(response.data['remaining_seconds'], 100 * 60 - 259)

    def test_duration_input_dot_and_leading_zero(self):
        self.counselor.call_seconds_balance = 100 * 60
        self.counselor.save()
        for fmt in ('4.19', '04:19', '4m19s'):
            response = self._post_with_input(fmt)
            self.assertEqual(response.status_code, 201, msg=fmt)
            self.assertEqual(response.data['duration_seconds'], 259, msg=fmt)

    def test_duration_input_shorthand(self):
        self.counselor.call_seconds_balance = 100 * 60
        self.counselor.save()
        response = self._post_with_input('4m')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['duration_seconds'], 240)

    def test_invalid_duration_input_400(self):
        response = self._post_with_input('not-a-duration')
        self.assertEqual(response.status_code, 400)
        self.assertIn('duration_input', response.data)

    def test_duration_seconds_still_accepted(self):
        response = self.client.post(
            f'/api/leads/{self.lead.id}/calls/', {
                'call_timestamp': timezone.now().isoformat(),
                'caller': 'Counselor',
                'phone_number': '9800000009',
                'duration_seconds': 270,
            }, format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['duration_seconds'], 270)

    def test_duration_frozen_via_input_on_patch(self):
        response = self._post_with_input('4:19')
        call_id = response.data['id']
        # a VALID new duration is still ignored on PATCH (frozen ledger)
        patch = self.client.patch(
            f'/api/leads/{self.lead.id}/calls/{call_id}/',
            {'duration_input': '9:09'}, format='json',
        )
        self.assertEqual(patch.status_code, 200)
        call = LeadCall.objects.get(pk=call_id)
        self.assertEqual(call.duration_seconds, 259)



class CallBalanceListTests(APITestCase):
    def setUp(self):
        from apps.callminutes.models import CallMinutesTransaction
        from apps.lead.models import LeadCall

        self.staff = User.objects.create_user(
            email='sb@test.com', name='Staff Boss', password='x', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='cb@test.com', name='Counselor Bal', password='x',
        )
        self.counselor.call_seconds_balance = 6000
        self.counselor.save()
        CallMinutesTransaction.objects.create(
            user=self.counselor, amount=6000, type='topup',
            balance_after=6000,
        )
        CallMinutesTransaction.objects.create(
            user=self.counselor, amount=-259, type='deduction',
            balance_after=5741,
        )
        self.counselor.call_seconds_balance = 5741
        self.counselor.save(update_fields=['call_seconds_balance'])

    def test_staff_can_list_all_active_users_with_nested(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get('/api/call-balance/')
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data['results'] if r['id'] == self.counselor.id)
        self.assertEqual(row['call_seconds_balance'], 5741)
        self.assertEqual(row['remaining_minutes'], 95)
        self.assertEqual(row['balance_display'], '95:41')
        self.assertEqual(len(row['recent_transactions']), 2)
        self.assertEqual(
            [t['type'] for t in row['recent_topups']], ['topup'],
        )

    def test_nested_topups_only_contain_topup_type(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get('/api/call-balance/')
        row = next(r for r in response.data['results'] if r['id'] == self.counselor.id)
        for t in row['recent_topups']:
            self.assertEqual(t['type'], 'topup')

    def test_inactive_users_excluded(self):
        self.counselor.is_active = False
        self.counselor.save(update_fields=['is_active'])
        self.client.force_authenticate(self.staff)
        response = self.client.get('/api/call-balance/')
        ids = [r['id'] for r in response.data['results']]
        self.assertNotIn(self.counselor.id, ids)

    def test_non_staff_denied(self):
        self.client.force_authenticate(self.counselor)
        response = self.client.get('/api/call-balance/')
        self.assertIn(response.status_code, (403, 401))

    def test_search_and_limit(self):
        from apps.callminutes.models import CallMinutesTransaction
        for i in range(12):
            CallMinutesTransaction.objects.create(
                user=self.counselor, amount=-1, type='deduction',
                balance_after=1,
            )
        self.client.force_authenticate(self.staff)
        response = self.client.get(
            f'/api/call-balance/?transactions_limit=3&search=Counselor'
        )
        row = next(r for r in response.data['results'] if r['id'] == self.counselor.id)
        self.assertLessEqual(len(row['recent_transactions']), 3)

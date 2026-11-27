from unittest import mock

from django.contrib.auth.models import Group, Permission
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.account.models import User


class MeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='me@test.com', name='Mina', password='pass')
        self.client.force_authenticate(self.user)

    def test_me_returns_the_signed_in_user(self):
        User.objects.create_user(email='other@test.com', name='Other', password='pass')
        response = self.client.get('/api/users/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user.id)
        self.assertEqual(response.data['user_permissions'], [])

    def test_me_includes_permissions_from_groups(self):
        group = Group.objects.create(name='Test role')
        group.permissions.add(Permission.objects.get(codename='add_lead'))
        self.user.groups.add(group)
        response = self.client.get('/api/users/me/')
        self.assertEqual(response.data['user_permissions'], ['lead.add_lead'])
        self.assertEqual(response.data['groups'], [group.id])

    def test_me_needs_sign_in(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)


class DefaultRolesTests(APITestCase):
    def test_default_roles_exist_with_permissions(self):
        roles = {g.name: g for g in Group.objects.prefetch_related('permissions')}
        for name in ['Branch Manager', 'Counselor', 'Accountant', 'Front Desk', 'Trainer']:
            self.assertIn(name, roles)
            self.assertTrue(roles[name].permissions.exists(), name)
        counselor = {p.codename for p in roles['Counselor'].permissions.all()}
        self.assertIn('add_lead', counselor)
        self.assertNotIn('delete_lead', counselor)
        self.assertNotIn('add_payment', counselor)
        accountant = {p.codename for p in roles['Accountant'].permissions.all()}
        self.assertIn('add_payment', accountant)
        self.assertIn('view_payment', accountant)

    def test_role_screen_lists_every_default_permission(self):
        from apps.account.viewsets.permission_viewset import PermissionViewSet

        listed = set(PermissionViewSet.queryset.values_list('pk', flat=True))
        for group in Group.objects.filter(name__in=['Branch Manager', 'Counselor', 'Accountant', 'Front Desk', 'Trainer']):
            missing = group.permissions.exclude(pk__in=listed).values_list('codename', flat=True)
            self.assertEqual(list(missing), [], group.name)


class UserRightsTests(APITestCase):
    """Adding and editing users can't hand out more rights than the person has."""

    def setUp(self):
        from django.contrib.auth.models import Group, Permission

        from apps.branch.models import Branch

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        perms = Permission.objects.filter(
            content_type__app_label='account', codename__in=['add_user', 'change_user', 'view_user'],
        )
        self.manager = User.objects.create_user(email='m@test.com', name='Manager', password='pass', branch=self.ktm)
        self.manager.user_permissions.set(perms)
        self.counselor_role = Group.objects.create(name='Test counselor')
        self.counselor_role.permissions.set(perms.filter(codename='view_user'))
        self.admin_role = Group.objects.create(name='Test admin')
        self.admin_role.permissions.set(Permission.objects.filter(codename__in=['delete_user', 'add_user']))
        self.staff = User.objects.create_user(email='s@test.com', name='Staff', password='pass', is_staff=True)
        self.root = User.objects.create_superuser(email='r@test.com', name='Root', password='pass')
        self.client.force_authenticate(self.manager)

    def _register(self, **body):
        return self.client.post('/api/auth/register/', {
            'email': 'new@test.com', 'name': 'New', 'password': 'Secret123!', **body,
        }, format='json')

    def test_adding_a_user_keeps_the_adder_signed_in(self):
        response = self._register(groups=[self.counselor_role.id])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('access_token', response.cookies)
        self.assertNotIn('refresh_token', response.cookies)
        new = User.objects.get(email='new@test.com')
        self.assertEqual(new.branch, self.ktm)  # kept in the manager's branch
        self.assertTrue(new.check_password('Secret123!'))

    def test_new_user_needs_a_password(self):
        response = self.client.post('/api/auth/register/', {'email': 'n@test.com', 'name': 'N'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)

    def test_cannot_give_a_role_with_more_rights(self):
        response = self._register(groups=[self.admin_role.id])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Test admin', str(response.data['groups']))
        self.assertEqual(self._register(branch=self.pkr.id).status_code, 400)

    def test_cannot_raise_own_rights_or_touch_staff(self):
        me = f'/api/users/{self.manager.id}/'
        self.assertEqual(self.client.patch(me, {'groups': [self.admin_role.id]}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(me, {'branch': None}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(me, {'name': 'Manager K'}, format='json').status_code, 200)
        # Staff and superusers are out of reach (other branch, or staff).
        staff = f'/api/users/{self.staff.id}/'
        self.assertIn(self.client.patch(staff, {'password': 'x12345678'}, format='json').status_code, (400, 404))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('pass'))

    def test_only_a_superuser_changes_a_superuser(self):
        self.client.force_authenticate(self.staff)
        root = f'/api/users/{self.root.id}/'
        self.assertEqual(self.client.patch(root, {'password': 'x12345678'}, format='json').status_code, 400)
        self.client.force_authenticate(self.root)
        self.assertEqual(self.client.patch(root, {'name': 'Root 2'}, format='json').status_code, 200)


DEFAULT_ROLE_NAMES = ['Branch Manager', 'Counselor', 'Accountant', 'Front Desk', 'Trainer']


def _role_migration():
    import importlib

    return importlib.import_module('apps.account.migrations.0010_default_roles')


class DefaultRolesMigrationTests(APITestCase):
    """The default-roles migration: what it makes and what it leaves alone."""

    def _run_again(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        state = MigrationExecutor(connection).loader.project_state(('account', '0010_default_roles'))
        _role_migration().add_default_roles(state.apps, None)

    def test_every_listed_permission_exists(self):
        # A typo in the list would silently leave a permission out.
        import importlib

        later = importlib.import_module('apps.student.migrations.0005_branch_moves').ROLE_RIGHTS
        for name, models in _role_migration().DEFAULT_ROLES.items():
            expected = {('student', codename) for codename in later.get(name, [])}
            for label, actions in models.items():
                app_label, model = label.split('.')
                expected |= {(app_label, f'{action}_{model}') for action in actions}
            got = set(
                Group.objects.get(name=name).permissions.values_list('content_type__app_label', 'codename')
            )
            self.assertEqual(got, expected, name)

    def test_running_again_keeps_admin_changes(self):
        counselor = Group.objects.get(name='Counselor')
        counselor.permissions.remove(Permission.objects.get(codename='add_lead'))
        Group.objects.filter(name='Trainer').update(name='trainer')
        self._run_again()
        self.assertFalse(counselor.permissions.filter(codename='add_lead').exists())
        self.assertFalse(Group.objects.filter(name='Trainer').exists())
        self.assertEqual(Group.objects.filter(name__in=DEFAULT_ROLE_NAMES).count(), 4)

    def test_running_again_on_an_empty_database_makes_all_roles(self):
        Group.objects.filter(name__in=DEFAULT_ROLE_NAMES).delete()
        self._run_again()
        for name in DEFAULT_ROLE_NAMES:
            self.assertTrue(Group.objects.get(name=name).permissions.exists(), name)


class DefaultRoleAbilitiesTests(APITestCase):
    """Each default role can do what its name says, using the checks the API makes."""

    CAN = {
        'Branch Manager': [
            'lead.add_lead', 'lead.change_lead', 'lead.delete_lead', 'lead.change_leadcourseinterest',
            'lead.add_enrollment', 'lead.change_enrollment', 'lead.add_payment', 'lead.change_payment',
            'lead.add_adjustment', 'lead.view_payment', 'course.add_batchcost', 'course.change_batchcost',
            'course.change_coursebatch', 'student.add_attendance', 'lead.add_teamslicence',
            'lead.change_teamslicence', 'lead.add_teamsaccount', 'lead.change_teamsaccount',
            'student.move_branch',
        ],
        'Counselor': [
            'lead.add_lead', 'lead.change_lead', 'lead.change_leadcourseinterest', 'lead.add_enrollment',
            'lead.change_enrollment', 'lead.add_followup', 'lead.add_leadcall', 'lead.view_lead',
        ],
        'Accountant': [
            'lead.view_lead', 'lead.change_enrollment', 'lead.add_payment', 'lead.change_payment',
            'lead.add_adjustment', 'lead.view_payment', 'course.add_batchcost', 'course.change_batchcost',
        ],
        'Front Desk': [
            'lead.add_lead', 'lead.change_lead', 'lead.view_lead', 'student.add_student',
            'student.add_attendance',
        ],
        'Trainer': ['student.add_attendance', 'student.view_student'],
    }
    CANNOT = {
        'Branch Manager': ['lead.delete_payment', 'lead.delete_enrollment', 'account.add_user', 'auth.add_group'],
        'Counselor': [
            'lead.delete_lead', 'lead.delete_enrollment', 'lead.add_payment', 'lead.view_payment',
            'lead.add_adjustment', 'lead.add_teamsaccount', 'account.change_user', 'student.move_branch',
        ],
        'Accountant': ['lead.add_lead', 'lead.change_lead', 'course.change_coursebatch', 'account.change_user'],
        'Front Desk': [
            'lead.delete_lead', 'lead.add_enrollment', 'lead.change_enrollment', 'lead.add_payment',
            'lead.view_payment', 'student.move_branch',
        ],
        'Trainer': ['lead.view_lead', 'lead.add_lead', 'student.change_student', 'lead.view_payment'],
    }

    def test_each_role_has_the_rights_it_needs_and_no_more(self):
        for name in DEFAULT_ROLE_NAMES:
            user = User.objects.create_user(email=f'{name.replace(" ", "")}@test.com', name=name, password='x')
            user.groups.add(Group.objects.get(name=name))
            for perm in self.CAN[name]:
                self.assertTrue(user.has_perm(perm), f'{name} should have {perm}')
            for perm in self.CANNOT[name]:
                self.assertFalse(user.has_perm(perm), f'{name} should not have {perm}')


class DefaultRolesThroughTheApiTests(APITestCase):
    """The same rights, checked on real requests."""

    def setUp(self):
        from apps.branch.models import Branch

        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')

    def _as(self, role):
        user = User.objects.create_user(
            email=f'{role.replace(" ", "")}@test.com', name=role, password='x', branch=self.branch,
        )
        user.groups.add(Group.objects.get(name=role))
        self.client.force_authenticate(user)
        return user

    def test_counselor_adds_a_lead_but_cannot_delete_it(self):
        self._as('Counselor')
        response = self.client.post('/api/leads/', {'full_name': 'Hari', 'phone': '9800077101'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.delete(f"/api/leads/{response.data['id']}/").status_code, 403)

    def test_money_and_confirm_need_the_right_role(self):
        # Rights are checked before the record is looked up, so ids need not exist.
        pay = '/api/leads/999/enrollments/999/payments/'
        confirm = '/api/leads/999/enrollments/999/confirm/'
        self._as('Front Desk')
        self.assertEqual(self.client.post(confirm, {}, format='json').status_code, 403)
        # Counselors confirm enrollments but don't take money.
        self._as('Counselor')
        self.assertEqual(self.client.post(pay, {}, format='json').status_code, 403)
        self.assertNotEqual(self.client.post(confirm, {}, format='json').status_code, 403)
        due_dates = '/api/leads/999/enrollments/999/due-dates/'
        self.assertNotEqual(self.client.post(due_dates, {}, format='json').status_code, 403)
        self._as('Accountant')
        self.assertNotEqual(self.client.post(pay, {}, format='json').status_code, 403)
        self.assertNotEqual(self.client.post(confirm, {}, format='json').status_code, 403)

    def test_trainer_marks_attendance_but_cannot_add_leads(self):
        attendance = '/api/course-batches/999/attendance/'
        self._as('Trainer')
        self.assertNotEqual(self.client.post(attendance, {}, format='json').status_code, 403)
        response = self.client.post('/api/leads/', {'full_name': 'Hari', 'phone': '9800077102'}, format='json')
        self.assertEqual(response.status_code, 403)
        self._as('Counselor')
        self.assertEqual(self.client.post(attendance, {}, format='json').status_code, 403)

    def test_batch_close_and_costs_need_the_right_role(self):
        close, costs = '/api/course-batches/999/close/', '/api/course-batches/999/costs/'
        self._as('Accountant')
        self.assertEqual(self.client.post(close, {}, format='json').status_code, 403)
        self.assertNotEqual(self.client.post(costs, {}, format='json').status_code, 403)
        self._as('Branch Manager')
        self.assertNotEqual(self.client.post(close, {}, format='json').status_code, 403)

    def test_branch_manager_adds_teams_accounts(self):
        self._as('Counselor')
        self.assertEqual(self.client.post('/api/teams-accounts/', {}, format='json').status_code, 403)
        self._as('Branch Manager')
        self.assertNotEqual(self.client.post('/api/teams-accounts/', {}, format='json').status_code, 403)


class RoleApiTests(APITestCase):
    """Adding, renaming and deleting roles."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@test.com', name='Admin', password='x', is_staff=True)
        self.member = User.objects.create_user(email='m@test.com', name='Member', password='x')
        self.client.force_authenticate(self.admin)
        self.counselor = Group.objects.get(name='Counselor')
        self.view_lead = Permission.objects.get(codename='view_lead')

    def _add(self, name, **body):
        return self.client.post('/api/groups/', {'name': name, **body}, format='json')

    def test_name_must_be_new_in_any_capitalisation(self):
        for name in ['Counselor', 'counselor', 'COUNSELOR', '  Counselor  ']:
            response = self._add(name)
            self.assertEqual(response.status_code, 400, name)
            self.assertEqual(response.data['name'], ['A role with this name already exists.'], name)
        self.assertEqual(Group.objects.filter(name__iexact='counselor').count(), 1)

    def test_name_cannot_be_blank_or_too_long(self):
        for name in ['', '   ', 'x' * 151]:
            self.assertEqual(self._add(name).status_code, 400, repr(name))
        self.assertEqual(self.client.post('/api/groups/', {}, format='json').status_code, 400)
        self.assertEqual(self._add('x' * 150).status_code, 201)

    def test_name_is_saved_trimmed(self):
        response = self._add('  Sales Lead  ', permissions=[self.view_lead.id])
        self.assertEqual(response.status_code, 201, response.data)
        role = Group.objects.get(pk=response.data['id'])
        self.assertEqual(role.name, 'Sales Lead')
        self.assertEqual(list(role.permissions.all()), [self.view_lead])

    def test_rename_to_own_name_in_new_case_but_not_to_another_role(self):
        url = f'/api/groups/{self.counselor.id}/'
        self.assertEqual(self.client.patch(url, {'name': 'COUNSELOR'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(url, {'name': 'counselor'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(url, {'name': 'trainer'}, format='json').status_code, 400)
        self.counselor.refresh_from_db()
        self.assertEqual(self.counselor.name, 'counselor')

    def test_editing_permissions_replaces_the_list(self):
        url = f'/api/groups/{self.counselor.id}/'
        response = self.client.patch(url, {'permissions': [self.view_lead.id]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self.counselor.permissions.all()), [self.view_lead])
        self.assertEqual(self.client.patch(url, {'permissions': []}, format='json').status_code, 200)
        self.assertFalse(self.counselor.permissions.exists())
        self.assertEqual(self.client.patch(url, {'permissions': [999999]}, format='json').status_code, 400)

    def test_deleting_a_role_takes_its_rights_from_its_users(self):
        self.member.groups.add(self.counselor)
        self.assertTrue(User.objects.get(pk=self.member.pk).has_perm('lead.add_lead'))
        self.assertEqual(self.client.delete(f'/api/groups/{self.counselor.id}/').status_code, 204)
        member = User.objects.get(pk=self.member.pk)
        self.assertFalse(member.groups.exists())
        self.assertFalse(member.has_perm('lead.add_lead'))

    def test_anyone_signed_in_reads_roles_only_admins_change_them(self):
        url = f'/api/groups/{self.counselor.id}/'
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get('/api/groups/').status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self._add('Mine').status_code, 403)
        self.assertEqual(self.client.patch(url, {'name': 'Mine'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.assertEqual(self.client.get('/api/permissions/').status_code, 403)
        # Even with every right on roles, changing them stays with admins.
        self.member.user_permissions.set(Permission.objects.filter(content_type__app_label='auth'))
        self.client.force_authenticate(User.objects.get(pk=self.member.pk))
        self.assertEqual(self._add('Mine').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/groups/').status_code, 401)

    def test_permission_list_has_no_leftover_lead_type(self):
        from apps.account.viewsets.permission_viewset import PermissionViewSet

        self.assertFalse(PermissionViewSet.queryset.filter(codename__icontains='leadtype').exists())
        self.assertFalse(Permission.objects.filter(codename__icontains='leadtype').exists())

    def test_roles_come_back_in_name_order(self):
        names = [g['name'] for g in self.client.get('/api/groups/').data['results']]
        self.assertEqual(names, sorted(names))

    def test_users_in_any_of_several_roles(self):
        senior = Group.objects.create(name='Senior Counselor')
        both = User.objects.create_user(email='both@test.com', name='Both', password='x')
        both.groups.add(self.counselor, senior)
        only_senior = User.objects.create_user(email='senior@test.com', name='Senior', password='x')
        only_senior.groups.add(senior)
        self.member.groups.add(Group.objects.get(name='Trainer'))
        response = self.client.get(f'/api/users/?group__in={self.counselor.id},{senior.id}&page_size=50')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(u['email'] for u in response.data['results']), ['both@test.com', 'senior@test.com'])

    def test_permission_list_is_readable_and_skips_role_rights(self):
        results = self.client.get('/api/permissions/?page_size=500').data['results']
        sections = {p['content_type_name'] for p in results}
        self.assertIn('Lead call', sections)
        self.assertIn('Shift', sections)  # course.batch
        self.assertNotIn('Group', sections)
        self.assertNotIn('Permission', sections)
        self.assertFalse(any('.' in s for s in sections))


class BranchRequiredTests(APITestCase):
    """Everyone but staff works inside one branch."""

    def setUp(self):
        from django.core.cache import cache

        from apps.branch.models import Branch

        cache.clear()  # sign-in attempts are rate limited
        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.admin = User.objects.create_user(email='a@test.com', name='Admin', password='pass', is_staff=True)
        self.client.force_authenticate(self.admin)

    def _register(self, **body):
        return self.client.post('/api/auth/register/', {
            'email': 'new@test.com', 'name': 'New', 'password': 'Secret123!', **body,
        }, format='json')

    def _sign_in(self, email):
        self.client.force_authenticate(None)
        return self.client.post('/api/auth/login/', {'email': email, 'password': 'pass'}, format='json')

    def test_new_user_needs_a_branch(self):
        response = self._register()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['branch'], ['Pick a branch.'])
        self.assertEqual(self._register(branch=self.ktm.id).status_code, 201)

    def test_branch_cannot_be_taken_off_a_user(self):
        user = User.objects.create_user(email='u@test.com', name='U', password='pass', branch=self.ktm)
        url = f'/api/users/{user.id}/'
        self.assertEqual(self.client.patch(url, {'branch': None}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'name': 'U2'}, format='json').status_code, 200)
        # An older user saved without a branch must get one with the next edit.
        old = User.objects.create_user(email='old@test.com', name='Old', password='pass')
        response = self.client.patch(f'/api/users/{old.id}/', {'name': 'Old 2'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('branch', response.data)
        # Staff need no branch.
        response = self.client.patch(f'/api/users/{self.admin.id}/', {'name': 'Admin 2'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_user_without_a_branch_cannot_sign_in(self):
        User.objects.create_user(email='nob@test.com', name='No branch', password='pass')
        User.objects.create_user(email='ktm@test.com', name='Ktm', password='pass', branch=self.ktm)
        response = self._sign_in('nob@test.com')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Your account has no branch yet', str(response.data))
        self.assertNotIn('access_token', response.cookies)
        self.assertEqual(self._sign_in('ktm@test.com').status_code, 200)
        self.assertEqual(self._sign_in('a@test.com').status_code, 200)  # staff

    def test_losing_the_branch_signs_the_user_out(self):
        user = User.objects.create_user(email='ktm@test.com', name='Ktm', password='pass', branch=self.ktm)
        self.assertEqual(self._sign_in('ktm@test.com').status_code, 200)
        self.assertEqual(self.client.get('/api/users/me/').status_code, 200)
        user.branch = None
        user.save(update_fields=['branch'])
        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/refresh/', {}, format='json').status_code, 401)


class SessionCookieTests(APITestCase):
    """Refresh and sign out with a good, used, bad or missing refresh token."""

    def setUp(self):
        from apps.branch.models import Branch

        branch = Branch.objects.create(name='Kathmandu', code='KTM')
        User.objects.create_user(email='session@test.com', name='S', password='pass', branch=branch)
        response = self.client.post(
            '/api/auth/login/', {'email': 'session@test.com', 'password': 'pass'}, format='json',
        )
        self.assertEqual(response.status_code, 200)

    def _refresh(self):
        return self.client.post('/api/auth/refresh/', {}, format='json')

    def _logout(self):
        return self.client.post('/api/auth/logout/', {}, format='json')

    def _assert_cookies_cleared(self, response):
        self.assertEqual(response.cookies['access_token'].value, '')
        self.assertEqual(response.cookies['refresh_token'].value, '')

    def test_refresh_gives_new_cookies(self):
        response = self._refresh()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies['access_token'].value)

    def test_bad_refresh_token_is_refused(self):
        self.client.cookies['refresh_token'] = 'not-a-token'
        self.assertEqual(self._refresh().status_code, 401)

    def test_used_refresh_token_is_refused(self):
        old = self.client.cookies['refresh_token'].value
        self.assertEqual(self._refresh().status_code, 200)
        self.client.cookies['refresh_token'] = old  # rotated out, so blacklisted
        self.assertEqual(self._refresh().status_code, 401)

    def test_sign_out_blacklists_the_token_and_clears_cookies(self):
        refresh = self.client.cookies['refresh_token'].value
        response = self._logout()
        self.assertEqual(response.status_code, 200)
        self._assert_cookies_cleared(response)
        self.client.cookies['refresh_token'] = refresh
        self.assertEqual(self._refresh().status_code, 401)

    def test_sign_out_with_a_bad_or_missing_token_still_clears_cookies(self):
        self.client.cookies['refresh_token'] = 'not-a-token'
        response = self._logout()
        self.assertEqual(response.status_code, 200)
        self._assert_cookies_cleared(response)
        del self.client.cookies['refresh_token']
        response = self._logout()
        self.assertEqual(response.status_code, 200)
        self._assert_cookies_cleared(response)


class InactiveBranchTests(APITestCase):
    """Users of an inactive branch can't sign in and are signed out."""

    def setUp(self):
        from django.core.cache import cache

        from apps.branch.models import Branch

        cache.clear()  # sign-in attempts are rate limited
        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        User.objects.create_user(email='ktm@test.com', name='Ktm', password='pass', branch=self.ktm)
        User.objects.create_user(email='admin@test.com', name='Admin', password='pass', is_staff=True)

    def _sign_in(self, email):
        return self.client.post('/api/auth/login/', {'email': email, 'password': 'pass'}, format='json')

    def test_inactive_branch_refuses_sign_in(self):
        self.ktm.is_active = False
        self.ktm.save()
        response = self._sign_in('ktm@test.com')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Your branch is inactive', str(response.data))
        self.assertNotIn('access_token', response.cookies)
        self.assertEqual(self._sign_in('admin@test.com').status_code, 200)  # staff

    def test_branch_made_inactive_signs_the_user_out(self):
        self.assertEqual(self._sign_in('ktm@test.com').status_code, 200)
        self.assertEqual(self.client.get('/api/users/me/').status_code, 200)
        self.ktm.is_active = False
        self.ktm.save()
        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/refresh/', {}, format='json').status_code, 401)
        self.ktm.is_active = True
        self.ktm.save()
        self.assertEqual(self._sign_in('ktm@test.com').status_code, 200)


@mock.patch('apps.account.auth_views.send_password_changed_email_task.delay')
class PasswordChangeSignOutTests(APITestCase):
    """A new password signs out every other device: old cookies can't read or refresh."""

    def setUp(self):
        from django.core.cache import cache

        from apps.branch.models import Branch

        cache.clear()
        branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.user = User.objects.create_user(email='u@test.com', name='U', password='pass', branch=branch)
        self.admin = User.objects.create_user(email='admin@test.com', name='Admin', password='pass', is_staff=True)
        self.other_device = self._signed_in_client('u@test.com')

    def _signed_in_client(self, email, password='pass'):
        client = APIClient()
        response = client.post('/api/auth/login/', {'email': email, 'password': password}, format='json')
        self.assertEqual(response.status_code, 200)
        return client

    def _assert_signed_out(self, client):
        self.assertEqual(client.get('/api/users/me/').status_code, 401)
        self.assertEqual(client.post('/api/auth/refresh/', {}, format='json').status_code, 401)

    def test_change_password_signs_out_other_devices_and_keeps_this_one(self, _email):
        this_device = self._signed_in_client('u@test.com')
        response = this_device.post('/api/auth/change-password/', {
            'old_password': 'pass', 'new_password': 'NewPass123!', 'confirm_new_password': 'NewPass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self._assert_signed_out(self.other_device)
        # The device that changed it got new cookies and stays signed in.
        self.assertTrue(response.cookies['access_token'].value)
        self.assertEqual(this_device.get('/api/users/me/').status_code, 200)
        self.assertEqual(this_device.post('/api/auth/refresh/', {}, format='json').status_code, 200)
        self._signed_in_client('u@test.com', 'NewPass123!')

    def test_reset_password_signs_out_every_device(self, _email):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        response = APIClient().post('/api/auth/reset-password/', {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': default_token_generator.make_token(self.user),
            'new_password': 'NewPass123!', 'confirm_new_password': 'NewPass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self._assert_signed_out(self.other_device)

    def test_admin_setting_a_password_signs_the_user_out(self, _email):
        admin = self._signed_in_client('admin@test.com')
        response = admin.patch(f'/api/users/{self.user.id}/', {'password': 'NewPass123!'}, format='json')
        self.assertEqual(response.status_code, 200)
        self._assert_signed_out(self.other_device)
        self.assertEqual(admin.get('/api/users/me/').status_code, 200)

    def test_editing_without_a_password_keeps_the_user_signed_in(self, _email):
        admin = self._signed_in_client('admin@test.com')
        response = admin.patch(f'/api/users/{self.user.id}/', {'name': 'U2'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.other_device.get('/api/users/me/').status_code, 200)


class SecureCookieTests(APITestCase):
    """Sign-in cookies are Secure unless AUTH_COOKIE_SECURE is off (local http with DEBUG)."""

    def setUp(self):
        from django.core.cache import cache

        from apps.branch.models import Branch

        cache.clear()
        branch = Branch.objects.create(name='Kathmandu', code='KTM')
        User.objects.create_user(email='u@test.com', name='U', password='pass', branch=branch)

    def _sign_in(self):
        return self.client.post('/api/auth/login/', {'email': 'u@test.com', 'password': 'pass'}, format='json')

    def test_cookies_are_secure_when_on(self):
        with override_settings(AUTH_COOKIE_SECURE=True):
            response = self._sign_in()
            self.assertEqual(response.status_code, 200)
            for name in ('access_token', 'refresh_token'):
                self.assertTrue(response.cookies[name]['secure'])
                self.assertTrue(response.cookies[name]['httponly'])
            refreshed = self.client.post('/api/auth/refresh/', {}, format='json')
            self.assertTrue(refreshed.cookies['access_token']['secure'])

    def test_cookies_are_not_secure_when_off(self):
        with override_settings(AUTH_COOKIE_SECURE=False):
            response = self._sign_in()
            self.assertFalse(response.cookies['access_token']['secure'])

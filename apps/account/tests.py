from django.contrib.auth.models import Group, Permission
from rest_framework.test import APITestCase

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
        for name, models in _role_migration().DEFAULT_ROLES.items():
            expected = set()
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
            'lead.change_teamslicence',
        ],
        'Counselor': [
            'lead.add_lead', 'lead.change_lead', 'lead.change_leadcourseinterest', 'lead.add_enrollment',
            'lead.add_followup', 'lead.add_leadcall', 'lead.view_lead',
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
            'lead.delete_lead', 'lead.change_enrollment', 'lead.add_payment', 'lead.view_payment',
            'lead.add_adjustment', 'account.change_user',
        ],
        'Accountant': ['lead.add_lead', 'lead.change_lead', 'course.change_coursebatch', 'account.change_user'],
        'Front Desk': ['lead.delete_lead', 'lead.add_enrollment', 'lead.add_payment', 'lead.view_payment'],
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
        self._as('Counselor')
        self.assertEqual(self.client.post(pay, {}, format='json').status_code, 403)
        self.assertEqual(self.client.post(confirm, {}, format='json').status_code, 403)
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

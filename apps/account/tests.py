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
        group = Group.objects.create(name='Counselor')
        group.permissions.add(Permission.objects.get(codename='add_lead'))
        self.user.groups.add(group)
        response = self.client.get('/api/users/me/')
        self.assertEqual(response.data['user_permissions'], ['lead.add_lead'])
        self.assertEqual(response.data['groups'], [group.id])

    def test_me_needs_sign_in(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)


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
        self.counselor_role = Group.objects.create(name='Counselor')
        self.counselor_role.permissions.set(perms.filter(codename='view_user'))
        self.admin_role = Group.objects.create(name='Admin')
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
        self.assertIn('Admin', str(response.data['groups']))
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

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

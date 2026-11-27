"""Helpers for tests."""
from django.contrib.auth.models import Permission


def give_rights(user, *rights):
    """Give a user rights by 'app.codename'. Returns a fresh copy, as the old one caches rights."""
    for right in rights:
        app_label, codename = right.split('.')
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename),
        )
    return type(user).objects.get(pk=user.pk)


def give_view_rights(*users):
    """Every view right, for tests about branch walls rather than rights."""
    rights = Permission.objects.filter(codename__startswith='view_')
    for user in users:
        user.user_permissions.add(*rights)

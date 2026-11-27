from django.conf import settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


def set_auth_cookies(response, access=None, refresh=None):
    """Put the sign-in tokens in httpOnly cookies, Secure unless AUTH_COOKIE_SECURE is off."""
    for name, value in (('access_token', access), ('refresh_token', refresh)):
        if value:
            response.set_cookie(
                name, str(value),
                httponly=True, samesite='Lax', path='/', secure=settings.AUTH_COOKIE_SECURE,
            )


def sign_out_everywhere(user):
    """Blacklist every refresh token the user holds, so no device can stay signed in.

    Access tokens stop working too: they carry a digest of the password they
    were made with (SIMPLE_JWT CHECK_REVOKE_TOKEN).
    """
    for token in OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True):
        BlacklistedToken.objects.get_or_create(token=token)

"""Rate limits for the doors anyone can knock on: sign in, password reset and the lead intake.

Rates are in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']. Behind the frontend's
nginx, set NUM_PROXIES=1 so the client's own address is used, not nginx's.
Sign in and password reset count per address and email, so one office
sharing an address isn't locked out by one person's typos.
"""
from rest_framework.throttling import SimpleRateThrottle


class _PerAddress(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class _PerAddressAndEmail(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        email = str(request.data.get('email', '')).strip().lower()
        return self.cache_format % {'scope': self.scope, 'ident': f'{self.get_ident(request)}:{email}'}


class LoginThrottle(_PerAddressAndEmail):
    scope = 'login'


class PasswordResetThrottle(_PerAddressAndEmail):
    scope = 'password_reset'


class LeadIntakeThrottle(_PerAddress):
    scope = 'lead_intake'

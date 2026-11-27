from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

from apps.account.models import branch_refusal


class CookieJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        refusal = branch_refusal(user)
        if refusal:
            raise AuthenticationFailed(refusal)
        return user

    def authenticate(self, request):
        raw_token = request.COOKIES.get('access_token')
        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token)
                user = self.get_user(validated_token)
                return (user, validated_token)
            except (InvalidToken, AuthenticationFailed):
                return None
        return super().authenticate(request)

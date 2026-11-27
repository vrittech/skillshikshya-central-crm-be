from django.contrib.auth import authenticate

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from apps.account.models import User, branch_refusal


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'email'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'] = serializers.EmailField(required=True)
        self.fields.pop('username', None)

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.pop('password')

        if not email:
            raise serializers.ValidationError('Email is required.')

        user = authenticate(username=email, password=password)

        if user is None or not user.is_active:
            raise serializers.ValidationError(
                'Invalid email or password.',
            )
        refusal = branch_refusal(user)
        if refusal:
            raise serializers.ValidationError(refusal)

        self.user = user
        refresh = self.get_token(user)

        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }


class BranchCheckedRefreshSerializer(TokenRefreshSerializer):
    """Signs the user out at refresh when their branch was taken away, never
    set or made inactive, or when their password changed after sign-in."""

    def validate(self, attrs):
        data = super().validate(attrs)
        token = AccessToken(data['access'])
        user = User.objects.filter(pk=token['user_id']).first()
        if user is None:
            raise AuthenticationFailed('Sign in again.')
        if token.get(api_settings.REVOKE_TOKEN_CLAIM) != get_md5_hash_password(user.password):
            raise AuthenticationFailed('Your password was changed. Sign in again.')
        refusal = branch_refusal(user)
        if refusal:
            raise AuthenticationFailed(refusal)
        return data

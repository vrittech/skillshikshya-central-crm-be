from django.contrib.auth import authenticate

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import AccessToken

from apps.account.models import NO_BRANCH_MESSAGE, User, has_no_branch


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
        if has_no_branch(user):
            raise serializers.ValidationError(NO_BRANCH_MESSAGE)

        self.user = user
        refresh = self.get_token(user)

        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }


class BranchCheckedRefreshSerializer(TokenRefreshSerializer):
    """A user whose branch was taken away (or never set) is signed out at refresh."""

    def validate(self, attrs):
        data = super().validate(attrs)
        user = User.objects.filter(pk=AccessToken(data['access'])['user_id']).first()
        if user is None or has_no_branch(user):
            raise AuthenticationFailed(NO_BRANCH_MESSAGE)
        return data

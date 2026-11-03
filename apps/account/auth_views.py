from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenBlacklistView,
)
from apps.account.models import User
from apps.account.serializers.auth_serializers import CustomTokenObtainPairSerializer
from apps.account.serializers.user_serializer import UserSerializer, UserMiniSerializer
from apps.account.tasks import send_password_changed_email_task, send_reset_link_email_task
from apps.global_config.permissions import IsStaffOrHasPermission


class RegisterView(APIView):
    permission_classes = [IsStaffOrHasPermission]
    queryset = User.objects.none()

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                'message': 'Registration successful',
                'user': UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CookieTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response = Response(
            {'message': 'Login successful', 'user': UserMiniSerializer(serializer.user).data},
            status=status.HTTP_200_OK,
        )
        self._set_cookies(response, serializer.validated_data)
        return response

    def _set_cookies(self, response, data):
        access = data.get('access')
        refresh = data.get('refresh')
        if access:
            response.set_cookie(
                'access_token', access,
                httponly=True, samesite='Lax', path='/',
            )
        if refresh:
            response.set_cookie(
                'refresh_token', refresh,
                httponly=True, samesite='Lax', path='/',
            )


class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        if 'refresh' not in data and 'refresh_token' in request.COOKIES:
            data['refresh'] = request.COOKIES['refresh_token']

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        response = Response(
            {'message': 'Token refreshed'},
            status=status.HTTP_200_OK,
        )

        access = serializer.validated_data.get('access')
        if access:
            response.set_cookie(
                'access_token', access,
                httponly=True, samesite='Lax', path='/',
            )

        refresh = serializer.validated_data.get('refresh')
        if refresh:
            response.set_cookie(
                'refresh_token', refresh,
                httponly=True, samesite='Lax', path='/',
            )

        return response


class CookieTokenBlacklistView(TokenBlacklistView):
    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        if 'refresh' not in data and 'refresh_token' in request.COOKIES:
            data['refresh'] = request.COOKIES['refresh_token']

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        response = Response(
            {'message': 'Logged out successfully'},
            status=status.HTTP_200_OK,
        )
        response.delete_cookie('access_token', path='/')
        response.delete_cookie('refresh_token', path='/')
        return response


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        old_password = request.data.get('old_password')
        new_password = request.data.get('new_password')
        confirm_new_password = request.data.get('confirm_new_password')

        if not all([old_password, new_password, confirm_new_password]):
            return Response(
                {'detail': 'All fields are required: old_password, new_password, confirm_new_password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if new_password != confirm_new_password:
            return Response(
                {'detail': 'New passwords do not match.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        if not user.check_password(old_password):
            return Response(
                {'detail': 'Old password is incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        send_password_changed_email_task.delay(user.email, user.name)

        return Response(
            {'detail': 'Password changed successfully.'},
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response(
                {'detail': 'Email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_data = {
            'detail': 'If this email exists, a password reset link has been sent.'
        }

        if User.objects.filter(email=email).exists():
            user = User.objects.get(email=email)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
            send_reset_link_email_task.delay(email, reset_link)

            # Reset link is never returned in the API response (security).
            # Uncomment for local debugging only.
            # if settings.DEBUG:
            #     response_data['reset_link'] = reset_link

        return Response(response_data, status=status.HTTP_200_OK)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid')
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        confirm_new_password = request.data.get('confirm_new_password')

        if not all([uid, token, new_password, confirm_new_password]):
            return Response(
                {'detail': 'All fields are required: uid, token, new_password, confirm_new_password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if new_password != confirm_new_password:
            return Response(
                {'detail': 'New passwords do not match.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {'detail': 'Invalid reset link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {'detail': 'Invalid or expired reset link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()
        send_password_changed_email_task.delay(user.email, user.name)

        return Response(
            {'detail': 'Password reset successfully.'},
            status=status.HTTP_200_OK,
        )

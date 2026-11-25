from django.contrib import admin
from django.urls import path, include, re_path
from apps.account.auth_views import (
    RegisterView,
    CookieTokenObtainPairView,
    CookieTokenRefreshView,
    CookieTokenBlacklistView,
    ChangePasswordView,
    ForgotPasswordView,
    ResetPasswordView,
)
from apps.global_config.views import upload_file, serve_file
from apps.lead.intake import lead_intake

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/register/', RegisterView.as_view(), name='auth_register'),
    path('api/auth/login/', CookieTokenObtainPairView.as_view(), name='auth_login'),
    path('api/auth/refresh/', CookieTokenRefreshView.as_view(), name='auth_refresh'),
    path('api/auth/logout/', CookieTokenBlacklistView.as_view(), name='auth_logout'),
    path('api/auth/change-password/', ChangePasswordView.as_view(), name='auth_change_password'),
    path('api/auth/forgot-password/', ForgotPasswordView.as_view(), name='auth_forgot_password'),
    path('api/auth/reset-password/', ResetPasswordView.as_view(), name='auth_reset_password'),
    path('api/', include('apps.account.urls')),
    path('api/', include('apps.callminutes.urls')),
    path('api/', include('apps.course.urls')),
    path('api/', include('apps.global_config.urls')),
    path('api/', include('apps.branch.urls')),
    path('api/', include('apps.invoices.urls')),
    path('api/', include('apps.lead.urls')),
    path('api/', include('apps.leadreport.urls')),
    path('api/', include('apps.orbitchat.urls')),
    path('api/', include('apps.student.urls')),
    path('api/upload/', upload_file, name='upload_file'),
    path('api/public/lead-intake/', lead_intake, name='lead_intake'),
    re_path(r'^api/files/(?P<key>.+)$', serve_file, name='serve_file'),


]

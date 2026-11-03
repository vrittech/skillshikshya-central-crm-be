"""
Django settings for skillshikshyacrm project.

Generated skeleton for 'skillshikshyacrm'.
"""
from pathlib import Path
import os
from datetime import timedelta
from celery.schedules import crontab
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Celery

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

CELERY_BEAT_SCHEDULE = {
    'send-followup-reminders-every-5-minutes': {
        'task': 'apps.lead.tasks.send_followup_reminders_task',
        'schedule': 300,  # every 5 minutes
    },
    # OrbitChat lead sync — runs hourly on NPT hour boundaries.
    # minute=15 UTC == minute 0 Nepal time (UTC+5:45).
    # Enable/disable via Django Admin -> Orbitchat -> Sync states -> is_enabled
    # (no restart needed; automatic runs no-op while disabled).
    'poll-orbitchat-leads-hourly': {
        'task': 'apps.orbitchat.tasks.poll_orbitchat_leads_task',
        'schedule': crontab(minute=15),  # UTC 07:15 == NPT 13:00
    },
}


# Email

EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Skill Shikshya <noreply@skillshikshya.com>')

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/stable/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-me')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'corsheaders',
    'django_filters',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    # Project apps
    'apps.account',
    'apps.branch',
    'apps.callminutes',
    'apps.course',
    'apps.global_config',
    'apps.invoices',
    'apps.lead',
    'apps.leadreport',
    'apps.orbitchat',
    'apps.student',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'skillshikshyacrm.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'skillshikshyacrm.wsgi.application'
ASGI_APPLICATION = 'skillshikshyacrm.asgi.application'


# Database
# https://docs.djangoproject.com/en/stable/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.environ.get('DB_NAME', 'skillshikshyacrm_db'),
        'USER': os.environ.get('DB_USER', 'skillshikshyacrm_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'skillshikshyacrm_pass'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/stable/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/stable/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/stable/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'mediafiles'

# Default primary key field type
# https://docs.djangoproject.com/en/stable/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'account.User'

FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')

# OrbitChat Integration

ORBITCHAT_API_URL = os.environ.get('ORBITCHAT_API_URL', 'https://base.orbitchat.ai')
ORBITCHAT_EMAIL = os.environ.get('ORBITCHAT_EMAIL', '')
ORBITCHAT_PASSWORD = os.environ.get('ORBITCHAT_PASSWORD', '')

# Public Skill Shikshya course catalog
PUBLIC_COURSE_API_URL = os.environ.get('PUBLIC_COURSE_API_URL', 'https://base.skillshikshya.com/api/course/')

# Invoice emails

INVOICE_LOGO_URL = os.environ.get(
    'INVOICE_LOGO_URL',
    'https://pub-3a712783614d44db9c5b349676a4cff8.r2.dev/uploads/converted_image.png',
)

PASSWORD_RESET_TIMEOUT = 900  # 15 minutes

# R2 (Cloudflare) Object Storage

R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY', '')
R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID', '')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', '')
R2_PUBLIC_URL = os.environ.get('PUBLIC_DEVELOPMENT_URL', '')
R2_ENDPOINT_URL = f'https://{os.environ.get("R2_ACCOUNT_ID", "")}.r2.cloudflarestorage.com'


# Django REST Framework
# https://www.django-rest-framework.org/

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.account.authentication.CookieJWTAuthentication',
        'apps.account.authentication.CsrfExemptSessionAuthentication',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.global_config.pagination.CustomPagination',
    'PAGE_SIZE': 10,
}


# SimpleJWT
# https://django-rest-framework-simplejwt.readthedocs.io/

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    # Cookie settings
    'AUTH_COOKIE': 'access_token',
    'AUTH_COOKIE_REFRESH': 'refresh_token',
    'AUTH_COOKIE_SECURE': False,
    'AUTH_COOKIE_HTTP_ONLY': True,
    'AUTH_COOKIE_SAMESITE': 'Lax',
    'AUTH_COOKIE_PATH': '/',
}


# CORS
# https://github.com/adamchainz/django-cors-headers

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://localhost:5173',
    'http://127.0.0.1:3000',
    'http://127.0.0.1:5173',
]

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:3000',
    'http://localhost:5173',
    'http://127.0.0.1:3000',
    'http://127.0.0.1:5173',
]

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

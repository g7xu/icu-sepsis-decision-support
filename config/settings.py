"""
Django settings for ICU Sepsis Decision Support System.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Local apps
    'patients',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

# Session: use database backend for prediction cache (avoids cookie size limits)
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
# Reset simulation on new browser session (session expires when browser closes)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

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

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'mimiciv'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {
            'options': f"-c search_path={os.getenv('DB_SCHEMA', 'mimiciv_derived')},public"
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validators.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validators.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validators.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validators.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static', BASE_DIR / 'styles']

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Model service (external HTTPS prediction API)
# When MODEL_SERVICE_URL is empty, prediction returns an error (no stub/fake data)
MODEL_SERVICE_URL = os.getenv('MODEL_SERVICE_URL', '').rstrip('/')
MODEL_SERVICE_TIMEOUT = int(os.getenv('MODEL_SERVICE_TIMEOUT', '30'))
MODEL_SERVICE_API_KEY = os.getenv('MODEL_SERVICE_API_KEY', '')

# S3 storage for model IO (feature vectors/history/predictions)
MODEL_S3_BUCKET = os.getenv('MODEL_S3_BUCKET', '')
MODEL_S3_REGION = os.getenv('MODEL_S3_REGION', '')
MODEL_S3_PREFIX = os.getenv('MODEL_S3_PREFIX', 'model-io')
MODEL_HISTORY_HOURS = int(os.getenv('MODEL_HISTORY_HOURS', '6'))

# Local fallback model: loaded when MODEL_SERVICE_URL is unset or the external API fails.
LOCAL_MODEL_PATH = os.getenv('LOCAL_MODEL_PATH', str(BASE_DIR / 'models' / 'sepsis_model.joblib'))

# Optional explicit AWS credentials (for temporary/shared accounts)
# If empty, boto3 default credential chain is used.
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN', '')

# Similarity search (prediction view) - CSV of non-cohort feature vectors
# Set SIMILARITY_CSV_PATH in .env to override (path relative to project root)
SIMILARITY_CSV_PATH = os.getenv('SIMILARITY_CSV_PATH', 'static/similarity_matrix.csv')

# ── Production HTTPS settings ─────────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = False  # nginx handles the 80→443 redirect
    _domain = os.getenv('ALLOWED_HOSTS', '')
    _trusted = [f'https://{h.strip()}' for h in _domain.split(',') if h.strip() and not h.strip().replace('.', '').isdigit()]
    if _trusted:
        CSRF_TRUSTED_ORIGINS = _trusted

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ["REVOPS_DESK"] = "1"
if not os.environ.get("VERCEL"):
    load_dotenv(BASE_DIR / ".env.local")
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DESK_DEBUG") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"] + [
    h for h in os.environ.get("ALLOWED_HOSTS", "revops-desk.vercel.app").split(",") if h
]
if os.environ.get("VERCEL_URL"):
    ALLOWED_HOSTS.append(os.environ["VERCEL_URL"])
CSRF_TRUSTED_ORIGINS = ["https://" + h for h in ALLOWED_HOSTS if h not in {"localhost", "127.0.0.1", "testserver"}]
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "plane.db", "desk",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "crum.CurrentRequestUserMiddleware", "desk.middleware.PrivateResponseMiddleware",
]
ROOT_URLCONF = "desk.urls"
WSGI_APPLICATION = "desk.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
        "desk.views.navigation"]}}]
DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=0, conn_health_checks=True)}
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
AUTH_USER_MODEL = "db.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
TIME_ZONE = "Europe/Paris"
USE_TZ = True
LANGUAGE_CODE = "en"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = bool(os.environ.get("VERCEL")) and not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024
FILE_SIZE_LIMIT = 3 * 1024 * 1024
CELERY_TASK_ALWAYS_EAGER = True

# A dedicated Slack app; keep these credentials out of preview deployments.
SLACK_SIGNING_SECRET = os.environ.get("REVOPS_SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.environ.get("REVOPS_SLACK_BOT_TOKEN", "")
SLACK_TEAM_ID = os.environ.get("REVOPS_SLACK_TEAM_ID", "")
SLACK_APP_ID = os.environ.get("REVOPS_SLACK_APP_ID", "")

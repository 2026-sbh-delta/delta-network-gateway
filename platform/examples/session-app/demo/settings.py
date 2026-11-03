import os
from urllib.parse import unquote, urlsplit

SECRET_KEY = os.environ["APP_SECRET_KEY"]
if len(SECRET_KEY) < 32:
    raise RuntimeError("APP_SECRET_KEY must have at least 32 characters")
DEBUG = False
ALLOWED_HOSTS = os.environ.get("APP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("APP_CSRF_ORIGINS", "").split(",") if o]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.environ.get("APP_SECURE_COOKIES", "true") == "true"
SESSION_COOKIE_NAME = "delta_demo_session"
SESSION_COOKIE_AGE = 7200
SESSION_ENGINE = "django.contrib.sessions.backends.db"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "demo.notes"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware"]
ROOT_URLCONF = "demo.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth"]}}]
database = urlsplit(os.environ["DATABASE_URL"])
if database.scheme not in ("postgres", "postgresql"):
    raise RuntimeError("PostgreSQL required")
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": database.path.lstrip("/"), "USER": unquote(database.username or ""), "PASSWORD": unquote(database.password or ""), "HOST": database.hostname, "PORT": database.port or 5432, "CONN_MAX_AGE": 0, "OPTIONS": {"connect_timeout": 3}}}
USE_TZ = True
TIME_ZONE = "Asia/Seoul"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

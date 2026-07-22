# boot_django.py
#
# This file sets up and configures Django. It's used by scripts that need to
# execute as if running in a Django server (run_tests.py, run_canary_tests.py,
# check_migrations.py, django_shell.py).
#
# Modeled on the harness in TOMToolkit/tom_app_template: INSTALLED_APPS and
# MIDDLEWARE come from tom_common.default_settings, so this harness tracks TOM
# Toolkit's settings instead of hand-maintaining a copy. (It replaces the old
# tom_nonlocalizedevents_base/ test project, whose hand-copied settings went
# stale exactly that way.)

import os

import django
from django.conf import settings

from tom_common.default_settings import TOMTOOKIT_INSTALLED_APPS, TOMTOOKIT_MIDDLEWARE

APP_NAME = 'tom_nonlocalizedevents'  # the stand-alone app we are testing

# the repository root (this file lives in <repo>/tom_nonlocalizedevents/tests/)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def boot_django() -> None:
    settings.configure(
        BASE_DIR=BASE_DIR,
        DEBUG=True,
        SECRET_KEY='test-harness-secret-key-unsuitable-for-production',
        # The healpix/SQLAlchemy layer requires postgres (the template harness
        # uses sqlite). Defaults match both the CI service container and a
        # local docker postgres.
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.getenv('DB_NAME', 'tom_nonlocalizedevents'),
                'USER': os.getenv('DB_USER', 'postgres'),
                'PASSWORD': os.getenv('DB_PASS', 'postgres'),
                'HOST': os.getenv('DB_HOST', '127.0.0.1'),
                'PORT': os.getenv('DB_PORT', '5432'),
            }
        },
        # Migrations 0001+ were generated with AutoField PKs and the migration
        # graph is frozen (downstream TOMs depend on it) -- keep the default
        # that produced it so makemigrations --check stays clean.
        DEFAULT_AUTO_FIELD='django.db.models.AutoField',
        TOM_NAME='Test TOM',
        INSTALLED_APPS=TOMTOOKIT_INSTALLED_APPS + [APP_NAME],
        SITE_ID=1,
        EXTRA_FIELDS={},
        TIME_ZONE='UTC',
        USE_TZ=True,
        HOOKS={
            'target_post_save': 'tom_common.hooks.target_post_save',
            'observation_change_state': 'tom_common.hooks.observation_change_state',
            'data_product_post_upload': 'tom_dataproducts.hooks.data_product_post_upload'
        },
        MIDDLEWARE=TOMTOOKIT_MIDDLEWARE,
        TEMPLATES=[
            {
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'DIRS': [os.path.join(BASE_DIR, 'templates')],
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
        ],
        AUTHENTICATION_BACKENDS=(
            'django.contrib.auth.backends.ModelBackend',
            'guardian.backends.ObjectPermissionBackend',
        ),
        AUTH_STRATEGY='READ_ONLY',
        OPEN_URLS=[],
        TARGET_PERMISSIONS_ONLY=True,
        # tom_common.urls mounts this app's URLs automatically via the
        # include_url_paths() integration point in apps.py.
        ROOT_URLCONF='tom_common.urls',
        STATIC_URL='/static/',
        STATIC_ROOT=os.path.join(BASE_DIR, '_static'),
        MEDIA_ROOT=os.path.join(BASE_DIR, 'data'),
        MEDIA_URL='/data/',
        CRISPY_ALLOWED_TEMPLATE_PACKS='bootstrap5',
        CRISPY_TEMPLATE_PACK='bootstrap5',
        # The API tests read response.json()['count'], which exists only with
        # pagination enabled.
        REST_FRAMEWORK={
            'DEFAULT_PERMISSION_CLASSES': [],
            'TEST_REQUEST_DEFAULT_FORMAT': 'json',
            'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
            'PAGE_SIZE': 100
        },
        # Settings this app reads at runtime.
        HERMES_API_URL=os.getenv('HERMES_API_URL', 'https://hermes-dev.lco.global'),
        SAVE_TEST_ALERTS=True,
        FACILITIES={
            'LCO': {
                'portal_url': 'https://observe.lco.global',
                'api_key': '',
            },
        },
        TOM_FACILITY_CLASSES=[
            'tom_observations.facilities.lco_redirect.LCORedirectFacility',
            'tom_observations.facilities.gemini.GEMFacility',
            'tom_observations.facilities.lt.LTFacility'
        ]
    )
    django.setup()

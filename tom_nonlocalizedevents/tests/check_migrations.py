#!/usr/bin/env python
# check_migrations.py
#
# Fail (non-zero exit) if the app's models have changes not captured by a
# migration. Run by CI on every push/PR.
from django.core.management import call_command

from boot_django import boot_django, APP_NAME  # noqa

boot_django()
print(f'checking migrations for {APP_NAME}')
call_command('makemigrations', APP_NAME, '--check')

#!/usr/bin/env python
# django_shell.py
#
# Open a shell_plus session against the test-harness settings.
from django.core.management import call_command

from boot_django import boot_django

boot_django()
call_command('shell_plus')

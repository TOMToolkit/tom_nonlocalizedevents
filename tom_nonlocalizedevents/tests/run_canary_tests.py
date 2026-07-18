#!/usr/bin/env python
# run_canary_tests.py
#
# Run only the tests tagged 'canary' (tests that hit live services).
from django.core.management import call_command

from boot_django import boot_django, APP_NAME  # noqa

boot_django()
print(f'running canary tests for {APP_NAME}')
call_command('test', APP_NAME, '--tag=canary', verbosity=2)

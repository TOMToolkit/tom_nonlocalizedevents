#!/usr/bin/env python
# run_tests.py
#
# Run the non-canary test suite. Invoke from the repository root:
#     python tom_nonlocalizedevents/tests/run_tests.py
# Extra arguments are passed through to `manage.py test` (e.g. a dotted test
# label or -v). Requires a reachable postgres (see boot_django.py DATABASES).
import sys

from django.core.management import call_command

from boot_django import boot_django, APP_NAME  # noqa

boot_django()
print(f'running tests for {APP_NAME}')
call_command('test', *sys.argv[1:], '--exclude-tag=canary', verbosity=2)

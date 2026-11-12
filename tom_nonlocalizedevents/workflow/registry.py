"""The workflow steps a TOM declares in ``NONLOCALIZEDEVENTS_WORKFLOW_STEPS``, and the list column they add.

A workflow step is a page about one event. The setting is a list of dictionaries::

    NONLOCALIZEDEVENTS_WORKFLOW_STEPS = [
        {'label': 'Stub',                                   # the link in the header and the column heading
         'url_name': 'nonlocalizedevents:stub',             # a URL name whose pattern takes event_id
         'cell_text': 'tom_nonlocalizedevents.workflow.steps.stub.cell_text'},   # optional: (event) -> str
        {'label': 'Plans',
         'url_name': 'custom_code:plans',                    # a TOM's own page is declared the same way
         'cell_text': 'custom_code.workflow.steps.plans.cell_text'},
    ]

NOTES:
 1. Overview is always the first link and never a column.
 2. The order of the NONLOCALIZEDEVENTS_WORKFLOW_STEPS list is the display order.
 3. The NONLOCALIZEDEVENTS_WORKFLOW_STEPS is present in settings.py, then that dictionary
    replaces the DEFAULT_WORKFLOW_STEPS defined here.
 4. Errors in an entry raise ``ImproperlyConfigured``, naming the entry.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.module_loading import import_string
from django.utils.text import slugify

import django_tables2 as tables

from tom_nonlocalizedevents.models import NonLocalizedEvent

# the workflow steps used when settings.py does not define NONLOCALIZEDEVENTS_WORKFLOW_STEPS
DEFAULT_WORKFLOW_STEPS = [
    {'label': 'Candidates',
     'url_name': 'nonlocalizedevents:candidates',
     'cell_text': 'tom_nonlocalizedevents.workflow.steps.candidates.views.cell_text'},
    {'label': 'Stub',  # column heading in EventList
     'url_name': 'nonlocalizedevents:stub',  # urlpattern URL name for View
     'cell_text': 'tom_nonlocalizedevents.workflow.steps.stub.cell_text'},  # callable, returns row value
]


class WorkflowStep(NamedTuple):
    """This is the operational form of a workflow step, with actual callable functions
    (vs. dotted-string paths). The `_build_workflow_step()` function builds WorkflowStep instances
    from the dictionary descriptions in NONLOCALIZEDEVENTS_WORKFLOW_STEPS/DEFAULT_WORKFLOW_STEPS.
    The URL name stays a string; url() reverses it for each event.
    """
    column_name: str  # the name of this workflow step's column on the event list, derived from the label
    label: str
    url_name: str
    cell_text: Callable[[NonLocalizedEvent], str]

    def url(self, event: NonLocalizedEvent) -> str:
        """Return the URL of this workflow step's page for ``event``.

        Called for each event by workflow_step_links() (the header) and WorkflowStepColumn.render() (the event
        list). The URL contains the event id, which is why the URL name is reversed here, per event.
        """
        try:
            return reverse(self.url_name, args=(event.event_id,))
        except NoReverseMatch as error:
            raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS entry {self.label!r}: {self.url_name!r} '
                                       f'does not reverse with an event_id ({error})') from error


def _import_callable(entry: dict[str, Any], key: str) -> Callable[[NonLocalizedEvent], str]:
    try:
        imported_callable = import_string(entry[key])
    except ImportError as error:
        raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS entry {entry!r}: {key!r} cannot be '
                                   f'imported ({error})') from error
    if not callable(imported_callable):
        raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS entry {entry!r}: {key!r} is not callable')
    return imported_callable


def _build_workflow_step(entry: dict[str, Any]) -> WorkflowStep:
    """Build a workflow step from the dictionary specification.
    """
    # validate the dictionary description of the workflow step
    if not isinstance(entry, dict) or not entry.get('label'):
        raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS entry {entry!r} needs a label')
    if not entry.get('url_name'):
        raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS entry {entry!r} needs a url_name')

    label = str(entry['label'])
    if 'cell_text' in entry:
        cell_text = _import_callable(entry, 'cell_text')
    else:
        def cell_text(event: NonLocalizedEvent) -> str:
            return label

    # now create the WorkflowStep from the validated and imported dictionary values
    workflow_step = WorkflowStep(
        column_name=slugify(label).replace('-', '_') or 'workflow_step',
        label=label,
        url_name=str(entry['url_name']),
        cell_text=cell_text)
    return workflow_step


@lru_cache(maxsize=1)
def get_workflow_steps() -> list[WorkflowStep]:
    """Return the configured workflow steps, built and cached until the setting changes.
    """

    # get the entries from settings.py (or use defaults if no settings)
    entries = getattr(settings, 'NONLOCALIZEDEVENTS_WORKFLOW_STEPS', DEFAULT_WORKFLOW_STEPS)
    # do some validation
    if not isinstance(entries, (list, tuple)):
        raise ImproperlyConfigured('NONLOCALIZEDEVENTS_WORKFLOW_STEPS must be a list of dictionaries')

    # create a WorkflowStep instance from each entry
    workflow_steps = [_build_workflow_step(entry) for entry in entries]
    column_names = [workflow_step.column_name for workflow_step in workflow_steps]

    # make sure there are no duplicate workflow step labels (django-tables2 needs distinct column names, and two
    # labels differing only in case or punctuation would collide, so the check is on the column names)
    if len(set(column_names)) != len(column_names):
        raise ImproperlyConfigured(f'NONLOCALIZEDEVENTS_WORKFLOW_STEPS labels must be distinct, got {column_names}')
    return workflow_steps


@receiver(setting_changed)
def _clear_workflow_steps(sender: Any, setting: str, **kwargs: Any) -> None:
    if setting == 'NONLOCALIZEDEVENTS_WORKFLOW_STEPS':
        get_workflow_steps.cache_clear()


def workflow_step_links(event: NonLocalizedEvent, request_path: str) -> list[dict[str, Any]]:
    """Return the header's workflow step links for ``event``: label, URL, and whether the request is on that
    workflow step's page."""
    links = []
    for workflow_step in get_workflow_steps():
        url = workflow_step.url(event)
        links.append({'label': workflow_step.label, 'url': url, 'current': urlsplit(url).path == request_path})
    return links


class WorkflowStepColumn(tables.Column):
    """The event list column of one workflow step: its cell text, linked to its page for the row's event."""

    def __init__(self, workflow_step: WorkflowStep, **kwargs: Any) -> None:
        self.workflow_step = workflow_step
        kwargs.setdefault('verbose_name', workflow_step.label)
        kwargs.setdefault('orderable', False)
        kwargs.setdefault('empty_values', ())
        super().__init__(**kwargs)

    def render(self, record: NonLocalizedEvent) -> str:
        # hx-boost="false" for the same reason as the event-id link: the table carries hx-boost for sorting
        return format_html('<a href="{}" hx-boost="false">{}</a>',
                           self.workflow_step.url(record), self.workflow_step.cell_text(record))


def workflow_step_columns() -> list[tuple[str, tables.Column]]:
    """Return ``extra_columns`` for the event list: one column per configured workflow step, in settings order."""
    return [(workflow_step.column_name, WorkflowStepColumn(workflow_step)) for workflow_step in get_workflow_steps()]

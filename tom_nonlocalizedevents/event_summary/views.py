"""This views.py module implements a mix-in class (EventSummaryMixin) for Views that
include the event summary. (The event_summary sub-package exists just to keep this and
other event_summary-specific modules together).

The EventSummaryMixin puts the event summary on a page when its View subclasses it
(i.e "mixes it in"): setup() resolves the event and the selected alert from the request,
get_context_data() adds the summary context, and get_template_names() answers an alert-tab
click with the update fragment instead of the full page.
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from tom_nonlocalizedevents.event_summary.context import (EVENT_HEADER_ID, event_summary_context, ordered_sequences,
                                                          resolve_event, select_sequence)
from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent

EVENT_SUMMARY_UPDATE_TEMPLATE = 'tom_nonlocalizedevents/partials/event_summary_update.html'


def htmx_target_element_id(request: HttpRequest) -> str | None:
    """Return the id of the HTML element an htmx request will swap its response into, or None for other requests.

    That element, whose id is returned, is htmx's "target" (i.e. the ``hx-target`` attribute,
    sent as the ``HX-Target`` request header).
    """
    # get the HTML element targeted by the htmx request (request.htmx.target)
    hx_target_request_header = getattr(getattr(request, 'htmx', None), 'target', None)
    if not hx_target_request_header:
        return None

    # htmx 4 sends tag#id (div#event-header); htmx 1.9 sends the bare id
    target_element_id = hx_target_request_header.split('#', 1)[-1]  # remove anything that isn't the bare element id
    return target_element_id


def is_history_restore_request(request: HttpRequest) -> bool:
    return bool(getattr(getattr(request, 'htmx', None), 'history_restore_request', False))


class EventSummaryMixin:
    """Put the event summary on a view about one event and answer its alert-tab clicks.

    ``setup()`` resolves the event from the URL (``event_id`` or ``pk``), loads its alerts and selects the
    one to show (``?sequence=``, latest by default). ``get_context_data()`` adds the summary context. A click
    on an alert tab is an htmx request targeting the header; it is answered with the update fragment: the header,
    the sky-map data and, when the page template defines a ``workflow-step-content`` partial, the workflow step's
    content, the last two out of band. A history-restore request gets the full page. Place this mixin before
    ``HTMXTableViewMixin`` in a view's bases so these two cases are decided before the table logic.
    """
    event: NonLocalizedEvent
    sequences: list[EventSequence]
    selected_sequence: EventSequence | None
    update_template_name = EVENT_SUMMARY_UPDATE_TEMPLATE
    # False on a view whose page draws its own sky map, so the summary leaves its sky map out
    show_sky_map: bool = True
    # Templates searched for the workflow-step-content partial after the page's own: a partial is not visible
    # through {% extends %}, so a page rendered by a template that extends the one defining the partial
    # names the defining template here.
    workflow_step_content_template_names: list[str] = []

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.event = resolve_event(**kwargs)
        self.sequences = ordered_sequences(self.event)
        self.selected_sequence = select_sequence(self.sequences, request.GET.get('sequence'))

    def is_alert_tab_request(self) -> bool:
        return htmx_target_element_id(self.request) == EVENT_HEADER_ID

    def get_page_template_names(self) -> list[str]:
        """The templates of the full page; views override this when the next class would answer htmx requests
        with a partial (``HTMXTableViewMixin`` does)."""
        return super().get_template_names()

    def get_template_names(self) -> list[str]:
        if self.is_alert_tab_request():
            return [self.update_template_name]
        if is_history_restore_request(self.request) or not getattr(self.request, 'htmx', False):
            return self.get_page_template_names()
        # another htmx request (sorting or filtering a table): the next class decides, with its partial
        return super().get_template_names()

    def workflow_step_content_template_name(self) -> str | None:
        """The ``workflow-step-content`` partial of the page template, or of one of the templates named in
        ``workflow_step_content_template_names``; None when no template defines one."""
        for template_name in [*self.get_page_template_names(), *self.workflow_step_content_template_names]:
            partial_name = f'{template_name}#workflow-step-content'
            try:
                get_template(partial_name)
            except TemplateDoesNotExist:
                continue
            return partial_name
        return None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(event_summary_context(self.request, self.event, self.selected_sequence,
                                             sky_map=self.show_sky_map, sequences=self.sequences))
        if self.is_alert_tab_request():
            context['workflow_step_content_template'] = self.workflow_step_content_template_name()
        return context

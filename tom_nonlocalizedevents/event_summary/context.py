"""Helper functions for the template context of the event summary: the event, its alerts, the selected alert
and what it shows.
"""
from __future__ import annotations

import logging
from typing import Any

from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404

from tom_nonlocalizedevents.event_summary.header import header_attributes
from tom_nonlocalizedevents.event_summary.sky_map import sky_map_layers
from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent
from tom_nonlocalizedevents.services.alerts import parse_alert

logger = logging.getLogger(__name__)

# The id of the element an alert-tab click replaces; the header partial, the mixin and the tests share it.
EVENT_HEADER_ID = 'event-header'
# The id of the json_script element the sky map's JavaScript reads; it is replaced out of band on a tab click.
SKY_MAP_DATA_ID = 'event-sky-map-data'


def resolve_event(event: NonLocalizedEvent | str | None = None, **kwargs: Any) -> NonLocalizedEvent:
    """Return the event given as an object, an event id, or URL keyword arguments (``event_id`` or ``pk``).

    Raises:
        Http404: When no event matches, or nothing identifies one.
    """
    if isinstance(event, NonLocalizedEvent):
        return event
    if event is not None:
        return get_object_or_404(NonLocalizedEvent, event_id=event)
    if 'pk' in kwargs:
        return get_object_or_404(NonLocalizedEvent, pk=kwargs['pk'])
    if 'event_id' in kwargs:
        return get_object_or_404(NonLocalizedEvent, event_id=kwargs['event_id'])
    raise Http404('No pk or event_id provided')


def ordered_sequences(event: NonLocalizedEvent) -> list[EventSequence]:
    """Return the event's alerts in sequence order with the relations the summary reads, in one query."""
    return list(event.sequences.select_related('localization', 'external_coincidence',
                                               'external_coincidence__localization').order_by('sequence_id'))


def select_sequence(sequences: list[EventSequence], requested: str | None) -> EventSequence | None:
    """Return the alert a page shows: the one whose ``sequence_id`` was requested, else the latest.

    An unknown or non-integer request falls back to the latest alert (logged).
    """
    if not sequences:
        return None
    if requested not in (None, ''):
        try:
            requested_id = int(requested)
        except ValueError:
            logger.info(f'ignoring non-integer sequence request {requested!r}')
        else:
            for sequence in sequences:
                if sequence.sequence_id == requested_id:
                    return sequence

            # we didn't find the requested sequence_id
            logger.info(f'no sequence {requested_id} for {sequences[0].nonlocalizedevent.event_id}; showing the latest')
    return sequences[-1]  # fall back to the latest


def event_summary_context(request: HttpRequest, event: NonLocalizedEvent,
                          selected_sequence: EventSequence | None = None, sky_map: bool = True,
                          sequences: list[EventSequence] | None = None) -> dict[str, Any]:
    """Build the context for the summary partials to render.

    Args:
        request: The current request; ``?sequence=`` selects the alert when ``selected_sequence`` is not given.
        event: The event the page is about.
        selected_sequence: The alert to show; by default the requested or latest one.
        sky_map: Whether the sky map is part of the summary (a page with its own sky map passes False).
        sequences: The event's alerts if the caller already loaded them.
    """
    # make sure we have all the pieces needed to create the context
    if sequences is None:
        sequences = ordered_sequences(event)
    if selected_sequence is None:
        selected_sequence = select_sequence(sequences, request.GET.get('sequence'))

    context = {
        'event': event,
        'nonlocalizedevent': event,
        'sequences': sequences,
        'selected_sequence': selected_sequence,
        'selected_alert': parse_alert(selected_sequence) if selected_sequence else None,
        'header_attributes': header_attributes(selected_sequence) if selected_sequence else [],
        'event_header_id': EVENT_HEADER_ID,
        # the alert tabs request the page's own URL with ?sequence=; the view (an EventSummaryMixin
        # subclass) answers with the update fragment
        'tab_request_path': request.path,
        'show_sky_map': sky_map,
        'sky_map_data_id': SKY_MAP_DATA_ID,
        'sky_map_layers': sky_map_layers(event, selected_sequence).model_dump(mode='json') if sky_map else None,
        'workflow_steps': [],
    }
    return context

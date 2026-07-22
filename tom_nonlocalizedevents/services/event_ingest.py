"""Core event-ingestion orchestration.

This module ingests a NonLocalizedEvent -- the event itself, not the alert
about it -- from already-parsed data, regardless of source (live IGWN alert
stream, GraceDB back-fill, or any future adapter). Source-specific adapters
(e.g. services.gracedb) build the standardized ``alert_data`` dictionary and
call :func:`ingest_igwn_event_from_data`.
"""
import logging
import traceback
import uuid as uuid_module
from typing import Any

import requests

from tom_nonlocalizedevents.healpix_utils import create_localization_for_skymap
from tom_nonlocalizedevents.models import EventLocalization, EventSequence, ExternalCoincidence, NonLocalizedEvent

logger = logging.getLogger(__name__)

# how long to wait on skymap downloads before giving up (seconds)
REQUESTS_TIMEOUT = 60


def ingest_igwn_event_from_data(alert_data: dict[str, Any],
                                ingestor_source: str = 'unknown',
                                hermes_message_id: uuid_module.UUID | None = None
                                ) -> tuple[NonLocalizedEvent | None, EventSequence | None]:
    """Ingest a non-localized event from a standardized data dictionary.

    Encapsulates the full logic for creating or updating a NonLocalizedEvent
    and its related models (EventLocalization, ExternalCoincidence,
    EventSequence). It is the single point of entry for event ingestion,
    whether from a live alert stream, a cached alert, or a manual process.

    It handles: retractions; idempotent creation of the NonLocalizedEvent;
    skymap processing from either raw bytes embedded in the alert (the live
    IGWN kafka stream embeds them at ``event.skymap`` /
    ``external_coinc.combined_skymap``) or from URLs in the ``urls``
    dictionary; and creation of the localization/coincidence/sequence records.
    Embedded skymap bytes are popped out of the alert so they never land in
    the EventSequence.details JSON.

    The expected ``alert_data`` structure is::

        {
            'superevent_id': str, 'alert_type': str, 'sequence_num': int,
            'urls': {'skymap': 'http://...', 'combined_skymap': 'http://...'},
            'event': { ... event details, optionally 'skymap': bytes ... },
            'external_coinc': { ... optionally 'combined_skymap': bytes ... }
        }

    Args:
        alert_data: A dictionary containing the standardized event data.
        ingestor_source: Identifies the source of the ingestion (e.g. 'hop',
            'gracedb'); stored on the EventSequence.
        hermes_message_id: The Hermes message UUID for this alert (the hop
            '_id' kafka header), stored on the EventSequence when known.

    Returns:
        A tuple of the created/updated NonLocalizedEvent and EventSequence,
        (nonlocalizedevent, None) for retractions, or (None, None) if the
        alert is malformed.
    """
    event_id = alert_data.get('superevent_id')
    if not event_id:
        logger.warning(f"Malformed alert data: missing 'superevent_id'. Data: {alert_data}")
        return None, None

    # Handle retractions first, as they are a terminal action for an event.
    if alert_data.get('alert_type', '').upper() == 'RETRACTION':
        nonlocalizedevent, _ = NonLocalizedEvent.objects.update_or_create(
            event_id=event_id,
            event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
            defaults={'state': NonLocalizedEvent.NonLocalizedEventState.RETRACTED}
        )
        logger.info(f"Event {event_id} has been retracted.")
        return nonlocalizedevent, None

    # Ensure the parent NonLocalizedEvent exists.
    nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
        event_id=event_id,
        event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
    )
    if nle_created:
        logger.info(f"Created new NonLocalizedEvent: {event_id}")

    event_details = alert_data.get('event') or {}
    external_coinc_details = alert_data.get('external_coinc') or {}
    urls = alert_data.get('urls') or {}

    # Pop any embedded skymap bytes out of the details dictionaries BEFORE the
    # details are stored: the live IGWN stream embeds multi-megabyte skymaps
    # in the alert payload, and they must not end up in the details JSON.
    skymap_bytes = event_details.pop('skymap', None)
    combined_skymap_bytes = external_coinc_details.pop('combined_skymap', None)

    localization = _process_skymap(
        nonlocalizedevent=nonlocalizedevent,
        skymap_bytes=skymap_bytes,
        skymap_url=urls.get('skymap'),
        pipeline=event_details.get('pipeline', ''),
        is_combined=False
    )

    combined_localization = _process_skymap(
        nonlocalizedevent=nonlocalizedevent,
        skymap_bytes=combined_skymap_bytes,
        skymap_url=urls.get('combined_skymap'),
        pipeline=event_details.get('pipeline', ''),
        is_combined=True
    )
    external_coincidence = None  # stays None when the alert has no combined skymap
    if combined_localization:
        external_coincidence, _ = ExternalCoincidence.objects.get_or_create(
            localization=combined_localization,
            defaults={'details': external_coinc_details}
        )

    sequence_id = alert_data.get('sequence_num')
    if sequence_id is None:
        # Fallback for streams that don't provide a sequence number.
        sequence_id = nonlocalizedevent.sequences.count() + 1

    sequence_defaults = {
        'localization': localization,
        'external_coincidence': external_coincidence,
        'details': event_details,
        'event_subtype': alert_data.get('alert_type'),
        'ingestor_source': ingestor_source
    }
    if hermes_message_id is not None:
        # only set when known: a UUID-less update of an existing sequence
        # (e.g. a GraceDB back-fill refreshing a stream-ingested one) must
        # not null out the stored message id
        sequence_defaults['hermes_message_id'] = hermes_message_id
    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        sequence_id=sequence_id,
        defaults=sequence_defaults
    )

    if es_created and localization is None:
        logger.warning(
            f'Created EventSequence {event_sequence.id} for {nonlocalizedevent.event_id} without an EventLocalization.'
        )

    return nonlocalizedevent, event_sequence


def _process_skymap(nonlocalizedevent: NonLocalizedEvent, skymap_bytes: bytes | None,
                    skymap_url: str | None, pipeline: str, is_combined: bool) -> EventLocalization | None:
    """Create an EventLocalization from raw skymap bytes, or by fetching a skymap URL.

    Raw bytes take precedence; the URL is fetched only when no bytes were
    provided. Returns None (with the error logged) if neither source is
    available or processing fails -- ingestion of the sequence proceeds
    without a localization in that case.
    """
    if not skymap_bytes and not skymap_url:
        return None

    try:
        if not skymap_bytes:  # no embedded bytes -- fetch from the URL
            response = requests.get(skymap_url, timeout=REQUESTS_TIMEOUT)
            response.raise_for_status()
            skymap_bytes = response.content

        return create_localization_for_skymap(
            nonlocalizedevent=nonlocalizedevent, skymap_bytes=skymap_bytes,
            skymap_url=skymap_url or '', pipeline=pipeline, is_combined=is_combined
        )
    except Exception as e:
        skymap_source = skymap_url or "provided bytes"
        logger.error(
            f"Failed to retrieve/process {'combined ' if is_combined else ''}localization "
            f"for {nonlocalizedevent.event_id} from {skymap_source}. Exception: {e}"
        )
        logger.error(traceback.format_exc())
        return None

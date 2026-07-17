import logging
import traceback

import requests
from typing import Dict, Any, List, Tuple, Optional

from django.db import transaction
from tom_nonlocalizedevents.healpix_utils import create_localization_for_skymap
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, ExternalCoincidence, EventLocalization

logger = logging.getLogger(__name__)


def ingest_igwn_event_from_data(alert_data: Dict[str, Any], ingestor_source: str = 'unknown') -> Tuple[Optional[NonLocalizedEvent], Optional[EventSequence]]:
    """
    A centralized service function to ingest a non-localized event from a data dictionary.

    This function encapsulates the full logic for creating or updating a NonLocalizedEvent
    and its related models (EventLocalization, ExternalCoincidence, EventSequence) from a
    standardized data structure. It is designed to be the single point of entry for event
    ingestion, whether from a live alert stream, a cached alert, or a manual process.

    It handles:
    - Retractions
    - Idempotent creation of NonLocalizedEvent
    - Processing of primary and combined skymaps by fetching from URLs specified in the `urls` dictionary.
    - Creation of EventLocalization, ExternalCoincidence, and EventSequence records

    The expected `alert_data` structure is:
    {
        'superevent_id': str, 'alert_type': str, 'sequence_num': int,
        'urls': {'skymap': 'http://...', 'combined_skymap': 'http://...'},
        'event': { ... event details ... }, 'external_coinc': { ... coinc details ... }
    }
    :param alert_data: A dictionary containing the standardized event data.
    :param ingestor_source: A string identifying the source of the ingestion (e.g., 'hop', 'gracedb').
    :return: A tuple containing the created/updated NonLocalizedEvent and EventSequence,
             or (None, None) if ingestion fails or is not applicable.
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

    event_details = alert_data.get('event', {})
    external_coinc_details = alert_data.get('external_coinc', {})

    # --- Process Primary Localization ---
    localization = _process_skymap(
        nonlocalizedevent=nonlocalizedevent,
        skymap_bytes=None,  # This service fetches from URL, it does not expect raw bytes.
        skymap_url=alert_data.get('urls', {}).get('skymap'),
        pipeline=event_details.get('pipeline', ''),
        is_combined=False
    )

    # --- Process Combined Localization (External Coincidence) ---
    combined_localization = _process_skymap(
        nonlocalizedevent=nonlocalizedevent,
        skymap_bytes=None,  # This service fetches from URL, it does not expect raw bytes.
        skymap_url=alert_data.get('urls', {}).get('combined_skymap'),
        pipeline=event_details.get('pipeline', ''),
        is_combined=True
    )
    if combined_localization:
        external_coincidence, _ = ExternalCoincidence.objects.get_or_create(
            localization=combined_localization,
            defaults={'details': external_coinc_details}
        )

    # --- Create the Event Sequence ---
    sequence_id = alert_data.get('sequence_num')
    if sequence_id is None:
        # Fallback for streams that don't provide a sequence number.
        sequence_id = nonlocalizedevent.sequences.count() + 1

    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        sequence_id=sequence_id,
        defaults={
            'localization': localization,
            'external_coincidence': external_coincidence,
            'details': event_details,
            'event_subtype': alert_data.get('alert_type'),
            'ingestor_source': ingestor_source
        }
    )

    if es_created and localization is None:
        logger.warning(
            f'Created EventSequence {event_sequence.id} for {nonlocalizedevent.event_id} without an EventLocalization.'
        )

    return nonlocalizedevent, event_sequence


def _process_skymap(nonlocalizedevent: NonLocalizedEvent, skymap_bytes: Optional[bytes],
                    skymap_url: Optional[str], pipeline: str, is_combined: bool) -> Optional[EventLocalization]:
    """Helper function to process a skymap, either from raw bytes or a URL."""
    if not skymap_bytes and not skymap_url:
        return None

    try:
        if not skymap_bytes:  # If bytes aren't provided, fetch from URL
            response = requests.get(skymap_url)
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

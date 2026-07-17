import logging
from typing import Dict, Any, Optional, Tuple

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence
from .base import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)


def ingest_event_from_hermes_message(message: Dict[str, Any]) -> Tuple[Optional[NonLocalizedEvent], Optional[EventSequence]]:
    """
    Adapter function to ingest a non-localized event from a Hermes/Hopskotch message.

    This function acts as a bridge between the raw message format from the
    alert stream and the centralized ingestion service. It extracts the necessary
    data, transforms it into a standardized dictionary, and then passes it to
    `ingest_igwn_event_from_data` for processing.

    :param message: The raw dictionary-like message from the Hermes broker.
    :return: A tuple containing the created/updated NonLocalizedEvent and EventSequence,
             or (None, None) if ingestion fails.
    """
    alert_data = _build_hermes_alert_data(message)
    if not alert_data:
        logger.warning(f"Malformed Hermes message, cannot create nonlocalizedevent. Message = {message}")
        return None, None

    # The 'hop' source is specific to events ingested via Hermes/Hopskotch.
    return ingest_igwn_event_from_data(alert_data, ingestor_source='hop')


def _build_hermes_alert_data(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transforms a raw Hermes message into the standardized alert data dictionary.

    This helper function is responsible only for data transformation. It performs
    no I/O or database operations.

    :param message: The raw dictionary-like message from the Hermes broker.
    :return: A standardized dictionary for the ingestion service, or None if the
             message is invalid.
    """
    data = message.get('message', {}).get('data', {})
    event_id = data.get('superevent_id')
    if not event_id:
        return None

    # This dictionary structure is the standard format expected by the base ingestion service.
    # It contains all necessary information, including URLs to skymaps, which the
    # base service will fetch.
    return {
        'superevent_id': event_id,
        'alert_type': data.get('alert_type', ''),
        'sequence_num': data.get('sequence_num'),
        'urls': data.get('urls', {}),
        'event': data.get('event'),
        'external_coinc': data.get('external_coinc')
    }
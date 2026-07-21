import logging
from typing import Any

from django.conf import settings

from hop.io import Metadata
from hop.models import JSONBlob

from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent
from tom_nonlocalizedevents.services.event_ingest import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)


def handle_igwn_message(message: JSONBlob, metadata: Metadata | None = None,
                        **kwargs: Any) -> tuple[NonLocalizedEvent | None, EventSequence | None]:
    """Ingest an IGWN GWAlert from the hop/Kafka stream.

    Unwraps the hop message and hands the alert to the source-independent
    ingestion service; the only logic living here is what is stream-specific
    (message unwrapping and the test-alert gate).

    Args:
        message: The hop message; its ``content`` holds the GWAlert dict.
        metadata: Stream metadata (unused). Named rather than positional-only
            because released tom_alertstreams passes it positionally while the
            2.0-era convention passes it by keyword.
        **kwargs: Absorbs the extra context the tom_alertstreams 2.0-era
            unified convention passes to every handler (``alert_stream=``,
            ``topic=``, ...).

    Returns:
        The (nonlocalizedevent, event_sequence) tuple from the ingestion
        service; (nonlocalizedevent, None) for retractions; (None, None) for
        malformed alerts or test alerts skipped per SAVE_TEST_ALERTS.
    """
    # hop-client packed the alert as a single-element list through 0.6; newer
    # releases deliver the alert dict directly. Customers run both, so
    # tolerate both shapes (issue #77).
    alert = message.content
    if isinstance(alert, (list, tuple)):
        alert = alert[0]
    logger.info(f"Handling igwn alert for event {alert.get('superevent_id')}")

    # Only store test alerts (MS... superevent ids) if we are configured to do so.
    # TODO: consider moving SAVE_TEST_ALERTS from the top level of settings
    #  to the ALERT_STREAMS list of stream configuration dictionaries.
    if alert.get('superevent_id', '').startswith('M') and not getattr(settings, 'SAVE_TEST_ALERTS', True):
        return None, None

    # All ingestion logic (retractions, embedded skymaps, sequence numbering
    # and creation) lives in the source-independent service layer.
    return ingest_igwn_event_from_data(alert, ingestor_source='hop')

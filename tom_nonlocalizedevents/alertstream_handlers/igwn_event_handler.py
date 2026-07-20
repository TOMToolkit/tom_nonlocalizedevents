import logging

from django.conf import settings

from hop.io import Metadata
from hop.models import JSONBlob

from tom_nonlocalizedevents.services.event_ingest import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)


def handle_igwn_message(message: JSONBlob, metadata: Metadata):
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

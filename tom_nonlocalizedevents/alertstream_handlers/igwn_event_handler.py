import logging
import traceback

from hop.io import Metadata
from hop.models import JSONBlob
from django.conf import settings

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, ExternalCoincidence
from tom_nonlocalizedevents.healpix_utils import create_localization_for_skymap

logger = logging.getLogger(__name__)


def get_sequence_number(superevent_id: str) -> int:
    """ This returns the sequence number of the next sequence for a superevent_id. This is a hack to get
        around the fact that IGWN GWAlerts no longer tell you the sequence number within them.
    """
    try:
        nle = NonLocalizedEvent.objects.get(event_id=superevent_id)
        return nle.sequences.count() + 1
    except NonLocalizedEvent.DoesNotExist:
        return 1  # The nonlocalizedevent doesnt exist in our system yet, so this must be the first sequence


def handle_igwn_message(message: JSONBlob, metadata: Metadata):
    """

    Here what the logged message looks like:

    {'alert_type': 'RETRACTION',
     'time_created': '2025-07-04T03:03:41Z',
      'superevent_id': 'MS250704c',
      'event': None,
      'external_coinc': None,
      'urls': {
        'gracedb': 'https://gracedb.ligo.org/supe{'alert_type': 'RETRACTION',
        'time_created': '2025-07-04T03:03:41Z',
        'superevent_id': 'MS250704c',
        'event': None,
        'external_coinc': None,
        'urls': {
          'gracedb': 'https://gracedb.ligo.org/superevents/MS250704c/view/'}
        }revents/MS250704c/view/'}
    }

    """
    logger.debug(f'handle_igwn_message: message: {message}')
    logger.debug(f'handle_igwn_message: message.content: {message.content}')

    try:
        alert = message.content[0]
    except KeyError as err:
        logger.error(f"messsage.content[0] isn't working here type(message): {type(message)}")
        logger.error(f"messsage.content[0] isn't working here: {err}")

    alert = message
    # logger.info(f"Handling igwn alert for event {alert.get('superevent_id')}")

    # Only store test alerts if we are configured to do so
    # TODO: consider moving SAVE_TEST_ALERTS from top level of settings
    #  to the ALERT_STREAMS list of stream configuration dictionaries.
    try:
        save_test_alerts = settings.SAVE_TEST_ALERTS
    except AttributeError as err:
        save_test_alerts = True
        logger.warning(f'{err} Using {save_test_alerts} as default value.')
    if alert.get('superevent_id', '').startswith('M') and not save_test_alerts:
        return None, None

    # Handle RETRACTION and exit
    if alert.get('alert_type', '') == 'RETRACTION':
        nonlocalizedevent, nle_created = NonLocalizedEvent.objects.update_or_create(
            event_id=alert['superevent_id'],
            event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
            defaults={'state': NonLocalizedEvent.NonLocalizedEventState.RETRACTED}
        )
        return nonlocalizedevent, None

    # Get (or create) the NonLocalizedEvent for further processing
    nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
        event_id=alert['superevent_id'],
        event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
    )
    if nle_created:
        logger.info(f"Ingested a new GW event with id {alert['superevent_id']} from IGWN alertstream")

    # Here we do a bit of pre-processing for IGWN alerts in order to be able to remove the skymap before saving the file
    localization = None
    pipeline = alert.get('event', {}).get('pipeline', '')
    if alert.get('event'):
        skymap_bytes = alert['event'].pop('skymap')
        if skymap_bytes:
            try:
                localization = create_localization_for_skymap(
                    nonlocalizedevent=nonlocalizedevent, skymap_bytes=skymap_bytes, pipeline=pipeline
                )
            except Exception as e:
                localization = None
                logger.error(f'Could not create EventLocalization for event: {alert["superevent_id"]}. Exception: {e}')
                logger.error(traceback.format_exc())

    external_coincidence = None
    if alert.get('external_coinc', {}):
        combined_skymap_bytes = alert['external_coinc'].pop('combined_skymap')
        if combined_skymap_bytes:
            try:
                combined_localization = create_localization_for_skymap(
                    nonlocalizedevent=nonlocalizedevent, skymap_bytes=combined_skymap_bytes,
                    is_combined=True, pipeline=pipeline
                )
                external_coincidence, _ = ExternalCoincidence.objects.get_or_create(
                    localization=combined_localization, details=alert.get('external_coinc')
                )
            except Exception as e:
                external_coincidence = None
                logger.error(
                    f'Could not create combined EventLocalization for event: {alert["superevent_id"]}. Exception: {e}'
                )
                logger.error(traceback.format_exc())

    logger.info(f"Storing igwn alert: {alert}")

    # Now ingest the sequence for that event
    sequence_number = get_sequence_number(alert['superevent_id'])
    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        localization=localization,
        external_coincidence=external_coincidence,
        sequence_id=sequence_number,
        details=alert.get('event'),
        defaults={
            'event_subtype': alert.get('alert_type'),
            'ingestor_source': 'hop'
        }
    )
    if es_created and localization is None:
        warning_msg = (
            f'{"Creating" if es_created else "Updating"} EventSequence without EventLocalization:'
            f'{event_sequence} for NonLocalizedEvent: {nonlocalizedevent}'
        )
        logger.warning(warning_msg)

    return nonlocalizedevent, event_sequence

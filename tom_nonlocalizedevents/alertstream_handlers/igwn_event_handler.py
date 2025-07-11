import logging

from hop.io import Metadata
from hop.models import JSONBlob, AvroBlob
from django.conf import settings

from tom_nonlocalizedevents.services import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


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
    logger.debug(f'handle_igwn_message: metadata: {metadata}')
    logger.debug(f'handle_igwn_message: message.content: {message.content}')

    try:
        alert_data = message.content[0]
    except KeyError as err:
        logger.error(f"messsage.content[0] isn't working here type(message): {type(message)}")
        logger.error(f"messsage.content[0] isn't working here: {err}")
        alert_data: AvroBlob = AvroBlob.deserialize(message).content

    # Only store test alerts if we are configured to do so
    try:
        save_test_alerts = settings.SAVE_TEST_ALERTS
    except AttributeError as err:
        save_test_alerts = True
        logger.warning(f'{err} Using {save_test_alerts} as default value.')

    if alert_data.get('superevent_id', '').startswith('M') and not save_test_alerts:
        logger.info(f"Skipping test event: {alert_data.get('superevent_id')}")
        return None, None

    # The service function handles all the logic of creating the event, sequences, and localizations.
    # The raw alert from the stream contains the skymap data as bytes directly in the dictionary,
    # which is what the service function expects.
    nonlocalizedevent, event_sequence = ingest_igwn_event_from_data(alert_data)
    return nonlocalizedevent, event_sequence

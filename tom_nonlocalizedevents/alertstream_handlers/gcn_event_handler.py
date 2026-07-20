''' This class defines a message handler for a tom_alertstreams connection to GW events

'''
import logging
import os
import traceback
import requests
from django.conf import settings

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence
from tom_nonlocalizedevents.healpix_utils import create_localization_for_skymap

logger = logging.getLogger(__name__)


EXPECTED_FIELDS = [
    'trigger_num',
    'sequence_num',
    'notice_type',
    'skymap_fits_url'
]


def extract_all_fields(message):
    parsed_fields = {}
    for line in message.splitlines():
        entry = line.split(':', maxsplit=1)
        if len(entry) > 1:
            if entry[0].strip() == 'COMMENTS' and 'comments' in parsed_fields:
                parsed_fields['comments'] += entry[1].lstrip()
            else:
                parsed_fields[entry[0].strip().lower()] = entry[1].strip()
    return parsed_fields


def get_moc_url_from_skymap_fits_url(skymap_fits_url):
    base, filename = os.path.split(skymap_fits_url)
    # Repair broken skymap filenames given in gcn mock alerts right now
    if filename.endswith('.fit'):
        filename = filename + 's'
    # Replace the non-MOC skymap url provided with the MOC version, but keep the ,# on the end
    filename = filename.replace('LALInference.fits.gz', 'LALInference.multiorder.fits')
    filename = filename.replace('bayestar.fits.gz', 'bayestar.multiorder.fits')
    return os.path.join(base, filename)


def handle_message(message):
    # It receives a bytestring message or a Kafka message in the LIGO GW format
    # fields must be extracted from the message text and stored into in the model
    # It returns the nonlocalizedevent and event sequence ingested or None, None.
    if not isinstance(message, bytes):
        bytes_message = message.value()
    else:
        bytes_message = message
    logger.warning(f"Processing message: {bytes_message.decode('utf-8')}")
    fields = extract_all_fields(bytes_message.decode('utf-8'))
    if not all(field in fields.keys() for field in EXPECTED_FIELDS):
        logger.warning(f"Incoming GW message did not have the expected fields, ignoring it: {fields.keys()}")
        return

    if fields and fields['trigger_num'].startswith('M') and not getattr(settings, 'SAVE_TEST_ALERTS', True):
        return

    if fields:
        nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
            event_id=fields['trigger_num'],
            event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
        )
        if nle_created:
            logger.info(f"Ingested a new GW event with id {fields['trigger_num']} from alertstream")
        # Next attempt to ingest and build the localization of the event
        skymap_url = get_moc_url_from_skymap_fits_url(fields['skymap_fits_url'])
        try:
            skymap_resp = requests.get(skymap_url)
            skymap_resp.raise_for_status()
            localization = create_localization_for_skymap(
                nonlocalizedevent=nonlocalizedevent,
                skymap_bytes=skymap_resp.content,
                skymap_url=skymap_url
            )
        except Exception as e:
            localization = None
            logger.error(
                f"Failed to retrieve and process localization from skymap file at {skymap_url}. Exception: {e}"
            )
            logger.error(traceback.format_exc())

        # Now ingest the sequence for that event
        event_sequence, es_created = EventSequence.objects.update_or_create(
            nonlocalizedevent=nonlocalizedevent,
            localization=localization,
            sequence_id=fields['sequence_num'],
            defaults={
                'event_subtype': fields['notice_type'],
                'details': fields,
                'ingestor_source': 'gcn'
            }
        )
        if es_created and localization is None:
            warning_msg = (f'{"Creating" if es_created else "Updating"} EventSequence without EventLocalization:'
                           f'{event_sequence} for NonLocalizedEvent: {nonlocalizedevent}')
            logger.warning(warning_msg)

        return nonlocalizedevent, event_sequence
    return None, None


def handle_retraction(message):
    # It receives a bytestring message or a Kafka message in the LIGO GW format
    # For a retraction message, we just set the events status to retracted if it exists.
    if not isinstance(message, bytes):
        bytes_message = message.value()
    else:
        bytes_message = message
    # Just need the event_id from the retraction messages
    fields = extract_all_fields(bytes_message.decode('utf-8'))
    if 'trigger_num' not in fields:
        logger.warning("Retraction notice missing 'trigger_num' field, ignoring.")
        return
    # Then set the state to 'RETRACTED' for the event matching that id
    try:
        retracted_event = NonLocalizedEvent.objects.get(event_id=fields['trigger_num'])
        retracted_event.state = NonLocalizedEvent.NonLocalizedEventState.RETRACTED
        retracted_event.save()
        return retracted_event
    except NonLocalizedEvent.DoesNotExist:
        logger.warning((f"Got a Retraction notice for event id {fields['trigger_num']}"
                        f"which does not exist in the database"))
    return None

"""Tests of the Hermes messages service: parsing the archive's messages, collapsing copies, the error cases and
the cache."""
from datetime import datetime, timezone
from urllib.parse import urljoin

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

import requests
import responses
from responses import matchers

from tom_nonlocalizedevents.services.hermes_messages import (HERMES_MESSAGES_LIMIT, HermesMessage, event_start_time,
                                                             hermes_messages_for_event, one_copy_per_title)
from tom_nonlocalizedevents.tests.factories import EventSequenceFactory, NonLocalizedEventFactory

QUERY_URL = urljoin(settings.HERMES_API_URL, '/api/v0/query/')
SERVICE_LOGGER = 'tom_nonlocalizedevents.services.hermes_messages'
ORIGINATOR = 'Isaac McMahon at University of Zürich <isaac.mcmahon@ligo.org>'


def archive_message(uuid, title, timestamp, topic='gcn.circulars', originator=ORIGINATOR, retracted=False):
    """One entry of the archive's ``messages`` list, in the shape Hermes returned on 2026-11-11."""
    return {'metadata': {'topic': topic, 'timestamp': timestamp},
            'annotations': {'con_text_uuid': uuid, 'size': 6392, 'crc32': 1747834340, 'public': True,
                            'con_message_crc32': True, 'title': title, 'sender': 'weave132-a0876889',
                            'media_type': 'application/json', 'file_name': '', 'originator': originator,
                            'retracted': retracted}}


# a cut of the real answer for S250328ae: the DESGW final-epochs circular twice, Subaru twice, one more circular,
# two of the event's IGWN alerts (no title; the uuid is the alert's Hermes message id), and a message a TOM posted
# on its own topic
FINAL_EPOCHS = 'LIGO/Virgo/KAGRA S250328ae: DECam DESGW Candidates (Final Epochs)'
SUBARU = 'LIGO/Virgo/KAGRA S250328ae: Subaru/PFS Spectroscopic Follow-up'
EPOCH_2 = 'LIGO/Virgo/KAGRA S250328ae: DECam DESGW Candidates (Epoch 2)'
ALERT_1_UUID = 'dff8c424-3a97-45fc-a18d-ab76d0db63a9'
ALERT_2_UUID = '0b5e2d6c-1111-4f1e-9d5a-2d7f0c3a9e01'
TOM_MESSAGE_TITLE = 'Test message about S250328ae'
ARCHIVE_ANSWER = {
    'messages': [
        archive_message('1aa8f78b-cbe5-4250-abdf-7daf798c323a', FINAL_EPOCHS, 1747642284458),
        archive_message('edd2f07f-ad56-4aca-b34f-65bf215e6a48', FINAL_EPOCHS, 1747158342822),
        archive_message('31f38718-5018-4c16-8aff-829fa4876e03', SUBARU, 1746893391595, originator='Haibin Zhang'),
        archive_message('ccbf547c-b4ca-488c-8f03-a560430253ce', SUBARU, 1746893390278, originator='Haibin Zhang'),
        archive_message('53d1c15c-7178-4cb2-b0a9-068308869ec4', EPOCH_2, 1746893389383),
        archive_message('0a7775dc-0041-42d2-82a1-c91121b09728', TOM_MESSAGE_TITLE, 1794451928105,
                        topic='tomtoolkit.test', originator=''),
        archive_message(ALERT_2_UUID, '', 1743150000000, topic='igwn.gwalert', originator=''),
        archive_message(ALERT_1_UUID, '', 1743148785794, topic='igwn.gwalert', originator=''),
    ],
    'next': None, 'prev': None,
}


class TestHermesMessage(TestCase):
    def test_from_archive_message(self):
        message = HermesMessage.from_archive_message(ARCHIVE_ANSWER['messages'][0])

        self.assertEqual(message.uuid, '1aa8f78b-cbe5-4250-abdf-7daf798c323a')
        self.assertEqual(message.topic, 'gcn.circulars')
        self.assertEqual(message.title, FINAL_EPOCHS)
        self.assertEqual(message.originator, ORIGINATOR)
        # the archive's timestamp is epoch milliseconds
        self.assertEqual(message.timestamp, datetime(2025, 5, 19, 8, 11, 24, 458000, tzinfo=timezone.utc))
        self.assertFalse(message.retracted)
        self.assertEqual(message.url,
                         urljoin(settings.HERMES_API_URL, '/message/1aa8f78b-cbe5-4250-abdf-7daf798c323a'))

    def test_an_alert_has_no_title(self):
        message = HermesMessage.from_archive_message(ARCHIVE_ANSWER['messages'][-1])

        self.assertEqual((message.topic, message.title), ('igwn.gwalert', ''))

    def test_a_toms_own_topic_is_kept_as_hermes_names_it(self):
        message = HermesMessage.from_archive_message(ARCHIVE_ANSWER['messages'][5])

        self.assertEqual((message.topic, message.title), ('tomtoolkit.test', TOM_MESSAGE_TITLE))

    def test_retracted(self):
        message = archive_message('u', 't', 1747642284458, retracted=True)

        self.assertTrue(HermesMessage.from_archive_message(message).retracted)

    def test_missing_title_and_originator_become_empty_strings(self):
        message = archive_message('u', None, 1747642284458, originator=None)

        parsed = HermesMessage.from_archive_message(message)

        self.assertEqual((parsed.title, parsed.originator), ('', ''))

    def test_one_copy_per_title_keeps_the_earliest_and_every_untitled_message(self):
        messages = [HermesMessage.from_archive_message(entry) for entry in ARCHIVE_ANSWER['messages']]

        kept = one_copy_per_title(messages)

        self.assertEqual(sorted(message.uuid for message in kept),
                         sorted([ALERT_1_UUID, ALERT_2_UUID, '0a7775dc-0041-42d2-82a1-c91121b09728',
                                 'edd2f07f-ad56-4aca-b34f-65bf215e6a48', 'ccbf547c-b4ca-488c-8f03-a560430253ce',
                                 '53d1c15c-7178-4cb2-b0a9-068308869ec4']))


class TestHermesMessagesForEvent(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.event = NonLocalizedEventFactory.create(event_id='S250328ae')
        # two alerts with a time and a GraceDB back-fill without one; the earliest time is the query's lower bound
        EventSequenceFactory.create(nonlocalizedevent=cls.event, sequence_id=1,
                                    details={'time': '2025-03-28T07:04:36.000Z', 'far': 1e-10})
        EventSequenceFactory.create(nonlocalizedevent=cls.event, sequence_id=2,
                                    details={'time': '2025-03-28T06:59:00.000Z', 'far': 1e-10})
        EventSequenceFactory.create(nonlocalizedevent=cls.event, sequence_id=3, details={'pipeline': 'gstlal'})
        cls.start = '2025-03-28T06:59:00+00:00'

    def setUp(self):
        cache.clear()

    @responses.activate
    def test_event_start_time_is_the_earliest_alert_time(self):
        self.assertEqual(event_start_time(self.event).isoformat(), self.start)
        self.assertIsNone(event_start_time(NonLocalizedEventFactory.create(event_id='S250328zz')))

    @responses.activate
    def test_asks_hermes_for_the_event_id_on_every_topic_from_the_first_alert_on(self):
        # no topic parameter: a TOM's own topic is as welcome as the circulars
        responses.get(QUERY_URL, json=ARCHIVE_ANSWER, match=[matchers.query_param_matcher(
            {'search_query': 'S250328ae', 'start': self.start, 'limit': str(HERMES_MESSAGES_LIMIT)})])

        messages, error = hermes_messages_for_event(self.event)

        self.assertIsNone(error)
        # one copy per title, newest first, the alerts (older) last
        self.assertEqual([message.title or message.topic for message in messages],
                         [TOM_MESSAGE_TITLE, FINAL_EPOCHS, SUBARU, EPOCH_2, 'igwn.gwalert', 'igwn.gwalert'])
        self.assertEqual(messages[1].uuid, 'edd2f07f-ad56-4aca-b34f-65bf215e6a48')
        self.assertEqual([message.uuid for message in messages[-2:]], [ALERT_2_UUID, ALERT_1_UUID])

    @responses.activate
    def test_an_event_without_alert_times_sends_no_start(self):
        event = NonLocalizedEventFactory.create(event_id='S250328zz')
        responses.get(QUERY_URL, json={'messages': [], 'next': None, 'prev': None},
                      match=[matchers.query_param_matcher({'search_query': 'S250328zz',
                                                           'limit': str(HERMES_MESSAGES_LIMIT)})])

        self.assertEqual(hermes_messages_for_event(event), ([], None))

    @responses.activate
    def test_limit_is_passed_through(self):
        responses.get(QUERY_URL, json={'messages': [], 'next': None, 'prev': None},
                      match=[matchers.query_param_matcher({'limit': '5'}, strict_match=False)])

        self.assertEqual(hermes_messages_for_event(self.event, limit=5), ([], None))

    @responses.activate
    def test_the_answer_is_cached_and_a_failure_is_not(self):
        responses.get(QUERY_URL, json=ARCHIVE_ANSWER)

        first, _ = hermes_messages_for_event(self.event)
        second, _ = hermes_messages_for_event(self.event)

        self.assertEqual(len(responses.calls), 1)
        self.assertEqual(second, first)

        cache.clear()
        responses.reset()
        responses.get(QUERY_URL, status=503)
        with self.assertLogs(SERVICE_LOGGER, level='WARNING'):
            self.assertEqual(hermes_messages_for_event(self.event)[0], [])
            hermes_messages_for_event(self.event)
        self.assertEqual(len(responses.calls), 2, 'a failure is asked again on the next request')

    @responses.activate
    def test_failures_give_an_empty_list_and_a_message(self):
        cases = {
            'timeout': {'body': requests.exceptions.ConnectTimeout('timed out')},
            'forbidden': {'status': 403},
            'not json': {'body': 'Service Unavailable', 'status': 200},
            'other shape': {'json': {'detail': 'something else'}},
            'message without annotations': {'json': {'messages': [{'metadata': {}}], 'next': None, 'prev': None}},
            'timestamp not a number': {'json': {'messages': [archive_message('u', 't', 'yesterday')],
                                                'next': None, 'prev': None}},
        }
        for name, answer in cases.items():
            with self.subTest(name=name):
                cache.clear()
                responses.reset()
                responses.get(QUERY_URL, **answer)

                with self.assertLogs(SERVICE_LOGGER, level='WARNING'):
                    messages, error = hermes_messages_for_event(self.event)

                self.assertEqual(messages, [])
                self.assertTrue(error.startswith('Hermes '), error)

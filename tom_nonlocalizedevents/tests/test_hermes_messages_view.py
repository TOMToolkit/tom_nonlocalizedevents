"""Tests of the Hermes messages view and of the card on the event page that requests it."""
from django.core.cache import cache
from django.urls import resolve, reverse

import responses

from tom_nonlocalizedevents.tests.factories import EventSequenceFactory
from tom_nonlocalizedevents.tests.test_event_summary import EventPageTestCase
from tom_nonlocalizedevents.tests.test_hermes_messages import (ALERT_1_UUID, ALERT_2_UUID, ARCHIVE_ANSWER, EPOCH_2,
                                                               FINAL_EPOCHS, QUERY_URL, SUBARU, TOM_MESSAGE_TITLE)
from tom_nonlocalizedevents.views import EventHermesMessagesView


class TestEventHermesMessagesView(EventPageTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.messages_url = reverse('nonlocalizedevents:event-hermes-messages', args=(self.event.event_id,))

    def test_url_and_resolution(self):
        self.assertEqual(self.messages_url, f'/nonlocalizedevents/hermes-messages/{self.event.event_id}/')
        self.assertIs(resolve(self.messages_url).func.view_class, EventHermesMessagesView)

    @responses.activate
    def test_lists_the_messages_newest_first_with_links_to_hermes(self):
        responses.get(QUERY_URL, json=ARCHIVE_ANSWER)

        response = self.client.get(self.messages_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/partials/event_hermes_messages_list.html')
        content = response.content.decode()
        self.assertLess(content.index(FINAL_EPOCHS), content.index(SUBARU))
        self.assertLess(content.index(SUBARU), content.index(EPOCH_2))
        # the earliest copy of the final-epochs circular is the one linked
        self.assertIn('href="https://hermes-dev.lco.global/message/edd2f07f-ad56-4aca-b34f-65bf215e6a48"', content)
        self.assertIn('2025-05-13', content)
        self.assertIn('>gcn.circulars<', content)
        self.assertIn(TOM_MESSAGE_TITLE, content)
        self.assertIn('>tomtoolkit.test<', content)
        self.assertNotIn('unavailable', content)

    @responses.activate
    def test_an_alert_is_named_after_the_toms_own_sequence_when_it_has_one(self):
        # the second alert of the archive answer is one the TOM ingested; the first is not
        EventSequenceFactory.create(nonlocalizedevent=self.event, sequence_id=7, event_subtype='UPDATE',
                                    hermes_message_id=ALERT_2_UUID)
        responses.get(QUERY_URL, json=ARCHIVE_ANSWER)

        content = self.client.get(self.messages_url).content.decode()

        self.assertIn('alert 7: UPDATE', content)
        self.assertIn('alert not ingested by this TOM', content)
        self.assertIn(f'href="https://hermes-dev.lco.global/message/{ALERT_1_UUID}"', content)
        self.assertEqual(content.count('>igwn.gwalert<'), 2)

    @responses.activate
    def test_no_messages(self):
        responses.get(QUERY_URL, json={'messages': [], 'next': None, 'prev': None})

        response = self.client.get(self.messages_url)

        self.assertContains(response, f'No message in the Hermes archive mentions {self.event.event_id}.')

    @responses.activate
    def test_hermes_failure_is_reported_in_the_card(self):
        responses.get(QUERY_URL, status=503)

        with self.assertLogs('tom_nonlocalizedevents.services.hermes_messages', level='WARNING'):
            response = self.client.get(self.messages_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hermes messages unavailable: Hermes could not be reached')

    def test_unknown_event_is_404(self):
        unknown_url = reverse('nonlocalizedevents:event-hermes-messages', args=('S000000zz',))

        self.assertEqual(self.client.get(unknown_url).status_code, 404)

    def test_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.messages_url).status_code, 302)

    def test_the_event_page_requests_the_list_when_it_loads(self):
        response = self.client.get(self.url)

        content = response.content.decode()
        self.assertIn('id="event-hermes-messages"', content)
        self.assertIn(f'hx-get="{self.messages_url}" hx-trigger="load"', content)
        # the page itself never asks Hermes
        self.assertNotIn(FINAL_EPOCHS, content)

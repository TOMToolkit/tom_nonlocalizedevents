"""Tests of the event summary: alert selection, the page, the mixin and its alert-tab update."""
from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.views.generic import TemplateView

from tom_nonlocalizedevents.event_summary.context import resolve_event, select_sequence
from tom_nonlocalizedevents.event_summary.views import EventSummaryMixin, htmx_target_element_id
from tom_nonlocalizedevents.tests.factories import (EventLocalizationFactory, EventSequenceFactory,
                                                    NonLocalizedEventFactory)
from tom_nonlocalizedevents.tests.test_alerts import CBC_ALERT

# the headers htmx sends for an alert-tab click, and for a browser-back history restore
TAB_CLICK_HEADERS = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'event-header'}
HISTORY_RESTORE_HEADERS = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_HISTORY_RESTORE_REQUEST': 'true'}


class EventPageTestCase(TestCase):
    """An event with three alerts, the second one combined with an external coincidence, and a logged-in client."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='test_user')
        cls.event = NonLocalizedEventFactory.create(event_id='S260901aa')
        cls.sequences = []
        for sequence_id, subtype, far in [(1, 'EARLYWARNING', 1e-8), (2, 'PRELIMINARY', 1e-9), (3, 'INITIAL', 1e-10)]:
            localization = EventLocalizationFactory.create(
                nonlocalizedevent=cls.event, distance_mean=100.0 * sequence_id, distance_std=10.0,
                area_90=1000.0 / sequence_id)
            cls.sequences.append(EventSequenceFactory.create(
                nonlocalizedevent=cls.event, localization=localization, sequence_id=sequence_id,
                event_subtype=subtype, details={**CBC_ALERT, 'far': far}))
        cls.url = reverse('nonlocalizedevents:event-detail', args=(cls.event.event_id,))

    def setUp(self):
        self.client.force_login(self.user)


class TestSelection(TestCase):
    def test_resolve_event(self):
        event = NonLocalizedEventFactory.create(event_id='S260901bb')

        self.assertEqual(resolve_event(event), event)
        self.assertEqual(resolve_event('S260901bb'), event)
        self.assertEqual(resolve_event(pk=event.pk), event)
        self.assertEqual(resolve_event(event_id='S260901bb'), event)
        with self.assertRaises(Http404):
            resolve_event(event_id='S000000xx')
        with self.assertRaises(Http404):
            resolve_event()

    def test_select_sequence(self):
        event = NonLocalizedEventFactory.create()
        first = EventSequenceFactory.create(nonlocalizedevent=event, sequence_id=1)
        latest = EventSequenceFactory.create(nonlocalizedevent=event, sequence_id=4)

        self.assertEqual(select_sequence([first, latest], None), latest)
        self.assertEqual(select_sequence([first, latest], ''), latest)
        self.assertEqual(select_sequence([first, latest], '1'), first)
        self.assertEqual(select_sequence([first, latest], '4'), latest)
        self.assertEqual(select_sequence([first, latest], '7'), latest)
        self.assertEqual(select_sequence([first, latest], 'three'), latest)
        self.assertIsNone(select_sequence([], '1'))

    def test_htmx_target_element_id(self):
        factory = RequestFactory()
        self.assertIsNone(htmx_target_element_id(factory.get('/')))
        request = factory.get('/', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='event-header')
        request.htmx = type('Details', (), {'target': 'event-header'})()
        self.assertEqual(htmx_target_element_id(request), 'event-header')
        request.htmx = type('Details', (), {'target': 'div#event-header'})()
        self.assertEqual(htmx_target_element_id(request), 'event-header')


class TestEventPage(EventPageTestCase):
    def test_shows_the_latest_alert_by_default(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_sequence'], self.sequences[-1])
        self.assertContains(response, 'id="event-header"')
        self.assertContains(response, '3: INITIAL')
        self.assertContains(response, '300 ± 10 Mpc')
        self.assertContains(response, 'id="workflow-step-content"')
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/event_page_base.html')
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/workflow_steps/overview.html')
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/detail/gw_detail.html')

    def test_sequence_parameter_selects_the_alert(self):
        response = self.client.get(self.url, {'sequence': '1'})

        self.assertEqual(response.context['selected_sequence'], self.sequences[0])
        self.assertContains(response, '100 ± 10 Mpc')

    def test_unknown_and_non_integer_sequences_fall_back_to_the_latest(self):
        for requested in ['9', 'x']:
            with self.subTest(requested=requested):
                response = self.client.get(self.url, {'sequence': requested})
                self.assertEqual(response.context['selected_sequence'], self.sequences[-1])

    def test_alert_tabs_request_the_page_itself(self):
        response = self.client.get(self.url, {'sequence': '2'})

        self.assertContains(response, f'hx-get="{self.url}?sequence=1"')
        self.assertContains(response, 'hx-target="#event-header"')
        self.assertContains(response, 'hx-push-url="?sequence=1"')
        self.assertContains(response, 'href="?sequence=3"')

    def test_event_without_alerts(self):
        event = NonLocalizedEventFactory.create(event_id='S260901cc')

        response = self.client.get(reverse('nonlocalizedevents:event-detail', args=(event.event_id,)))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_sequence'])
        self.assertContains(response, 'No alerts ingested')

    def test_pk_route_and_external_coincidence_badge(self):
        response = self.client.get(reverse('nonlocalizedevents:detail', args=(self.event.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['event'], self.event)

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)


class TestAlertTabUpdate(EventPageTestCase):
    def test_tab_click_returns_header_and_workflow_step_content_out_of_band(self):
        response = self.client.get(self.url, {'sequence': '1'}, **TAB_CLICK_HEADERS)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/partials/event_summary_update.html')
        self.assertTemplateNotUsed(response, 'tom_nonlocalizedevents/event_page_base.html')
        content = response.content.decode()
        self.assertIn('id="event-header"', content)
        self.assertIn('100 ± 10 Mpc', content)
        self.assertIn('<div id="workflow-step-content" hx-swap-oob="true">', content)
        self.assertIn('table-primary', content)  # the selected alert's row in the overview table
        self.assertNotIn('<html', content)

    def test_history_restore_gets_the_full_page(self):
        response = self.client.get(self.url, {'sequence': '1'}, **HISTORY_RESTORE_HEADERS)

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/event_page_base.html')

    def test_an_htmx_request_for_another_target_is_not_a_tab_click(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='something-else')

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/event_page_base.html')


class TestShowSkyMapAttribute(TestCase):
    """A view that draws its own sky map sets ``show_sky_map = False`` on the class."""

    def test_the_attribute_reaches_the_context(self):
        class PageWithoutSkyMap(EventSummaryMixin, TemplateView):
            template_name = 'tom_nonlocalizedevents/partials/event_header.html'
            show_sky_map = False

        event = NonLocalizedEventFactory.create(event_id='S260901nm')
        request = RequestFactory().get(f'/nonlocalizedevents/{event.event_id}/')
        response = PageWithoutSkyMap.as_view()(request, event_id=event.event_id)

        self.assertIs(response.context_data['show_sky_map'], False)

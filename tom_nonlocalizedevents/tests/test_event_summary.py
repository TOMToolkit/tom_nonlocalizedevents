"""Tests of the event summary: alert selection, the mixin and its alert-tab update."""
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

# the headers htmx sends for an alert-tab click
TAB_CLICK_HEADERS = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'event-header'}


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

"""Tests of the MOC JSON endpoint that serves a localization's credible region to Aladin Lite."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from tom_nonlocalizedevents.services.credible_regions import credible_region_moc_json
from tom_nonlocalizedevents.tests.factories import (EventLocalizationFactory, NonLocalizedEventFactory,
                                                    build_synthetic_localization, healpix_pixel_area,
                                                    healpix_pixel_count)
from tom_nonlocalizedevents.views import LocalizationMOCJsonView

ORDER = 3


def densities():
    """An order-3 map: pixels 0..3 hold 0.3, 0.22, 0.2, 0.13 (cumulative 0.3, 0.52, 0.72, 0.85); the rest share 0.15.

    The cumulative values stay clear of the probabilities the tests request, so no expectation depends on a
    floating-point tie. Pixels 0..3 are the children of order-2 pixel 0, so a region of all four reads back as
    that one cell.
    """
    count = healpix_pixel_count(ORDER)
    probabilities = [0.3, 0.22, 0.2, 0.13] + [0.15 / (count - 4)] * (count - 4)
    return [probability / healpix_pixel_area(ORDER) for probability in probabilities]


def skymaptile_queries(captured):
    return [query['sql'] for query in captured.captured_queries if 'skymaptile' in query['sql']]


class TestLocalizationMOCJsonView(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='test_user')
        cls.event = NonLocalizedEventFactory.create()
        cls.localization = build_synthetic_localization(cls.event, densities(), order=ORDER)
        cls.url = reverse('nonlocalizedevents:localization-moc', kwargs={'pk': cls.localization.pk})

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def test_url_and_resolution(self):
        self.assertEqual(self.url, f'/nonlocalizedevents/localizations/{self.localization.pk}/moc.json')
        self.assertIs(resolve(self.url).func.view_class, LocalizationMOCJsonView)

    def test_returns_the_region_as_moc_json(self):
        response = self.client.get(self.url, {'prob': '0.6'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        # cumulative 0.52 <= 0.6 admits pixels 0 and 1; pixel 2 would make it 0.72
        self.assertEqual(response.json(), {'3': [0, 1]})
        self.assertEqual(response.json(), credible_region_moc_json(self.localization, 0.6))

    def test_defaults_are_prob_0_9_and_display_order_10(self):
        response = self.client.get(self.url)

        # cumulative 0.85 <= 0.9 admits pixels 0..3, which mocpy reports as order-2 pixel 0; the stored order 3 is
        # already coarser than 10
        self.assertEqual(response.json(), {'2': [0]})
        self.assertEqual(response.json(), credible_region_moc_json(self.localization, 0.9, max_order=10))

    def test_max_order_degrades_the_region(self):
        self.assertEqual(self.client.get(self.url, {'prob': '0.6', 'max_order': '3'}).json(), {'3': [0, 1]})
        self.assertEqual(self.client.get(self.url, {'prob': '0.6', 'max_order': '2'}).json(), {'2': [0]})
        self.assertEqual(self.client.get(self.url, {'prob': '0.6', 'max_order': '0'}).json(), {'0': [0]})

    def test_the_densest_tile_is_the_region_when_no_tile_qualifies(self):
        self.assertEqual(self.client.get(self.url, {'prob': '0.1'}).json(), {'3': [0]})
        # pixel 0 alone (0.3) qualifies at 0.5, so this is the regular rule giving the same answer
        self.assertEqual(self.client.get(self.url, {'prob': '0.5'}).json(), {'3': [0]})

    def test_invalid_parameters_are_400(self):
        for params in [{'prob': '0'}, {'prob': '1.5'}, {'prob': '-0.5'}, {'prob': 'abc'},
                       {'max_order': '30'}, {'max_order': '-1'}, {'max_order': '2.5'}, {'max_order': 'x'}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.url, params).status_code, 400)

    def test_unknown_localization_is_404(self):
        self.assertEqual(self.client.get(reverse('nonlocalizedevents:localization-moc', kwargs={'pk': 0})).status_code,
                         404)

    def test_a_localization_without_tiles_gives_an_empty_object(self):
        empty = EventLocalizationFactory.create(nonlocalizedevent=self.event)

        response = self.client.get(reverse('nonlocalizedevents:localization-moc', kwargs={'pk': empty.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_second_request_is_served_from_the_cache(self):
        with CaptureQueriesContext(connection) as first:
            self.client.get(self.url, {'prob': '0.9'})
        with CaptureQueriesContext(connection) as second:
            response = self.client.get(self.url, {'prob': '0.90'})
        with CaptureQueriesContext(connection) as other_probability:
            self.client.get(self.url, {'prob': '0.5'})

        self.assertGreater(len(skymaptile_queries(first)), 0)
        self.assertEqual(skymaptile_queries(second), [])
        self.assertEqual(response.json(), {'2': [0]})
        self.assertGreater(len(skymaptile_queries(other_probability)), 0)

    def test_browser_cache_header(self):
        self.assertEqual(self.client.get(self.url)['Cache-Control'], 'private, max-age=86400')

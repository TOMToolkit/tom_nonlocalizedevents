"""Tests of the test factories and the synthetic-localization helpers themselves.

The credible-region tests trust these helpers for their expected values, so the helpers are checked against
the encoding the plugin stores (healpix_alchemy's) and the 1.0.0 encoder.
"""
import math

from django.db.models import signals
from django.test import TestCase

from astropy_healpix import level_ipix_to_uniq
from healpix_alchemy.constants import LEVEL, PIXEL_AREA

from tom_nonlocalizedevents.healpix_utils import uniq_to_bigintrange
from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate, SkymapTile
from tom_nonlocalizedevents.tests.factories import (CredibleRegionFactory, EventCandidateFactory,
                                                    EventLocalizationFactory, NonLocalizedEventFactory,
                                                    SkymapTileFactory, TargetFactory, build_synthetic_localization,
                                                    healpix_pixel_area, healpix_pixel_count, healpix_pixel_range)


class TestHealpixHelpers(TestCase):
    def test_pixel_count_and_area_follow_the_healpix_definition(self):
        self.assertEqual(healpix_pixel_count(0), 12)
        self.assertEqual(healpix_pixel_count(3), 768)
        self.assertAlmostEqual(healpix_pixel_area(0) * 12, 4 * math.pi)
        # the stored ranges count order-29 pixels, so the order-29 pixel area is healpix_alchemy's PIXEL_AREA
        self.assertAlmostEqual(healpix_pixel_area(LEVEL) / PIXEL_AREA, 1.0, places=12)

    def test_pixel_range_matches_the_encoder_that_ingestion_uses(self):
        """The helper must produce the same ranges as the 1.0.0 UNIQ-based encoder for the same pixels.

        tom_regions refactor: compare against ``healpix_django.encoding.uniq_to_range`` instead.
        """
        for order, ipix in [(0, 0), (0, 11), (3, 0), (3, 767), (8, 12345), (29, 7)]:
            with self.subTest(order=order, ipix=ipix):
                self.assertEqual(healpix_pixel_range(order, ipix),
                                 uniq_to_bigintrange(level_ipix_to_uniq(order, ipix)))

    def test_pixel_range_at_order_29_is_one_pixel_wide(self):
        self.assertEqual(healpix_pixel_range(29, 5), (5, 6))


class TestBuildSyntheticLocalization(TestCase):
    def setUp(self):
        self.event = NonLocalizedEventFactory.create()

    def test_builds_one_tile_per_pixel_covering_the_sky_without_overlap(self):
        localization = build_synthetic_localization(self.event, [1.0] * 768)

        tiles = list(SkymapTile.objects.filter(localization=localization).order_by('tile'))
        self.assertEqual(len(tiles), 768)
        self.assertEqual(tiles[0].tile.lower, 0)
        self.assertEqual(tiles[-1].tile.upper, healpix_pixel_count(LEVEL))
        for previous, current in zip(tiles, tiles[1:]):
            self.assertEqual(previous.tile.upper, current.tile.lower)
        self.assertEqual({tile.tile.upper - tile.tile.lower for tile in tiles}, {4 ** (LEVEL - 3)})

    def test_tile_areas_in_the_stored_encoding_sum_to_the_sphere(self):
        localization = build_synthetic_localization(self.event, [1.0] * 768)

        area = sum((tile.tile.upper - tile.tile.lower) * PIXEL_AREA for tile in localization.tiles.all())
        self.assertAlmostEqual(area, 4 * math.pi, places=10)

    def test_densities_are_stored_in_pixel_order(self):
        densities = [float(ipix) for ipix in range(768)]
        localization = build_synthetic_localization(self.event, densities)

        stored = list(localization.tiles.order_by('tile').values_list('probdensity', flat=True))
        self.assertEqual(stored, densities)

    def test_a_normalized_map_has_probabilities_summing_to_one(self):
        probabilities = [0.5, 0.3, 0.15] + [0.05 / 765] * 765
        densities = [probability / healpix_pixel_area(3) for probability in probabilities]
        localization = build_synthetic_localization(self.event, densities)

        total = sum(tile.probdensity * (tile.tile.upper - tile.tile.lower) * PIXEL_AREA
                    for tile in localization.tiles.all())
        self.assertAlmostEqual(total, 1.0, places=10)

    def test_other_orders_and_localization_fields(self):
        localization = build_synthetic_localization(self.event, [1.0] * 12, order=0, distance_mean=40.0)

        self.assertEqual(localization.tiles.count(), 12)
        self.assertEqual(localization.distance_mean, 40.0)

    def test_rejects_a_density_count_that_does_not_match_the_order(self):
        with self.assertRaisesRegex(ValueError, '767 densities given, but order 3 has 768 pixels'):
            build_synthetic_localization(self.event, [1.0] * 767)

    def test_does_not_run_the_localization_post_save_handler(self):
        """The handler would queue a credible-region computation on commit; the helper must queue nothing."""
        EventCandidateFactory.create(nonlocalizedevent=self.event)

        with self.captureOnCommitCallbacks() as callbacks:
            build_synthetic_localization(self.event, [1.0] * 768)

        self.assertEqual(callbacks, [])
        self.assertFalse(CredibleRegion.objects.exists())


class TestFactories(TestCase):
    def test_localization_date_is_timezone_aware(self):
        localization = EventLocalizationFactory.create()

        self.assertIsNotNone(localization.date.tzinfo)

    def test_target_factory_coordinates_are_valid_and_create_no_related_rows(self):
        target = TargetFactory.create()

        self.assertTrue(0 <= target.ra < 360)
        self.assertTrue(-90 <= target.dec <= 90)
        self.assertEqual(target.aliases.count(), 0)
        self.assertEqual(target.targetextra_set.count(), 0)

    def test_event_candidate_factory_sets_healpix_without_running_the_post_save_handler(self):
        received = []
        signals.post_save.connect(lambda **kwargs: received.append(kwargs['sender']), weak=False,
                                  dispatch_uid='test_event_candidate_factory')
        try:
            candidate = EventCandidateFactory.create()
        finally:
            signals.post_save.disconnect(dispatch_uid='test_event_candidate_factory')

        self.assertNotEqual(candidate.healpix, EventCandidate._meta.get_field('healpix').default)
        self.assertNotIn(EventCandidate, received)

    def test_credible_region_factory_links_the_candidate_to_the_localizations_event(self):
        credible_region = CredibleRegionFactory.create()

        self.assertEqual(credible_region.candidate.nonlocalizedevent, credible_region.localization.nonlocalizedevent)
        self.assertEqual(credible_region.smallest_percent, 100)

    def test_skymap_tile_factory_defaults_to_consecutive_order_3_pixels(self):
        localization = EventLocalizationFactory.create()
        SkymapTileFactory.reset_sequence(0)
        first, second = SkymapTileFactory.create_batch(2, localization=localization)

        self.assertEqual(first.tile.lower, 0)

        self.assertEqual(first.tile.upper, second.tile.lower)
        self.assertEqual(first.tile.upper - first.tile.lower, 4 ** (LEVEL - 3))
        self.assertAlmostEqual(first.probdensity * 4 * math.pi, 1.0)

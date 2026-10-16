"""Tests of services.credible_regions on synthetic order-3 localizations with chosen per-pixel probabilities.

Two maps are used throughout. ``DISTINCT`` gives pixels 0..6 the probabilities 0.33, 0.24, 0.16, 0.11, 0.07,
0.05, 0.03 and spreads the remaining 0.01 over the other 761 pixels, so the cumulative probabilities,
densest first, are 0.33, 0.57, 0.73, 0.84, 0.91, 0.96, 0.99 and then 1.0 for every low-density pixel. With the
default levels (0.25, 0.5, 0.75, 0.9, 0.95) the thresholds are therefore: none for 0.25 (the densest pixel
alone exceeds it), pixel 0's density for 0.5, pixel 2's for 0.75, pixel 3's for 0.9 and pixel 4's for 0.95.
``TIED`` gives pixels 0 and 1 the same probability 0.3, pixel 2 0.2 and pixel 3 0.12; the tied pair enters
the cumulative sum together (0.6, 0.6, 0.8, 0.92), so neither 0.25 nor 0.5 has a threshold.

The ``RECORDED_1_0_0_RESULTS`` dictionaries hold what the 1.0.0 SQLAlchemy implementation recorded for candidates at
the centers of pixels 0..6 and 100 of these maps (run on 2026-10-05 before that implementation was replaced).
"""
import math
import os
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from astropy_healpix import healpix_to_lonlat, level_to_nside
from healpix_alchemy.constants import PIXEL_AREA

from tom_nonlocalizedevents.models import CredibleRegion, SkymapTile
from tom_nonlocalizedevents.services.credible_regions import (DEFAULT_CREDIBLE_REGION_PROBABILITIES,
                                                              PIXEL_AREA_STERADIANS, TileArea,
                                                              credible_level_thresholds, credible_region_moc,
                                                              credible_region_moc_json,
                                                              credible_region_probabilities, localization_center,
                                                              range_to_level_ipix, tile_densities_at_points,
                                                              update_all_credible_region_percents_for_candidates)
from tom_nonlocalizedevents.tests.factories import (EventCandidateFactory, EventLocalizationFactory,
                                                    NonLocalizedEventFactory, TargetFactory,
                                                    build_synthetic_localization, healpix_pixel_area,
                                                    healpix_pixel_count, healpix_pixel_range)

ORDER = 3
DISTINCT = [0.33, 0.24, 0.16, 0.11, 0.07, 0.05, 0.03]
TIED = [0.3, 0.3, 0.2, 0.12]
CANDIDATE_PIXELS = [0, 1, 2, 3, 4, 5, 6, 100]
RECORDED_1_0_0_RESULTS = {
    'DISTINCT': {0: 50, 1: 75, 2: 75, 3: 90, 4: 95, 5: None, 6: None, 100: None},
    'TIED': {0: 75, 1: 75, 2: 90, 3: 95, 4: None, 5: None, 6: None, 100: None},
}


def densities_for(head_probabilities):
    """Per-pixel densities of an order-3 map with the given leading probabilities; the rest share the remainder."""
    count = healpix_pixel_count(ORDER)
    remainder = 1.0 - sum(head_probabilities)
    probabilities = list(head_probabilities) + [remainder / (count - len(head_probabilities))] * (
        count - len(head_probabilities))
    return [probability / healpix_pixel_area(ORDER) for probability in probabilities]


def skymaptile_queries(captured):
    return [query['sql'] for query in captured.captured_queries if 'skymaptile' in query['sql']]


class SyntheticLocalizationTestCase(TestCase):
    """Builds the DISTINCT map, with helpers to place candidates at pixel centers."""

    @classmethod
    def setUpTestData(cls):
        cls.event = NonLocalizedEventFactory.create()
        cls.densities = densities_for(DISTINCT)
        cls.localization = build_synthetic_localization(cls.event, cls.densities, order=ORDER)

    def candidate_at_pixel(self, ipix, event=None):
        """An EventCandidate whose target sits at the center of order-3 pixel ``ipix``."""
        longitude, latitude = healpix_to_lonlat(ipix, level_to_nside(ORDER), order='nested')
        target = TargetFactory.create(ra=float(longitude.deg), dec=float(latitude.deg))
        return EventCandidateFactory.create(target=target, nonlocalizedevent=event or self.event)

    def smallest_percents(self, candidates_by_pixel, localization=None):
        return {ipix: CredibleRegion.objects.filter(candidate=candidate, localization=localization or self.localization)
                .values_list('smallest_percent', flat=True).first()
                for ipix, candidate in candidates_by_pixel.items()}


class TestEncodingHelpers(TestCase):
    def test_pixel_area_constant_equals_the_healpix_alchemy_constant_exactly(self):
        """The 1.0.0 query multiplied by healpix_alchemy's PIXEL_AREA; thresholds stay identical only if ours is the
        same double."""
        self.assertEqual(PIXEL_AREA_STERADIANS, float(PIXEL_AREA))

    def test_tile_area_in_sql_matches_the_healpix_alchemy_tile_area(self):
        localization = EventLocalizationFactory.create()
        SkymapTile.objects.create(localization=localization, tile=healpix_pixel_range(3, 5), probdensity=1.0)
        SkymapTile.objects.create(localization=localization, tile=healpix_pixel_range(29, 7), probdensity=1.0)

        # ranges order by their lower bound, so the order-29 tile [7, 8) comes first
        order_29_area, order_3_area = (SkymapTile.objects.filter(localization=localization).order_by('tile')
                                       .annotate(area=TileArea('tile')).values_list('area', flat=True))

        self.assertEqual(order_3_area, 4 ** (29 - 3) * float(PIXEL_AREA))
        self.assertEqual(order_29_area, float(PIXEL_AREA))
        self.assertAlmostEqual(order_3_area, healpix_pixel_area(3), places=12)

    def test_range_to_level_ipix_inverts_the_stored_encoding(self):
        for level, ipix in [(0, 0), (0, 11), (3, 0), (3, 767), (8, 478293), (29, 7), (29, 12 * 4 ** 29 - 1)]:
            with self.subTest(level=level, ipix=ipix):
                self.assertEqual(range_to_level_ipix(*healpix_pixel_range(level, ipix)), (level, ipix))

    def test_range_to_level_ipix_rejects_ranges_that_are_not_one_cell(self):
        with self.assertRaisesRegex(ValueError, 'not a power of four'):
            range_to_level_ipix(0, 3)
        with self.assertRaisesRegex(ValueError, 'not a power of four'):
            range_to_level_ipix(0, 8)
        with self.assertRaisesRegex(ValueError, 'not aligned'):
            range_to_level_ipix(2, 6)
        with self.assertRaisesRegex(ValueError, 'not a power of four'):
            range_to_level_ipix(5, 5)


class TestCredibleRegionProbabilities(TestCase):
    def setUp(self):
        # The precedence tests below rely on the harness leaving the setting undefined.
        self.assertFalse(hasattr(settings, 'CREDIBLE_REGION_PROBABILITIES'))

    def test_default_is_the_1_0_0_list_largest_first(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop('CREDIBLE_REGION_PROBABILITIES', None)
            self.assertEqual(credible_region_probabilities(), [0.95, 0.9, 0.75, 0.5, 0.25])
        self.assertEqual(sorted(DEFAULT_CREDIBLE_REGION_PROBABILITIES, reverse=True), [0.95, 0.9, 0.75, 0.5, 0.25])

    def test_environment_variable_is_read_as_json(self):
        with mock.patch.dict(os.environ, {'CREDIBLE_REGION_PROBABILITIES': '[0.5, 0.99, 0.1]'}):
            self.assertEqual(credible_region_probabilities(), [0.99, 0.5, 0.1])

    def test_setting_takes_precedence_over_the_environment_and_accepts_a_list_or_a_json_string(self):
        with mock.patch.dict(os.environ, {'CREDIBLE_REGION_PROBABILITIES': '[0.5]'}):
            with override_settings(CREDIBLE_REGION_PROBABILITIES=[0.2, 0.8]):
                self.assertEqual(credible_region_probabilities(), [0.8, 0.2])
            with override_settings(CREDIBLE_REGION_PROBABILITIES='[0.2, 0.8]'):
                self.assertEqual(credible_region_probabilities(), [0.8, 0.2])

    def test_duplicates_collapse_and_values_become_floats(self):
        with override_settings(CREDIBLE_REGION_PROBABILITIES=[1, 0.5, '0.5']):
            self.assertEqual(credible_region_probabilities(), [1.0, 0.5])

    def test_invalid_values_raise_improperly_configured(self):
        for value in ['not json', [1.5], [0], [], 7, ['x']]:
            with self.subTest(value=value), override_settings(CREDIBLE_REGION_PROBABILITIES=value):
                with self.assertRaises(ImproperlyConfigured):
                    credible_region_probabilities()


class TestCredibleLevelThresholds(SyntheticLocalizationTestCase):
    def test_thresholds_follow_the_cumulative_probability_rule_in_one_query(self):
        with CaptureQueriesContext(connection) as captured:
            thresholds = credible_level_thresholds(self.localization, [0.25, 0.5, 0.75, 0.9, 0.95])

        self.assertEqual(len(skymaptile_queries(captured)), 1)
        self.assertEqual(thresholds, {
            0.25: None,                 # pixel 0 alone holds 0.33
            0.5: self.densities[0],     # cumulative 0.33 <= 0.5; pixel 1 would make it 0.57
            0.75: self.densities[2],    # 0.73
            0.9: self.densities[3],     # 0.84
            0.95: self.densities[4],    # 0.91
        })

    def test_default_probabilities_come_from_the_configuration(self):
        with override_settings(CREDIBLE_REGION_PROBABILITIES=[0.6]):
            self.assertEqual(credible_level_thresholds(self.localization), {0.6: self.densities[1]})

    def test_tied_densities_enter_the_cumulative_sum_together(self):
        localization = build_synthetic_localization(self.event, densities_for(TIED), order=ORDER)
        tied_density = densities_for(TIED)[0]

        thresholds = credible_level_thresholds(localization, [0.25, 0.5, 0.75, 0.9, 0.95])

        # the pair's cumulative probability is 0.6 for both rows, so neither qualifies at 0.5 and both do at 0.75
        self.assertEqual(thresholds, {0.25: None, 0.5: None, 0.75: tied_density,
                                      0.9: densities_for(TIED)[2], 0.95: densities_for(TIED)[3]})

    def test_a_probability_of_one_includes_every_tile(self):
        thresholds = credible_level_thresholds(self.localization, [1.0])

        self.assertEqual(thresholds[1.0], min(self.densities))

    def test_a_localization_without_tiles_has_no_thresholds(self):
        empty = EventLocalizationFactory.create(nonlocalizedevent=self.event)

        self.assertEqual(credible_level_thresholds(empty, [0.5, 0.9]), {0.5: None, 0.9: None})


class TestTileDensitiesAtPoints(SyntheticLocalizationTestCase):
    def test_returns_the_containing_tiles_density_in_the_order_given(self):
        points = [healpix_pixel_range(ORDER, ipix)[0] + 12345 for ipix in (6, 0, 100, 3)]

        with CaptureQueriesContext(connection) as captured:
            densities = tile_densities_at_points(self.localization, points)

        self.assertEqual(len(skymaptile_queries(captured)), 1)
        self.assertEqual(densities, [self.densities[6], self.densities[0], self.densities[100], self.densities[3]])

    def test_points_outside_every_tile_are_none(self):
        gap_pixel = 200
        self.localization.tiles.filter(tile__contains=healpix_pixel_range(ORDER, gap_pixel)[0]).delete()
        inside, outside = healpix_pixel_range(ORDER, 0)[0], healpix_pixel_range(ORDER, gap_pixel)[0] + 1

        self.assertEqual(tile_densities_at_points(self.localization, [outside, inside, outside]),
                         [None, self.densities[0], None])

    def test_no_points_means_no_query(self):
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(tile_densities_at_points(self.localization, []), [])
        self.assertEqual(captured.captured_queries, [])

    def test_only_the_given_localizations_tiles_count(self):
        other = build_synthetic_localization(self.event, [1.0] * healpix_pixel_count(ORDER), order=ORDER)

        self.assertEqual(tile_densities_at_points(other, [healpix_pixel_range(ORDER, 0)[0]]), [1.0])

    def test_candidate_healpix_values_are_points_in_this_encoding(self):
        candidate = self.candidate_at_pixel(4)

        self.assertEqual(tile_densities_at_points(self.localization, [candidate.healpix]), [self.densities[4]])


class TestUpdateAllCredibleRegionPercentsForCandidates(SyntheticLocalizationTestCase):
    def setUp(self):
        self.candidates = {ipix: self.candidate_at_pixel(ipix) for ipix in CANDIDATE_PIXELS}

    def test_records_the_smallest_containing_level_per_candidate(self):
        with CaptureQueriesContext(connection) as captured:
            update_all_credible_region_percents_for_candidates(self.localization)

        # one query for the thresholds and one for the candidates' tiles, however many candidates
        self.assertEqual(len(skymaptile_queries(captured)), 2)
        self.assertEqual(self.smallest_percents(self.candidates),
                         {0: 25, 1: 75, 2: 75, 3: 90, 4: 95, 5: None, 6: None, 100: None})

    def test_reproduces_the_1_0_0_results_except_where_the_densest_tile_alone_exceeds_a_level(self):
        """1.0.0 recorded no region for a candidate when the densest tile alone held more than the level's
        probability; this implementation records that tile as the region (plan decision I)."""
        update_all_credible_region_percents_for_candidates(self.localization)
        results = self.smallest_percents(self.candidates)

        self.assertEqual(results[0], 25)
        self.assertEqual(RECORDED_1_0_0_RESULTS['DISTINCT'][0], 50)
        self.assertEqual({ipix: value for ipix, value in results.items() if ipix != 0},
                         {ipix: value for ipix, value in RECORDED_1_0_0_RESULTS['DISTINCT'].items() if ipix != 0})

    def test_reproduces_the_1_0_0_results_for_tied_densities(self):
        tied_event = NonLocalizedEventFactory.create()
        tied_localization = build_synthetic_localization(tied_event, densities_for(TIED), order=ORDER)
        candidates = {ipix: self.candidate_at_pixel(ipix, event=tied_event) for ipix in CANDIDATE_PIXELS}

        update_all_credible_region_percents_for_candidates(tied_localization)
        results = self.smallest_percents(candidates, localization=tied_localization)

        # the tied pair is the densest tile for 0.25 and 0.5, where 1.0.0 recorded nothing
        self.assertEqual(results[0], 25)
        self.assertEqual(results[1], 25)
        self.assertEqual({ipix: value for ipix, value in results.items() if ipix > 1},
                         {ipix: value for ipix, value in RECORDED_1_0_0_RESULTS['TIED'].items() if ipix > 1})

    def test_only_the_given_candidate_ids_are_evaluated(self):
        update_all_credible_region_percents_for_candidates(self.localization, [self.candidates[3].pk])

        self.assertEqual(self.smallest_percents(self.candidates),
                         {0: None, 1: None, 2: None, 3: 90, 4: None, 5: None, 6: None, 100: None})

    def test_an_empty_id_list_means_every_candidate_of_the_event(self):
        update_all_credible_region_percents_for_candidates(self.localization, [])

        self.assertEqual(CredibleRegion.objects.filter(localization=self.localization).count(), 5)

    def test_an_existing_row_is_updated_and_rows_are_never_deleted(self):
        CredibleRegion.objects.create(localization=self.localization, candidate=self.candidates[3],
                                      smallest_percent=100)
        CredibleRegion.objects.create(localization=self.localization, candidate=self.candidates[100],
                                      smallest_percent=90)

        update_all_credible_region_percents_for_candidates(self.localization)

        self.assertEqual(self.smallest_percents(self.candidates)[3], 90)
        self.assertEqual(self.smallest_percents(self.candidates)[100], 90)

    def test_uses_the_configured_probabilities(self):
        with override_settings(CREDIBLE_REGION_PROBABILITIES=[0.6, 0.29]):
            update_all_credible_region_percents_for_candidates(self.localization)

        # 0.29: no tile qualifies, so the densest tile is the region; 0.6: pixels 0 and 1 (0.57)
        self.assertEqual(self.smallest_percents(self.candidates),
                         {0: 29, 1: 60, 2: None, 3: None, 4: None, 5: None, 6: None, 100: None})

    def test_a_single_probability_records_that_region_and_leaves_other_rows_alone(self):
        CredibleRegion.objects.create(localization=self.localization, candidate=self.candidates[4],
                                      smallest_percent=95)

        update_all_credible_region_percents_for_candidates(self.localization, probabilities=[0.9])

        self.assertEqual(self.smallest_percents(self.candidates),
                         {0: 90, 1: 90, 2: 90, 3: 90, 4: 95, 5: None, 6: None, 100: None})

    def test_candidates_outside_every_tile_get_no_row(self):
        self.localization.tiles.filter(tile__contains=self.candidates[2].healpix).delete()

        update_all_credible_region_percents_for_candidates(self.localization)

        self.assertIsNone(self.smallest_percents(self.candidates)[2])

    def test_a_localization_without_tiles_records_nothing(self):
        empty = EventLocalizationFactory.create(nonlocalizedevent=self.event)

        update_all_credible_region_percents_for_candidates(empty)

        self.assertFalse(CredibleRegion.objects.filter(localization=empty).exists())

    def test_an_event_without_candidates_runs_no_tile_query(self):
        other_event = NonLocalizedEventFactory.create()
        localization = build_synthetic_localization(other_event, [1.0] * healpix_pixel_count(ORDER), order=ORDER)

        with CaptureQueriesContext(connection) as captured:
            update_all_credible_region_percents_for_candidates(localization)

        self.assertEqual(skymaptile_queries(captured), [])


class TestCredibleRegionMoc(SyntheticLocalizationTestCase):
    """Order-3 pixels 0..3 are the children of order-2 pixel 0, so mocpy reports a region of all four as that one
    order-2 cell."""

    def test_moc_holds_the_tiles_at_or_above_the_threshold(self):
        moc = credible_region_moc(self.localization, 0.9)

        self.assertEqual(moc.max_order, 29)
        self.assertAlmostEqual(moc.sky_fraction * 4 * math.pi, 4 * healpix_pixel_area(ORDER), places=12)
        self.assertEqual(credible_region_moc_json(self.localization, 0.9), {'2': [0]})
        self.assertEqual(credible_region_moc_json(self.localization, 0.95), {'2': [0], '3': [4]})

    def test_the_densest_tile_is_the_region_when_no_tile_qualifies(self):
        self.assertEqual(credible_region_moc_json(self.localization, 0.25), {'3': [0]})

    def test_json_leaves_out_empty_orders_and_keeps_python_ints(self):
        moc_json = credible_region_moc_json(self.localization, 0.95)

        self.assertEqual(list(moc_json), ['2', '3'])
        self.assertEqual({type(ipix) for pixels in moc_json.values() for ipix in pixels}, {int})

    def test_cells_finer_than_max_order_are_degraded(self):
        self.assertEqual(credible_region_moc_json(self.localization, 0.95, max_order=2), {'2': [0, 1]})
        self.assertEqual(credible_region_moc_json(self.localization, 0.95, max_order=3), {'2': [0], '3': [4]})
        self.assertEqual(credible_region_moc_json(self.localization, 0.95, max_order=29), {'2': [0], '3': [4]})
        self.assertEqual(credible_region_moc_json(self.localization, 0.95, max_order=0), {'0': [0]})

    def test_a_probability_of_one_covers_the_sky(self):
        self.assertEqual(credible_region_moc(self.localization, 1.0).sky_fraction, 1.0)

    def test_a_localization_without_tiles_gives_an_empty_moc(self):
        empty = EventLocalizationFactory.create(nonlocalizedevent=self.event)

        self.assertTrue(credible_region_moc(empty, 0.9).empty())
        self.assertEqual(credible_region_moc_json(empty, 0.9), {})

    def test_arguments_are_validated(self):
        for prob in [0, -0.1, 1.0001]:
            with self.subTest(prob=prob), self.assertRaises(ValueError):
                credible_region_moc(self.localization, prob)
        for max_order in [-1, 30]:
            with self.subTest(max_order=max_order), self.assertRaises(ValueError):
                credible_region_moc_json(self.localization, 0.9, max_order=max_order)


class TestLocalizationCenter(SyntheticLocalizationTestCase):
    def test_center_of_the_densest_tile(self):
        longitude, latitude = healpix_to_lonlat(0, level_to_nside(ORDER), order='nested')

        self.assertEqual(localization_center(self.localization), (float(longitude.deg), float(latitude.deg)))

    def test_ties_resolve_to_the_lowest_range(self):
        localization = build_synthetic_localization(self.event, densities_for(TIED), order=ORDER)
        longitude, latitude = healpix_to_lonlat(0, level_to_nside(ORDER), order='nested')

        self.assertEqual(localization_center(localization), (float(longitude.deg), float(latitude.deg)))

    def test_none_without_tiles(self):
        self.assertIsNone(localization_center(EventLocalizationFactory.create(nonlocalizedevent=self.event)))

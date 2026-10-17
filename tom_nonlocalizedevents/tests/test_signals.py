"""Tests of the post_save handlers that compute credible regions for new candidates and new localizations.

The factories mute these handlers, so the rows under test are created with the model managers.
"""
import math

from django.db import transaction
from django.db.backends.postgresql.psycopg_any import NumericRange
from django.test import TestCase

from astropy_healpix import healpix_to_lonlat, level_to_nside

from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.tests.factories import (NonLocalizedEventFactory, TargetFactory,
                                                    build_synthetic_localization, healpix_pixel_area,
                                                    healpix_pixel_count, healpix_pixel_range)

ORDER = 3


def densities_with_peak_at_pixel_0(peak_probability):
    """Densities of an order-3 map where pixel 0 holds ``peak_probability`` and the rest share the remainder."""
    count = healpix_pixel_count(ORDER)
    probabilities = [peak_probability] + [(1 - peak_probability) / (count - 1)] * (count - 1)
    return [probability / healpix_pixel_area(ORDER) for probability in probabilities]


def target_at_pixel(ipix):
    longitude, latitude = healpix_to_lonlat(ipix, level_to_nside(ORDER), order='nested')
    return TargetFactory.create(ra=float(longitude.deg), dec=float(latitude.deg))


class TestEventCandidatePostSave(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.event = NonLocalizedEventFactory.create()
        cls.peak_localization = build_synthetic_localization(cls.event, densities_with_peak_at_pixel_0(0.6))
        cls.flat_localization = build_synthetic_localization(cls.event, [1 / (4 * math.pi)] * 768)

    def test_a_new_candidate_gets_a_row_for_every_localization_of_its_event(self):
        candidate = EventCandidate.objects.create(target=target_at_pixel(0), nonlocalizedevent=self.event)

        percents = dict(CredibleRegion.objects.filter(candidate=candidate)
                        .values_list('localization_id', 'smallest_percent'))
        # pixel 0 alone exceeds every level of the peaked map, so it is the 25% region (plan decision I);
        # on the flat map every tile ties, so the whole sky is the 25% region as well
        self.assertEqual(percents, {self.peak_localization.pk: 25, self.flat_localization.pk: 25})

    def test_a_candidate_outside_the_largest_region_gets_no_row(self):
        candidate = EventCandidate.objects.create(target=target_at_pixel(100), nonlocalizedevent=self.event)

        self.assertEqual(CredibleRegion.objects.filter(candidate=candidate, localization=self.peak_localization)
                         .count(), 0)
        self.assertEqual(CredibleRegion.objects.get(candidate=candidate, localization=self.flat_localization)
                         .smallest_percent, 25)

    def test_the_candidate_is_scored_inside_the_transaction_that_creates_it(self):
        """Candidates created in a transaction (as a TOM's ingestion code does) are seen by the computation,
        which the 1.0.0 SQLAlchemy connection could not do."""
        with transaction.atomic():
            candidate = EventCandidate.objects.create(target=target_at_pixel(0), nonlocalizedevent=self.event)
            self.assertTrue(CredibleRegion.objects.filter(candidate=candidate).exists())

    def test_saving_an_existing_candidate_again_does_not_recompute(self):
        candidate = EventCandidate.objects.create(target=target_at_pixel(0), nonlocalizedevent=self.event)
        CredibleRegion.objects.filter(candidate=candidate).update(smallest_percent=77)

        candidate.viable = False
        candidate.save()

        self.assertEqual(set(CredibleRegion.objects.filter(candidate=candidate)
                             .values_list('smallest_percent', flat=True)), {77})


class TestEventLocalizationPostSave(TestCase):
    def setUp(self):
        self.event = NonLocalizedEventFactory.create()
        self.candidate_at_peak = EventCandidate.objects.create(target=target_at_pixel(0), nonlocalizedevent=self.event)
        self.candidate_far_away = EventCandidate.objects.create(target=target_at_pixel(100),
                                                                nonlocalizedevent=self.event)

    def test_a_new_localization_scores_the_events_candidates_after_commit(self):
        """Ingestion saves the localization first and its tiles afterwards, so the computation must wait for
        the commit to see the tiles."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            localization = EventLocalization.objects.create(nonlocalizedevent=self.event)
            SkymapTile.objects.bulk_create(
                SkymapTile(localization=localization, tile=NumericRange(*healpix_pixel_range(ORDER, ipix)),
                           probdensity=density)
                for ipix, density in enumerate(densities_with_peak_at_pixel_0(0.6)))
            self.assertFalse(CredibleRegion.objects.filter(localization=localization).exists())

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(CredibleRegion.objects.get(localization=localization, candidate=self.candidate_at_peak)
                         .smallest_percent, 25)
        self.assertFalse(CredibleRegion.objects.filter(localization=localization,
                                                       candidate=self.candidate_far_away).exists())

    def test_saving_an_existing_localization_again_queues_nothing(self):
        localization = build_synthetic_localization(self.event, densities_with_peak_at_pixel_0(0.6))

        with self.captureOnCommitCallbacks() as callbacks:
            localization.distance_mean = 100.0
            localization.save()

        self.assertEqual(callbacks, [])

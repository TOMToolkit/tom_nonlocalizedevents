"""Model factories and HEALPix helpers for the plugin's tests.

The HEALPix helpers build a complete, low-order synthetic localization from densities a test chooses, so
credible-region tests know their expected results without ingesting a FITS skymap.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import timezone
import math

from django.db.backends.postgresql.psycopg_any import NumericRange
from django.db.models import signals

import factory
from healpix_alchemy.constants import LEVEL

from tom_targets.models import Target

from tom_nonlocalizedevents.models import (CredibleRegion, EventCandidate, EventLocalization, EventSequence,
                                           NonLocalizedEvent, SkymapTile)


def healpix_pixel_count(order: int) -> int:
    """Return the number of HEALPix pixels at ``order`` (12 * 4**order)."""
    return 12 * 4 ** order


def healpix_pixel_area(order: int) -> float:
    """Return the area in steradians of one HEALPix pixel at ``order`` (the sphere divided equally)."""
    return 4 * math.pi / healpix_pixel_count(order)


def healpix_pixel_range(order: int, ipix: int) -> tuple[int, int]:
    """Return the half-open range of order-29 NESTED pixel indices covered by pixel ``ipix`` at ``order``.

    This is the encoding ``SkymapTile.tile`` stores (healpix_alchemy's): a pixel at order ``o`` covers
    ``4**(29 - o)`` consecutive order-29 pixels, starting at ``ipix`` shifted left by ``2 * (29 - o)`` bits.
    """
    shift = 2 * (LEVEL - order)
    return ipix << shift, (ipix + 1) << shift


class NonLocalizedEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NonLocalizedEvent

    event_id = factory.Faker('pystr')


class EventSequenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventSequence

    nonlocalizedevent = factory.SubFactory(NonLocalizedEventFactory)


# EventLocalization's post_save handler computes credible regions for the event's candidates after commit;
# tests of that handler create the localization without this factory.
@factory.django.mute_signals(signals.post_save)
class EventLocalizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventLocalization

    nonlocalizedevent = factory.SubFactory(NonLocalizedEventFactory)
    skymap_url = factory.Faker('url')
    date = factory.Faker('date_time', tzinfo=timezone.utc)


class TargetFactory(factory.django.DjangoModelFactory):
    """A sidereal target with coordinates in range and no related rows.

    ``tom_targets.tests.factories.SiderealTargetFactory`` draws right ascension from -90..90 and creates five
    related rows per target; the credible-region tests create many targets at chosen coordinates and need
    nothing else.
    """
    class Meta:
        model = Target

    name = factory.Sequence(lambda n: f'target-{n}')
    type = Target.SIDEREAL
    ra = factory.Faker('pyfloat', min_value=0, max_value=359, right_digits=6)
    dec = factory.Faker('pyfloat', min_value=-89, max_value=89, right_digits=6)
    epoch = 2000.0


# EventCandidate's post_save handler computes credible regions for every localization of the event; tests
# of that handler create the candidate without this factory. EventCandidate.save() still sets ``healpix``
# from the target's coordinates.
@factory.django.mute_signals(signals.post_save)
class EventCandidateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventCandidate

    target = factory.SubFactory(TargetFactory)
    nonlocalizedevent = factory.SubFactory(NonLocalizedEventFactory)


class CredibleRegionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CredibleRegion

    localization = factory.SubFactory(EventLocalizationFactory)
    candidate = factory.SubFactory(EventCandidateFactory,
                                   nonlocalizedevent=factory.SelfAttribute('..localization.nonlocalizedevent'))


class SkymapTileFactory(factory.django.DjangoModelFactory):
    """One tile of a localization: by default the n-th order-3 pixel at the uniform all-sky density."""
    class Meta:
        model = SkymapTile

    localization = factory.SubFactory(EventLocalizationFactory)
    tile = factory.Sequence(lambda n: NumericRange(*healpix_pixel_range(3, n % healpix_pixel_count(3))))
    probdensity = 1 / (4 * math.pi)


def build_synthetic_localization(event: NonLocalizedEvent, densities: Sequence[float], order: int = 3,
                                 **localization_fields) -> EventLocalization:
    """Create a localization whose tiles are every HEALPix pixel at ``order`` with the given densities.

    ``densities[ipix]`` is the probability density per steradian of NESTED pixel ``ipix``. One tile per pixel
    is stored in the depth-29 range encoding, so the tiles cover the whole sky without overlap. Divide a
    per-pixel probability by ``healpix_pixel_area(order)`` to get its density; a map is normalized when the
    densities times the pixel area sum to 1.

    Args:
        event: The event the localization belongs to.
        densities: ``12 * 4**order`` probability densities in NESTED pixel order.
        order: HEALPix order of the synthetic pixels; 3 (768 tiles) keeps tests fast.
        **localization_fields: Passed to ``EventLocalizationFactory`` (so the post_save handler is muted).

    Returns:
        The saved localization, with its tiles created in one ``bulk_create``.

    Raises:
        ValueError: If ``densities`` does not have one entry per pixel at ``order``.
    """
    expected_count = healpix_pixel_count(order)
    if len(densities) != expected_count:
        raise ValueError(f'{len(densities)} densities given, but order {order} has {expected_count} pixels')
    localization = EventLocalizationFactory.create(nonlocalizedevent=event, **localization_fields)
    SkymapTile.objects.bulk_create(
        SkymapTile(localization=localization, tile=NumericRange(*healpix_pixel_range(order, ipix)),
                   probdensity=density)
        for ipix, density in enumerate(densities)
    )
    return localization

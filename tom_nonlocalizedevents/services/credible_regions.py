"""Compute Credible regions of an event localization, with the Django ORM from the stored skymap tiles.

A localization is stored as ``SkymapTile`` rows. Each row covers a half-open range of order-29 NESTED HEALPix
pixel indices (healpix_alchemy's encoding of one multi-order skymap cell). Each carries that cell's probability
density per steradian. The credible region at probability ``p`` is the set of the densest tiles whose
cumulative probability stays within ``p``. It is derived here as a density floor and answers three
questions about it:
 1. which candidates fall inside (``update_all_credible_region_percents_for_candidates``);
 2. what its outline is for a sky map (``credible_region_moc``); and
 3. where the probability peaks (``localization_center``).

Comments marked ``tom_regions refactor`` are notes for the tom_nonlocalizedevents 2.0.0
refactor from healpix_alchemy to ``tom_regions.healpix_django``.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
import json
import logging
import math
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import F, FloatField, Func, Max, Min, Q, Sum, ValueRange, Window

from astropy_healpix import healpix_to_lonlat, level_to_nside
from mocpy import MOC
import numpy as np

from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate, EventLocalization, SkymapTile

logger = logging.getLogger(__name__)

# tom_regions refactor: both constants come from tom_regions.healpix_django.encoding in 2.0.0.
# The order of the pixel indices that tile ranges and EventCandidate.healpix store (healpix_alchemy's LEVEL).
HEALPIX_RANGE_ORDER = 29
# Area in steradians of one order-29 pixel: the sphere divided into 12 * 4**29 equal pixels. This evaluates to
# the same double as healpix_alchemy.constants.PIXEL_AREA, which the 1.0.0 SQLAlchemy query used, so the
# cumulative probabilities below reproduce its thresholds exactly.
PIXEL_AREA_STERADIANS = 4 * math.pi / (12 * 4 ** HEALPIX_RANGE_ORDER)

DEFAULT_CREDIBLE_REGION_PROBABILITIES = [0.25, 0.5, 0.75, 0.9, 0.95]


class TileArea(Func):
    """Sky area in steradians of a tile range column: its count of order-29 pixels times one pixel's area.

    The pixel area is written into the SQL as a literal rather than bound as a parameter so the expression can
    be used anywhere an aggregate or window function is (``repr`` of a Python float round-trips exactly).

    tom_regions refactor: replaced by ``tom_regions.healpix_django.functions.TileArea``.
    """
    template = '((upper(%(expressions)s) - lower(%(expressions)s))::double precision * %(pixel_area)s)'
    output_field = FloatField()

    def __init__(self, expression: str | F) -> None:
        super().__init__(expression, pixel_area=repr(PIXEL_AREA_STERADIANS))


def range_to_level_ipix(lower: int, upper: int) -> tuple[int, int]:
    """Decode a stored tile range into the HEALPix order and NESTED pixel index of the cell it encodes.

    A cell at order ``o`` covers ``4**(29 - o)`` consecutive order-29 pixels starting at a multiple of that
    count, so the order follows from the range's length and the index from its start.

    tom_regions refactor: replaced by ``tom_regions.healpix_django.encoding.range_to_level_ipix``.

    Raises:
        ValueError: If the range is not one HEALPix cell (its length is not a power of four, or it is not
            aligned to its length).
    """
    length = upper - lower
    if length <= 0 or length & (length - 1) or (length.bit_length() - 1) % 2:
        raise ValueError(f'range [{lower}, {upper}) is not a HEALPix cell: its length is not a power of four')
    if lower % length:
        raise ValueError(f'range [{lower}, {upper}) is not a HEALPix cell: it is not aligned to its length')
    level = HEALPIX_RANGE_ORDER - (length.bit_length() - 1) // 2
    return level, lower >> (2 * (HEALPIX_RANGE_ORDER - level))


def credible_region_probabilities() -> list[float]:
    """Return the credible-region probabilities to record for candidates, largest first.

    Read from ``settings.CREDIBLE_REGION_PROBABILITIES`` (a list, or the JSON string form some TOMs use),
    then from the ``CREDIBLE_REGION_PROBABILITIES`` environment variable (JSON, the 1.0.0 form), then the
    default ``[0.25, 0.5, 0.75, 0.9, 0.95]``.

    Raises:
        ImproperlyConfigured: If the configured value is not a list of probabilities in (0, 1].
    """
    configured = getattr(settings, 'CREDIBLE_REGION_PROBABILITIES', None)
    if configured is None:
        configured = os.getenv('CREDIBLE_REGION_PROBABILITIES', DEFAULT_CREDIBLE_REGION_PROBABILITIES)
    try:
        if isinstance(configured, str):
            configured = json.loads(configured)
        probabilities = sorted({float(probability) for probability in configured}, reverse=True)
    except (TypeError, ValueError) as error:
        raise ImproperlyConfigured(f'CREDIBLE_REGION_PROBABILITIES must be a list of probabilities, '
                                   f'got {configured!r}') from error
    if not probabilities or any(not 0 < probability <= 1 for probability in probabilities):
        raise ImproperlyConfigured(f'CREDIBLE_REGION_PROBABILITIES must be probabilities in (0, 1], '
                                   f'got {configured!r}')
    return probabilities


def _thresholds_and_densest(localization: EventLocalization,
                            probabilities: Sequence[float]) -> tuple[dict[float, float | None], float | None]:
    """Run the one query behind ``credible_level_thresholds`` and also return the localization's peak density.

    The tiles are ordered by density, densest first, and each tile's probability (density times area) is
    summed cumulatively with a RANGE frame, so tiles of equal density are peers that enter the sum together.
    The threshold for ``p`` is the lowest density among tiles whose cumulative probability is at most ``p``,
    which is the rule the 1.0.0 SQLAlchemy query and LIGO's own area-of-credible-region recipe use (both stop
    before the tile that crosses ``p``).
    """
    tiles = SkymapTile.objects.filter(localization=localization).annotate(
        cumulative_probability=Window(
            expression=Sum(F('probdensity') * TileArea('tile')),
            order_by=F('probdensity').desc(),
            frame=ValueRange(start=None, end=0),
        )
    )
    aggregates = {
        f'threshold_{index}': Min('probdensity', filter=Q(cumulative_probability__lte=probability))
        for index, probability in enumerate(probabilities)
    }
    aggregates['densest'] = Max('probdensity')
    result = tiles.aggregate(**aggregates)
    thresholds = {probability: result[f'threshold_{index}'] for index, probability in enumerate(probabilities)}
    return thresholds, result['densest']


def credible_level_thresholds(localization: EventLocalization,
                              probabilities: Sequence[float] | None = None) -> dict[float, float | None]:
    """Return, per probability, the probability density at the edge of that credible region.

    A tile belongs to the region at ``p`` when its density is at least the threshold. The threshold is
    ``None`` when no tile qualifies, which happens when the densest tile alone holds more than ``p`` of the
    probability (and for a localization without tiles); callers that need a region in that case take the
    densest tile, as ``update_all_credible_region_percents_for_candidates`` and ``credible_region_moc`` do.

    Args:
        localization: The localization whose tiles are summed.
        probabilities: The credible-region probabilities; ``credible_region_probabilities()`` by default.

    Returns:
        ``{probability: density threshold or None}`` for the given probabilities, in one database query.
    """
    if probabilities is None:
        probabilities = credible_region_probabilities()
    thresholds, _ = _thresholds_and_densest(localization, probabilities)
    return thresholds


def _density_floors(localization: EventLocalization, probabilities: Sequence[float]) -> dict[float, float | None]:
    """Return the thresholds with the densest tile's density standing in where no tile qualifies.

    ``None`` remains only for a localization without tiles.
    """
    thresholds, densest = _thresholds_and_densest(localization, probabilities)
    return {probability: densest if threshold is None else threshold
            for probability, threshold in thresholds.items()}


def tile_densities_at_points(localization: EventLocalization, points: Sequence[int]) -> list[float | None]:
    """Return the probability density of the tile containing each point, in the order the points were given.

    ``points`` are order-29 NESTED pixel indices (what ``EventCandidate.healpix`` stores). A point outside
    every tile of the localization maps to ``None``. The tiles of a skymap do not overlap; should they, the
    highest density wins, which is what "inside a region whose floor the tile meets" means for any of them.

    tom_regions refactor: the containment operator and the encoding are the ``healpix_django`` ones.
    """
    if not points:
        return []
    # One query for every point: Postgres expands the array with its positions and joins each point to the
    # tile containing it. The ORM has no join on an unnested array, so this is the one raw statement here.
    sql = (
        'SELECT points.ordinality, MAX(tiles.probdensity) '
        'FROM unnest(%s::bigint[]) WITH ORDINALITY AS points(point, ordinality) '
        f'LEFT JOIN {connection.ops.quote_name(SkymapTile._meta.db_table)} AS tiles '
        'ON tiles.localization_id = %s AND tiles.tile @> points.point '
        'GROUP BY points.ordinality ORDER BY points.ordinality'
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [[int(point) for point in points], localization.pk])
        return [density for _, density in cursor.fetchall()]


def update_all_credible_region_percents_for_candidates(eventlocalization: EventLocalization,
                                                       event_candidate_ids: Iterable[int] | None = None,
                                                       probabilities: Sequence[float] | None = None) -> None:
    """Record, for each candidate, the smallest credible region of the localization that contains it.

    Creates or updates one ``CredibleRegion`` row per candidate that lies inside the largest of the regions;
    candidates outside it get no row, and existing rows are never deleted. Same name, signature and results as
    the 1.0.0 function in ``healpix_utils`` for committed data, with two differences: candidates saved in the
    current transaction are seen (the SQLAlchemy engine was a second connection), and when a single tile holds
    more than a region's probability that tile is the region instead of no region at all.

    Args:
        eventlocalization: The localization whose regions are evaluated.
        event_candidate_ids: Primary keys of the candidates to evaluate; empty or ``None`` means every
            candidate of the localization's event (the 1.0.0 reading of an empty list, kept).
        probabilities: The credible-region probabilities to consider; ``credible_region_probabilities()`` by
            default. A single probability records that one region for the candidates inside it and leaves the
            other candidates' rows as they are.
    """
    if not event_candidate_ids:
        candidates = eventlocalization.nonlocalizedevent.candidates.all()
    else:
        candidates = EventCandidate.objects.filter(pk__in=event_candidate_ids)
    candidates = list(candidates.values_list('pk', 'healpix'))
    if not candidates:
        return

    if probabilities is None:
        probabilities = credible_region_probabilities()
    floors = _density_floors(eventlocalization, probabilities)
    densities = tile_densities_at_points(eventlocalization, [healpix for _, healpix in candidates])
    for (candidate_id, _), density in zip(candidates, densities):
        if density is None:
            continue
        containing = [probability for probability in probabilities
                      if floors[probability] is not None and density >= floors[probability]]
        if not containing:
            continue
        # round(), not int(): int(0.29 * 100) is 28
        CredibleRegion.objects.update_or_create(
            candidate_id=candidate_id,
            localization=eventlocalization,
            defaults={'smallest_percent': round(min(containing) * 100)},
        )


def _validate_probability(prob: float) -> None:
    if not 0 < prob <= 1:
        raise ValueError(f'a credible-region probability must be in (0, 1], got {prob!r}')


def credible_region_moc(localization: EventLocalization, prob: float) -> MOC:
    """Return the credible region at ``prob`` as a multi-order coverage map of the tiles inside it.

    The MOC is at order 29 (the stored encoding), so it is exact; degrade it for display. A localization
    without tiles gives an empty MOC.

    Raises:
        ValueError: If ``prob`` is not in (0, 1].
    """
    _validate_probability(prob)
    floor = _density_floors(localization, [prob])[prob]
    if floor is None:
        return MOC.new_empty(HEALPIX_RANGE_ORDER)
    # tom_regions refactor: the tile ranges come out of MOCMixin.hpx in 2.0.0.
    ranges = (SkymapTile.objects.filter(localization=localization, probdensity__gte=floor)
              .values_list('tile', flat=True))
    return MOC.from_depth29_ranges(
        HEALPIX_RANGE_ORDER,
        np.array([(tile.lower, tile.upper) for tile in ranges], dtype=np.uint64).reshape(-1, 2),
    )


def credible_region_moc_json(localization: EventLocalization, prob: float, max_order: int = 10) -> dict[str, list[int]]:
    """Return the credible region at ``prob`` in the MOC JSON form Aladin Lite draws (``{"order": [ipix, ...]}``).

    Cells finer than ``max_order`` are degraded to it, which keeps the payload a few kilobytes at the
    default order 10 (about 3.4 arcmin cells). Orders with no cells are left out; an empty region is ``{}``.

    Raises:
        ValueError: If ``prob`` is not in (0, 1] or ``max_order`` is not in 0..29.
    """
    if not 0 <= max_order <= HEALPIX_RANGE_ORDER:
        raise ValueError(f'max_order must be in 0..{HEALPIX_RANGE_ORDER}, got {max_order!r}')
    moc = credible_region_moc(localization, prob)
    if max_order < moc.max_order:
        moc = moc.degrade_to_order(max_order)
    return {order: pixels for order, pixels in moc.serialize(format='json').items() if pixels}


def localization_center(localization: EventLocalization) -> tuple[float, float] | None:
    """Return the ICRS right ascension and declination in degrees of the densest tile's center.

    Ties are broken by the lowest tile range so the answer is stable. ``None`` for a localization without
    tiles.
    """
    densest = (SkymapTile.objects.filter(localization=localization)
               .order_by('-probdensity', 'tile').values_list('tile', flat=True).first())
    if densest is None:
        return None
    level, ipix = range_to_level_ipix(densest.lower, densest.upper)
    longitude, latitude = healpix_to_lonlat(ipix, level_to_nside(level), order='nested')
    return float(longitude.deg), float(latitude.deg)

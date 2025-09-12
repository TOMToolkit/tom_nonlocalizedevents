''' This set of methods supports generating and querying healpix MOC maps for GW events,
    using healpix_alchemy and model mappings with sql_alchemy queries.
'''
from astropy.table import Table
from tom_nonlocalizedevents.models import (NonLocalizedEvent, EventCandidate, SkymapTile,
                                           EventLocalization, CredibleRegion)
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.db.utils import IntegrityError
from healpix_alchemy.constants import HPX, LEVEL
from healpix_alchemy.types import Tile, Point
import sqlalchemy as sa
from sqlalchemy.orm import relationship, declarative_base, Session
from astropy import units as u
import astropy_healpix as ah
from mocpy import MOC
from ligo.skymap import distance
from dateutil.parser import parse
import numpy as np
import os
import hashlib
from io import BytesIO
import uuid
import sys
import json
import logging

# from django.db.models import Sum, Subquery, F, Min
# from tom_nonlocalizedevents_base.settings import DATABASES


logger = logging.getLogger(__name__)

#
# SQLAlchemy Engine Configuration
#  see https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine

SA_DB_CONNECTION_URL = os.getenv(
    'SA_DB_CONNECTION_URL',
    (f"postgresql://{settings.DATABASES['default']['USER']}:{settings.DATABASES['default']['PASSWORD']}"
     f"@{settings.DATABASES['default']['HOST']}:{settings.DATABASES['default']['PORT']}"
     f"/{settings.DATABASES['default']['NAME']}"))
POOL_RECYCLE = 4 * 60 * 60
POOL_SIZE = os.getenv('POOL_SIZE', 5)
MAX_OVERFLOW = os.getenv('MAX_OVERFLOW', 10)

CREDIBLE_REGION_PROBABILITIES = sorted(json.loads(os.getenv(
    'CREDIBLE_REGION_PROBABILITIES', '[0.25, 0.5, 0.75, 0.9, 0.95]')), reverse=True)

Base = declarative_base()
sa_engine = sa.create_engine(
    SA_DB_CONNECTION_URL,
    pool_recycle=POOL_RECYCLE,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW
)


def uniq_to_bigintrange(value):
    level, ipix = ah.uniq_to_level_ipix(value)
    shift = 2 * (LEVEL - level)
    return (ipix << shift, (ipix + 1) << shift)


def sequence_to_bigintrange(sequence):
    return f'[{sequence[0]},{sequence[1]})'


def tiles_from_moc(moc):
    return (f'[{lo},{hi})' for lo, hi in moc.to_depth29_ranges)


def tiles_from_polygon_skycoord(polygon):
    return tiles_from_moc(MOC.from_polygon_skycoord(polygon.transform_to(HPX.frame)))


def get_confidence_regions(skymap: Table):
    """ This helper method takes in the astropy Table skymap and attempts to parse out
        the 50 and 90 area confidence values. It returns a tuple of (area_50, area_90).
        See https://emfollow.docs.ligo.org/userguide/tutorial/multiorder_skymaps.html.
    """
    try:
        # Sort the pixels of the sky map by descending probability density
        skymap.sort('PROBDENSITY', reverse=True)
        # Find the area of each pixel
        level, ipix = ah.uniq_to_level_ipix(skymap['UNIQ'])
        pixel_area = ah.nside_to_pixel_area(ah.level_to_nside(level))
        # Calculate the probability within each pixel: the pixel area times the probability density
        prob = pixel_area * skymap['PROBDENSITY']
        # Calculate the cumulative sum of the probability
        cumprob = np.cumsum(prob)
        # Find the pixel for which the probability sums to 0.5 (0.9)
        index_50 = cumprob.searchsorted(0.5)
        index_90 = cumprob.searchsorted(0.9)
        # The area of the 50% (90%) credible region is simply the sum of the areas of the pixels up to that probability
        area_50 = pixel_area[:index_50].sum().to_value(u.deg ** 2.)
        area_90 = pixel_area[:index_90].sum().to_value(u.deg ** 2.)

        return area_50, area_90
    except Exception as e:
        logger.error(f'Unable to parse raw skymap for OBJECT {skymap.meta["OBJECT"]} for confidence regions: {e}')

    return None, None


def get_skymap_version(nle: NonLocalizedEvent, skymap_hash: uuid, is_combined: bool) -> int:
    """ This method gets the most recent previous sequence of this superevent and checks if the skymap has changed.
        It returns the 'version' of the skymap, which can be used to retrieve the proper file and image files from
        gracedb. This is a hack because IGWN GWAlerts no longer have any way of knowing which skymap version they
        reference.
    """
    try:
        for sequence in nle.sequences.all().reverse():
            if (is_combined and sequence.external_coincidence and sequence.external_coincidence.localization
                    and sequence.external_coincidence.localization.skymap_hash != skymap_hash):
                return sequence.external_coincidence.localization.skymap_version + 1
            elif (not is_combined) and sequence.localization and sequence.localization.skymap_hash != skymap_hash:
                return sequence.localization.skymap_version + 1
        return 0
    except NonLocalizedEvent.DoesNotExist:
        return 0  # The nonlocalizedevent doesnt exist in our system yet, so this must be the first skymap version


def create_localization_for_skymap(nonlocalizedevent: NonLocalizedEvent, skymap_bytes: bytes, skymap_url: str = '',
                                   is_combined: bool = False, pipeline: str = ''):
    """ Create localization from skymap bytes and related fields """
    logger.info(f"Creating localization for {nonlocalizedevent.event_id} with skymap {skymap_url}")
    skymap_hash = hashlib.md5(skymap_bytes).hexdigest()
    skymap_uuid = uuid.UUID(skymap_hash)
    try:
        localization = EventLocalization.objects.get(nonlocalizedevent=nonlocalizedevent, skymap_hash=skymap_uuid)
    except EventLocalization.DoesNotExist:
        skymap = Table.read(BytesIO(skymap_bytes))
        is_burst = pipeline in ['CWB', 'oLIB', 'MLy']
        if not is_burst:
            distance_mean = skymap.meta['DISTMEAN']
            distance_std = skymap.meta['DISTSTD']
            row_dist_mean, row_dist_std, _ = distance.parameters_to_moments(
                skymap['DISTMU'], skymap['DISTSIGMA'])
        else:
            distance_mean = 0
            distance_std = 0
            row_dist_mean = None
            row_dist_std = None
        date = parse(skymap.meta['DATE']).replace(tzinfo=timezone.utc) if 'DATE' in skymap.meta else None
        skymap_version = get_skymap_version(nonlocalizedevent, skymap_hash=skymap_uuid, is_combined=is_combined)
        if not skymap_url:
            base_url = f"https://gracedb.ligo.org/api/superevents/{nonlocalizedevent.event_id}/files/"
            if pipeline in ['pycbc', 'gstlal', 'MBTA', 'MBTAOnline', 'spiir']:
                if is_combined:
                    skymap_url = f"{base_url}combined-ext.multiorder.fits,{skymap_version}"
                else:
                    skymap_url = f"{base_url}bayestar.multiorder.fits,{skymap_version}"
            elif pipeline == 'CWB' and not is_combined:
                skymap_url = f"{base_url}cwb.multiorder.fits,{skymap_version}"
            elif pipeline == 'oLIB' and not is_combined:
                skymap_url = f"{base_url}olib.multiorder.fits,{skymap_version}"
        area_50, area_90 = get_confidence_regions(skymap)

        with transaction.atomic():
            try:
                localization, is_new = EventLocalization.objects.get_or_create(
                    nonlocalizedevent=nonlocalizedevent,
                    skymap_hash=skymap_uuid,
                    defaults={
                        'distance_mean': distance_mean,
                        'distance_std': distance_std,
                        'skymap_version': skymap_version,
                        'skymap_url': skymap_url,
                        'area_50': area_50,
                        'area_90': area_90,
                        'date': date
                    }
                )
                if not is_new:
                    # This is added to protect against race conditions where the localization has already been added
                    return localization
                for i, row in enumerate(skymap):
                    # This is necessary to make sure we don't get an underflow error in postgres
                    # when operating with the probdensity float field
                    probdensity = row['PROBDENSITY'] if row['PROBDENSITY'] > sys.float_info.min else 0
                    SkymapTile.objects.create(
                        localization=localization,
                        tile=uniq_to_bigintrange(row['UNIQ']),
                        probdensity=probdensity,
                        distance_mean=row_dist_mean[i] if row_dist_mean is not None else 0,
                        distance_std=row_dist_std[i] if row_dist_std is not None else 0
                    )
            except IntegrityError as e:
                if 'unique constraint' in e.message:
                    return EventLocalization.objects.get(nonlocalizedevent=nonlocalizedevent, skymap_hash=skymap_hash)
                raise e
    return localization


# healpix_alchemy models pointing to django ORM models, for building a sql alchemy query
class SaSkymap(Base):
    __tablename__ = 'tom_nonlocalizedevents_eventlocalization'
    id = sa.Column(sa.Integer, primary_key=True)
    tiles = relationship(lambda: SaSkymapTile)


class SaSkymapTile(Base):
    __tablename__ = 'tom_nonlocalizedevents_skymaptile'
    id = sa.Column(sa.Integer, primary_key=True)
    localization_id = sa.Column(sa.ForeignKey(SaSkymap.id), primary_key=True)
    tile = sa.Column(Tile, primary_key=True, index=True)
    probdensity = sa.Column(sa.Float, nullable=False)
    distance_mean = sa.Column(sa.Float, nullable=False)
    distance_std = sa.Column(sa.Float, nullable=False)


class SaTarget(Base):
    __tablename__ = 'tom_targets_target'
    id = sa.Column(sa.Integer, primary_key=True)
    distance = sa.Column(sa.Float, nullable=True)
    distance_err = sa.Column(sa.Float, nullable=True)


class SaEventCandidate(Base):
    __tablename__ = 'tom_nonlocalizedevents_eventcandidate'
    id = sa.Column(sa.Integer, primary_key=True)
    target_id = sa.Column(sa.ForeignKey(SaTarget.id), primary_key=True)
    healpix = sa.Column(Point, index=True, nullable=False)


def update_all_credible_region_percents_for_candidates(eventlocalization, event_candidate_ids=None):
    ''' This helper function runs through the defined set of discrete credible region probabilities
        and creates or updates the probability of a credible region for each of the event candidates
        that fall within the event localization's credible region of that priority
    '''
    if not event_candidate_ids:
        event_candidate_ids = list(eventlocalization.nonlocalizedevent.candidates.values_list('pk', flat=True))
    for probability in CREDIBLE_REGION_PROBABILITIES:
        update_credible_region_percent_for_candidates(eventlocalization, probability, event_candidate_ids)


def update_credible_region_percent_for_candidates(eventlocalization, prob, event_candidate_ids=None):
    ''' This function creates a credible region linkage with probability prob for each of the event candidate
        ids supplied if they fall within that prob for the event location specified.
    '''
    if not event_candidate_ids:
        event_candidate_ids = list(eventlocalization.nonlocalizedevent.candidates.values_list('pk', flat=True))

    with Session(sa_engine) as session:

        cum_prob = sa.func.sum(
            SaSkymapTile.probdensity * SaSkymapTile.tile.area
        ).over(
            order_by=SaSkymapTile.probdensity.desc()
        ).label(
            'cum_prob'
        )

        subquery = sa.select(
            SaSkymapTile.probdensity,
            cum_prob
        ).filter(
            SaSkymapTile.localization_id == eventlocalization.id
        ).subquery()

        min_probdensity = sa.select(
            sa.func.min(subquery.columns.probdensity)
        ).filter(
            subquery.columns.cum_prob <= prob
        ).scalar_subquery()

        query = sa.select(
            SaEventCandidate.id
        ).filter(
            SaEventCandidate.id.in_(event_candidate_ids),
            SaSkymapTile.localization_id == eventlocalization.id,
            SaSkymapTile.tile.contains(SaEventCandidate.healpix),
            SaSkymapTile.probdensity >= min_probdensity
        )

        results = session.execute(query)

        for sa_event_candidate_id in results:
            CredibleRegion.objects.update_or_create(
                candidate=EventCandidate.objects.get(id=sa_event_candidate_id[0]),
                localization=eventlocalization,
                defaults={
                    'smallest_percent': int(prob * 100.0)
                }
            )

# def point_in_range_django(eventlocalization, prob):
# This code is a beginning attempt to translate the healpix_alchemy query from sql_alchemy to django ORM
# It is non-functional and needs more work to get right.
#     event_candidate_ids = list(eventlocalization.nonlocalizedevent.candidates.values_list('pk', flat=True))

#     SkymapTile.objects.order_by('-probdensity').aggregate(cum_prob=Sum(F('probdensity') * F('tile__area')))

#     cum_prob = SkymapTile.objects.order_by('-probdensity').alias(cum_prob=Sum(F('probdensity') *
#                F('tile__area'))).annotate(cum_prob=F('cum_prob'),
#     )
#     min_probdensity = SkymapTile.objects.order_by('-probdensity').alias(
#         cum_prob=Sum(F('probdensity') * F('tile__area')), min_probdensity=Min(F('probdensity'))).annotate(
#         cum_prob=F('cum_prob'), min_probdensity=F('min_probdensity')
#     ).filter(
#         localization__id=eventlocalization.id,
#         cum_prob__lte=prob,
#         probdensity__gte=min_probdensity,
#         tile__contains=
#         localization__nonlocalizedevent__candidates
#     )
#     candidates = EventCandidate.objects.filter(
#         nonlocalizedevent=eventlocalization.nonlocalizedevent,
#         eventlocalization__tiles__contains(healpix)
#     ).values('id')

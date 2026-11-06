"""What the sky map draws for the selected alert: credible regions, the event's candidates, the sun and the moon.

The data crosses into JavaScript as JSON (``json_script``), so it is a pydantic model and the JavaScript
(``static/tom_nonlocalizedevents/js/event_sky_map.js``) reads exactly these field names.
"""
from __future__ import annotations

from datetime import datetime
import math

from django.urls import reverse
from django.utils import timezone

from astroplan import moon_illumination
from astropy.coordinates import get_body
from astropy.time import Time
from pydantic import BaseModel, Field

from tom_nonlocalizedevents.models import EventLocalization, EventSequence, NonLocalizedEvent
from tom_nonlocalizedevents.services.credible_regions import localization_center

# The credible levels drawn for the selected alert's localization: the outer one as an outline, the inner one
# filled. The combined (external coincidence) localization is drawn at the outer level in a second colour.
SKY_MAP_LEVELS = [(0.9, False), (0.5, True)]
LOCALIZATION_COLOR = '#1f77b4'
COMBINED_LOCALIZATION_COLOR = '#ff7f0e'
# Display conventions for the avoidance regions around the sun and the moon; the moon's grows with its
# illumination (3 degrees at new moon, 45 at full moon). They are shown for orientation, not enforced.
SUN_EXCLUSION_RADIUS_DEG = 15.0
MOON_EXCLUSION_RADIUS_DEG_NEW = 3.0
MOON_EXCLUSION_RADIUS_DEG_FULL = 45.0
# Field of view: a multiple of the 90% region's diameter, within bounds; all-sky without an area.
FOV_PER_REGION_DIAMETER = 3.0
FOV_MIN_DEG = 10.0
FOV_MAX_DEG = 360.0


class SkyMapMoc(BaseModel):
    """One credible region to draw, fetched by the JavaScript from the MOC endpoint."""
    url: str
    label: str
    color: str
    fill: bool


class SkyMapMarker(BaseModel):
    """A candidate target."""
    name: str
    ra: float
    dec: float
    viable: bool
    url: str


class SkyMapBody(BaseModel):
    """The sun or the moon with its avoidance radius."""
    name: str
    ra: float
    dec: float
    exclusion_radius_deg: float
    illumination: float | None = None


class SkyMapLayers(BaseModel):
    """Everything the sky map draws for one alert."""
    center_ra: float | None = None
    center_dec: float | None = None
    fov_deg: float = FOV_MAX_DEG
    mocs: list[SkyMapMoc] = Field(default_factory=list)
    candidates: list[SkyMapMarker] = Field(default_factory=list)
    sun: SkyMapBody
    moon: SkyMapBody
    at: datetime


def sun_and_moon(at: datetime | None = None) -> tuple[SkyMapBody, SkyMapBody, datetime]:
    """Return the geocentric sun and moon positions at ``at`` (now by default) with their avoidance radii."""
    at = at or timezone.now()
    time = Time(at)
    sun = get_body('sun', time)
    moon = get_body('moon', time)
    illumination = float(moon_illumination(time))
    moon_radius = MOON_EXCLUSION_RADIUS_DEG_NEW + (MOON_EXCLUSION_RADIUS_DEG_FULL - MOON_EXCLUSION_RADIUS_DEG_NEW) * (
        illumination)
    return (SkyMapBody(name='Sun', ra=float(sun.ra.deg), dec=float(sun.dec.deg),
                       exclusion_radius_deg=SUN_EXCLUSION_RADIUS_DEG),
            SkyMapBody(name='Moon', ra=float(moon.ra.deg), dec=float(moon.dec.deg),
                       exclusion_radius_deg=moon_radius, illumination=illumination),
            at)


def _moc_layers(localization: EventLocalization, label: str, color: str, levels=SKY_MAP_LEVELS) -> list[SkyMapMoc]:
    url = reverse('nonlocalizedevents:localization-moc', args=(localization.pk,))
    return [SkyMapMoc(url=f'{url}?prob={probability}', label=f'{label} {probability:.0%}', color=color, fill=fill)
            for probability, fill in levels]


def field_of_view(localization: EventLocalization | None) -> float:
    """Return the field of view in degrees that shows the localization's 90% region with room around it."""
    if localization is None or not localization.area_90:
        return FOV_MAX_DEG
    diameter = 2 * math.sqrt(localization.area_90 / math.pi)
    return min(max(FOV_PER_REGION_DIAMETER * diameter, FOV_MIN_DEG), FOV_MAX_DEG)


def sky_map_layers(event: NonLocalizedEvent, selected_sequence: EventSequence | None,
                   at: datetime | None = None) -> SkyMapLayers:
    """Build the sky-map layers for ``event`` with ``selected_sequence``'s localization(s) drawn.

    Without a localization (no alert, a retraction, a burst without a skymap) the map still shows the
    candidates, the sun and the moon, all-sky.
    """
    sun, moon, at = sun_and_moon(at)
    layers = SkyMapLayers(sun=sun, moon=moon, at=at)
    localization = selected_sequence.localization if selected_sequence else None
    if localization is not None:
        layers.mocs.extend(_moc_layers(localization, 'localization', LOCALIZATION_COLOR))
        center = localization_center(localization)
        if center is not None:
            layers.center_ra, layers.center_dec = center
        layers.fov_deg = field_of_view(localization)
        coincidence = selected_sequence.external_coincidence
        if coincidence is not None and coincidence.localization is not None:
            layers.mocs.extend(_moc_layers(coincidence.localization, 'combined', COMBINED_LOCALIZATION_COLOR,
                                           levels=SKY_MAP_LEVELS[:1]))
    for candidate in event.candidates.select_related('target').order_by('target__name'):
        layers.candidates.append(SkyMapMarker(
            name=candidate.target.name, ra=candidate.target.ra, dec=candidate.target.dec, viable=candidate.viable,
            url=reverse('targets:detail', args=(candidate.target.pk,))))
    return layers

"""The attributes of the selected alert shown in the event header, and the formatting of science quantities.

The formatting helpers are ports of SAGUARO's template filters. ``tables.py`` imports them from here
for the event list.
"""
from __future__ import annotations

import math
from typing import Any, NamedTuple

from tom_nonlocalizedevents.models import EventLocalization, EventSequence
from tom_nonlocalizedevents.services.alerts import (GenericAlert, GravitationalWaveAlert, parse_alert,
                                                    parse_external_coincidence)

# SAGUARO's conversion constant: false-alarm rate in Hz -> inverse FAR in years
HZ_TO_INVERSE_YEARS = 3.168808781402895e-08
SI_PREFIXES = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q']


class HeaderAttribute(NamedTuple):
    """One row of the header's attribute list: a label, its text, an optional hover explanation, and an optional
    URL that turns the text into a link."""
    label: str
    value: str
    title: str = ''
    url: str = ''


def format_inverse_far(far: float | None) -> str:
    """Format a false-alarm rate (Hz) as a human-readable inverse FAR, SAGUARO-style.

    Years with SI prefixes above one year (kyr, Myr, ...), days below one year.
    Significant CBC alerts have 1/FAR > 0.5 yr.
    """
    if not far:
        return ''
    inv_far = HZ_TO_INVERSE_YEARS / far
    if inv_far > 1.:
        magnitude = int(math.log10(inv_far) / 3.)
        if magnitude < len(SI_PREFIXES):
            inv_far *= 1000. ** -magnitude
            unit = SI_PREFIXES[magnitude] + 'yr'
        else:
            unit = 'yr'
    else:
        inv_far *= 365.25
        unit = 'd'
    if inv_far >= 1000.:
        return f'{inv_far:.0e} {unit}'
    elif inv_far > 10.:
        return f'{inv_far:.0f} {unit}'
    return f'{inv_far:.1f} {unit}'


def format_distance(localization: EventLocalization | None) -> str:
    """Format a localization's luminosity distance as 'mean ± std', SAGUARO-style."""
    if localization is None or not localization.distance_mean:
        return ''
    distance_mean = localization.distance_mean
    distance_std = localization.distance_std
    if distance_mean < 1000.:
        unit = 'Mpc'
    else:
        distance_mean /= 1000.
        distance_std /= 1000.
        unit = 'Gpc'
    if distance_mean > 10.:
        return f'{distance_mean:.0f} ± {distance_std:.0f} {unit}'
    return f'{distance_mean:.1f} ± {distance_std:.1f} {unit}'


def format_area(area: float | None) -> str:
    """Format a credible-region area in square degrees without decimals; a dash when it is unknown."""
    return '–' if area is None else f'{area:,.0f}'


def format_percent(value: Any) -> str:
    """Render a 0-1 probability as a whole percent; empty string when absent."""
    try:
        return f'{float(value):.0%}'
    except (TypeError, ValueError):
        return ''


def most_likely_classification(details: dict | None) -> str:
    """Extract the most likely classification from a sequence's details dictionary, SAGUARO-style."""
    if not details:
        return ''
    return GravitationalWaveAlert.model_validate(details).most_likely_classification()


def _gravitational_wave_attributes(sequence: EventSequence, alert: GravitationalWaveAlert) -> list[HeaderAttribute]:
    """Return the header rows of a gravitational-wave alert, in display order; a row is left out when the alert
    does not carry its quantity."""
    attributes = []
    # Event time (when the signal arrived); 1/FAR (1/false-alarm-rate; is this noise?)
    if alert.time:
        attributes.append(HeaderAttribute('Event time', alert.time.strftime('%Y-%m-%d %H:%M:%S') + ' UTC'))
    if alert.far:
        attributes.append(HeaderAttribute('1 / FAR', format_inverse_far(alert.far), 'inverse false-alarm rate'))

    # Classification (BNS, NSBH, BBH, Terrestrial), with probabilities; most probable first
    if alert.classification:
        attributes.append(HeaderAttribute('Classification', ', '.join(
            f'{name} {format_percent(probability)}' for name, probability in alert.classification_by_probability())))
    # Properties (HasNS, HasRemnant, HasMassGap, HasSSM) as probabilities
    if alert.properties:
        attributes.append(HeaderAttribute('Properties', ' · '.join(
            f'{name} {probability:.2f}' for name, probability in alert.properties.items())))

    # Burst: burst searches report a duration and a central frequency (instead of a classification)
    if alert.is_burst:
        if alert.duration is not None:
            attributes.append(HeaderAttribute('Duration', f'{alert.duration * 1000:.0f} ms'))
        if alert.central_frequency is not None:
            attributes.append(HeaderAttribute('Central frequency', f'{alert.central_frequency:.0f} Hz'))

    # where it is: luminosity distance and the areas of the 50% and 90% credible regions, from the localization
    localization = sequence.localization
    if localization is not None:
        if format_distance(localization):
            attributes.append(HeaderAttribute('Distance', format_distance(localization)))
        if localization.area_50 is not None or localization.area_90 is not None:
            attributes.append(HeaderAttribute(
                'Area 50% / 90%', f'{format_area(localization.area_50)} / {format_area(localization.area_90)} deg²'))

    # which detectors observed it, and which pipeline and search found it
    if alert.instruments:
        attributes.append(HeaderAttribute('Instruments', ' '.join(alert.instruments)))
    pipeline_and_search = ' / '.join(part for part in (alert.pipeline, alert.search) if part)
    if pipeline_and_search:
        attributes.append(HeaderAttribute('Pipeline / search', pipeline_and_search))

    # a coincident external trigger (observatory, search, notice id, time offset, joint false-alarm rate)
    coincidence = parse_external_coincidence(sequence)
    if coincidence is not None:
        parts = [part for part in (coincidence.observatory, coincidence.search) if part]
        if coincidence.gcn_notice_id is not None:
            parts.append(f'notice {coincidence.gcn_notice_id}')
        if coincidence.time_difference is not None:
            parts.append(f'Δt {coincidence.time_difference:+.1f} s')
        joint_far = coincidence.time_sky_position_coincidence_far or coincidence.time_coincidence_far
        if joint_far:
            parts.append(f'joint 1 / FAR {format_inverse_far(joint_far)}')
        attributes.append(HeaderAttribute('External coincidence', ', '.join(parts) or 'yes'))
    return attributes


def _generic_attributes(alert: GenericAlert) -> list[HeaderAttribute]:
    return [HeaderAttribute(str(key).replace('_', ' '), str(value)) for key, value in alert.scalar_items()]


def header_attributes(sequence: EventSequence) -> list[HeaderAttribute]:
    """Return the attribute rows the event header shows for ``sequence``.

    Gravitational-wave alerts get the IGWN quantities (time, 1/FAR, classification, properties, burst
    parameters, distance, areas, instruments, pipeline and search, external coincidence); other event types
    list the scalar entries of their alert dictionary until they have schemas of their own.
    """
    alert = parse_alert(sequence)
    if isinstance(alert, GravitationalWaveAlert):
        attributes = _gravitational_wave_attributes(sequence, alert)
    else:
        attributes = _generic_attributes(alert)
    # the alert as Hermes received it, when the ingestion recorded the Hermes message id
    if sequence.hermes_url:
        attributes.append(HeaderAttribute('Hermes message', 'this alert on Hermes', url=sequence.hermes_url))
    return attributes

"""django-tables2 table for the event list page, built on TOM Toolkit's HTMXTable.

Columns and their presentation follow SAGUARO's event list pages (minus their
site-specific survey columns), and the formatting helpers are ports of their
template filters, so the two TOMs render science quantities identically.
"""
import math
from typing import Any

from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

import django_tables2 as tables

from tom_common.htmx_table import HTMXTable

from tom_nonlocalizedevents.models import EventLocalization, EventSequence, NonLocalizedEvent

# SAGUARO's conversion constant: false-alarm rate in Hz -> inverse FAR in years
HZ_TO_INVERSE_YEARS = 3.168808781402895e-08
SI_PREFIXES = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q']


def latest_sequence(event: NonLocalizedEvent) -> EventSequence | None:
    """Return the event's latest EventSequence from the prefetched relation.

    Materializes the prefetch cache (rather than calling ``.last()``, which
    issues a query per row); EventSequence.Meta already orders by sequence_id.
    """
    sequences = list(event.sequences.all())
    return sequences[-1] if sequences else None


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


def most_likely_classification(details: dict | None) -> str:
    """Extract the most likely classification from a sequence's details, SAGUARO-style.

    SSM searches report as SSM; CBC alerts report the max-probability entry of
    the classification dict; everything else (bursts) reports its group.
    """
    if not details:
        return ''
    if details.get('search') == 'SSM':
        return 'SSM'
    if details.get('group') == 'CBC':
        classification = details.get('classification') or {}
        if classification:
            return max(classification, key=classification.get)
        return ''
    return details.get('group') or ''


def _percent(value: Any) -> str:
    """Render a 0-1 probability as a whole percent; empty string when absent."""
    try:
        return f'{float(value):.0%}'
    except (TypeError, ValueError):
        return ''


def _row_is_significant(event: NonLocalizedEvent) -> bool:
    sequence = latest_sequence(event)
    return bool(sequence and sequence.details and sequence.details.get('significant'))


class NonLocalizedEventTable(HTMXTable):
    """SAGUARO-shaped event table: science-triage columns from each event's latest sequence.

    The science columns render from the prefetched latest sequence (see the
    list view's queryset), so they are display-only (orderable=False) -- the
    equivalent server-side comparisons live in NonLocalizedEventFilterSet.
    """
    event_id = tables.Column(verbose_name='Event ID')
    event_type = tables.Column(verbose_name='Type')
    inverse_far = tables.Column(verbose_name='1/FAR', orderable=False, empty_values=(),
                                attrs={'th': {'title': 'Inverse False Alarm Rate'}})
    classification = tables.Column(verbose_name='Class.', orderable=False, empty_values=(),
                                   attrs={'th': {'title': 'Most Likely Classification'}})
    distance = tables.Column(verbose_name='Distance', orderable=False, empty_values=())
    # headers spelled out to match the filter labels (SAGUARO abbreviates these
    # to 'NS?' / 'Bright?', which obscures the filter-column correspondence)
    has_ns = tables.Column(verbose_name='HasNS', orderable=False, empty_values=(),
                           attrs={'th': {'title': 'Has Neutron Star?'}})
    has_remnant = tables.Column(verbose_name='HasRemnant', orderable=False, empty_values=(),
                                attrs={'th': {'title': 'Has Remnant (is it bright)?'}})
    external_links = tables.Column(verbose_name='Ext. Links', orderable=False, empty_values=())

    partial_template_name = 'tom_nonlocalizedevents/partials/nonlocalizedevent_table_partial.html'

    class Meta(HTMXTable.Meta):
        model = NonLocalizedEvent
        fields = ('event_id', 'event_type')
        sequence = ('event_id', 'event_type', 'inverse_far', 'classification', 'distance',
                    'has_ns', 'has_remnant', 'external_links')
        # HTMXTable declares a 'selection' CheckBoxColumn bound to a grouping-form this
        # page doesn't have; inherited declared columns render regardless of Meta.fields,
        # so Meta.exclude is the only way to drop it.
        exclude = ('selection',)
        # SAGUARO renders events whose latest sequence is significant in bold
        row_attrs = {'class': lambda record: 'fw-bold' if _row_is_significant(record) else ''}

    def render_event_id(self, record: NonLocalizedEvent, value: str) -> str:
        """Link to the detail page, with SAGUARO's state glyphs (X retracted, check confirmed)."""
        url = reverse('nonlocalizedevents:event-detail', args=(record.event_id,))
        sequence = latest_sequence(record)
        if record.state == NonLocalizedEvent.NonLocalizedEventState.RETRACTED:
            glyph = mark_safe(' <span title="retracted">&#x274c;</span>')
        elif sequence and sequence.event_subtype and sequence.event_subtype.upper() != 'PRELIMINARY':
            glyph = mark_safe(' <span title="confirmed">&#x2714;</span>')
        else:
            glyph = ''
        # hx-boost="false": the table element carries hx-boost/hx-target attrs for
        # sorting and pagination; without the opt-out, clicking this link would swap
        # the detail page INTO the table container instead of navigating to it
        return format_html('<a href="{}" hx-boost="false">{}</a>{}', url, value, glyph)

    def render_inverse_far(self, record: NonLocalizedEvent) -> str:
        sequence = latest_sequence(record)
        return format_inverse_far((sequence.details or {}).get('far') if sequence else None)

    def render_classification(self, record: NonLocalizedEvent) -> str:
        sequence = latest_sequence(record)
        return most_likely_classification(sequence.details if sequence else None)

    def render_distance(self, record: NonLocalizedEvent) -> str:
        sequence = latest_sequence(record)
        return format_distance(sequence.localization if sequence else None)

    def render_has_ns(self, record: NonLocalizedEvent) -> str:
        sequence = latest_sequence(record)
        return _percent(((sequence.details or {}).get('properties') or {}).get('HasNS') if sequence else None)

    def render_has_remnant(self, record: NonLocalizedEvent) -> str:
        sequence = latest_sequence(record)
        return _percent(((sequence.details or {}).get('properties') or {}).get('HasRemnant') if sequence else None)

    def render_external_links(self, record: NonLocalizedEvent) -> str:
        """Favicon links to external services; GraceDB/Treasure Map only apply to GW events."""
        sequence = latest_sequence(record)
        # link the LATEST sequence's Hermes message page when its UUID is known;
        # fall back to the event-level Hermes URL (per-sequence links, one per
        # alert, live on the detail page)
        hermes_url = (sequence.hermes_url if sequence else None) or record.hermes_url
        links = [format_html(
            '<a href="{}" target="_blank" rel="noopener" title="Hermes (latest message)">'
            '<img src="https://hermes.lco.global/favicon.ico" alt="Hermes" height="16"></a>',
            hermes_url)]
        if record.event_type == NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE:
            links.append(format_html(
                '<a href="{}" target="_blank" rel="noopener" title="GraceDB">'
                '<img src="https://gracedb.ligo.org/static/images/favicon.png" alt="GraceDB" height="16"></a>',
                record.gracedb_url))
            links.append(format_html(
                '<a href="{}" target="_blank" rel="noopener" title="Gravitational Wave Treasure Map">'
                '<img src="https://treasuremap.space/gwtm_logo.png" alt="Treasure Map" height="16"></a>',
                record.treasuremap_url))
        return mark_safe(' '.join(links))


class GravitationalWaveEventTable(NonLocalizedEventTable):
    """The GW tab's table: the full science column set; the event-type column is
    redundant on a single-type tab."""

    class Meta(NonLocalizedEventTable.Meta):
        exclude = ('selection', 'event_type')
        sequence = ('event_id', 'inverse_far', 'classification', 'distance',
                    'has_ns', 'has_remnant', 'external_links')


class EventTypeStubTable(NonLocalizedEventTable):
    """Base for tabs awaiting type-specific columns: generic columns only.

    The GW-specific science columns are excluded; each concrete subclass grows
    its own columns as the type's alert content gets modeled.
    """

    class Meta(NonLocalizedEventTable.Meta):
        exclude = ('selection', 'event_type', 'inverse_far', 'classification',
                   'distance', 'has_ns', 'has_remnant')
        sequence = ('event_id', 'external_links')


class GammaRayBurstEventTable(EventTypeStubTable):
    """Stub: gamma-ray-burst-specific columns land here."""


class NeutrinoEventTable(EventTypeStubTable):
    """Stub: neutrino-specific columns land here."""


class XRayTransientEventTable(EventTypeStubTable):
    """Stub: X-ray-transient-specific columns land here."""


class UnknownEventTable(EventTypeStubTable):
    """Stub: columns for events of unknown type land here."""

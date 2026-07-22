"""FilterSets for the event list page tabs, built on TOM Toolkit's HTMXTableFilterSet.

The science filters are ports of SAGUARO's NonLocalizedEventFilter; they
compare against each event's LATEST sequence via a Subquery annotation, so
the comparisons run in SQL. Each event-type tab has its own FilterSet: the
gravitational-wave set carries the full science filters, the other types are
stubs awaiting type-specific filters. There is no event-type filter -- the
tabs themselves select the type.
"""
import sys

from django import forms
from django.db.models import OuterRef, QuerySet, Subquery

import django_filters
from crispy_forms.bootstrap import PrependedAppendedText
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, HTML, Layout, Row

from tom_common.htmx_table import HTMXTableFilterSet, htmx_attributes_delayed, htmx_attributes_instant

from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent
from tom_nonlocalizedevents.tables import HZ_TO_INVERSE_YEARS


class BaseEventFilterSet(HTMXTableFilterSet):
    """Filters common to every event-type tab, laid out flat (no 'Advanced' collapse)."""

    prefix = django_filters.ChoiceFilter(
        choices=(('S', 'Real'), ('MS', 'Test')), label='Alert Type', empty_label='All',
        field_name='event_id', lookup_expr='startswith',
        widget=forms.Select(attrs=htmx_attributes_instant))
    state = django_filters.ChoiceFilter(
        choices=(('ACTIVE', 'Active'), ('RETRACTED', 'Retracted')), label='State', empty_label='All',
        widget=forms.Select(attrs=htmx_attributes_instant))

    def general_search(self, queryset: QuerySet, name: str, value) -> QuerySet:
        """General search matches on event_id only.

        Overrides the parent's default, which ORs an icontains across every
        model field -- common substrings then match all rows, which reads as
        the search doing nothing.
        """
        if not value:
            return queryset
        return queryset.filter(event_id__icontains=value)

    @staticmethod
    def last_sequence_filter(queryset: QuerySet, name: str, value) -> QuerySet:
        """Filter on a field of each event's LATEST EventSequence (SAGUARO's idiom).

        The filter's field_name encodes the lookup path within the sequence;
        the latest sequence's value is annotated per-event via Subquery so the
        comparison runs in SQL. Unit conversions: 1/FAR arrives in years
        (converted to the stored Hz), HasNS/HasRemnant arrive as percentages
        (converted to the stored 0-1 decimals).
        """
        name_parts = name.split('__')
        field_name = '__'.join(name_parts[:-1])  # the path without the trailing lookup (__lte/__gte)
        if name_parts[-2] == 'far':
            value = HZ_TO_INVERSE_YEARS / float(value)  # yr -> Hz
        elif name_parts[-2].startswith('Has'):
            value = 0.01 * float(value)  # percent -> decimal
        else:
            value = float(value)
        last_value = EventSequence.objects.filter(
            nonlocalizedevent_id=OuterRef('id')).order_by('-sequence_id').values(field_name)[:1]
        return queryset.annotate(**{field_name: Subquery(last_value)}).filter(**{name: value})

    @staticmethod
    def _clear_filters_column() -> Column:
        """The Clear Filters button: plain navigation to the bare path drops every
        query parameter; the phantom label top-aligns it with the labeled inputs."""
        return Column(
            HTML('<label class="form-label d-block">&nbsp;</label>'
                 '<a href="{{ request.path }}" class="btn btn-outline-secondary">Clear Filters</a>'),
            css_class='col-auto',
        )

    def _layout_rows(self) -> list[Row]:
        """The crispy layout rows; per-type FilterSets override to add their filters."""
        return [
            Row(
                Column('query', css_class='col-12 col-md-4'),
                Column('prefix', css_class='col-12 col-md-3'),
                Column('state', css_class='col-12 col-md-3'),
                self._clear_filters_column(),
            ),
        ]

    @property
    def form(self) -> forms.Form:
        """Flat crispy layout with no submit button (filtering is htmx-driven).

        The helper is REPLACED unconditionally: the django-filters DRF
        FilterSet underneath HTMXTableFilterSet pre-attaches its own helper on
        every form access (all fields stacked, bootstrap3, a Submit button),
        so a replace-only-if-absent guard would keep that form forever.
        """
        form = super(HTMXTableFilterSet, self).form  # the plain django-filters form, skipping the parent's layout
        form.helper = FormHelper()
        form.helper.form_tag = False       # the template provides <form id="filter-form">
        form.helper.disable_csrf = True
        form.helper.layout = Layout(*self._layout_rows())
        return form

    class Meta:
        model = NonLocalizedEvent
        fields = []  # every filter is declared; the flat layouts list them explicitly


class NonLocalizedEventFilterSet(BaseEventFilterSet):
    """The All tab's filters: the common set plus the GW science filters."""

    inv_far_min = django_filters.NumberFilter(
        'details__far__lte', method='last_sequence_filter', label='1/FAR',
        min_value=sys.float_info.epsilon,
        help_text='Significant CBC alerts have 1/FAR > 0.5 yr',
        widget=forms.NumberInput(attrs=htmx_attributes_delayed))
    distance_max = django_filters.NumberFilter(
        'localization__distance_mean__lte', method='last_sequence_filter', label='Distance',
        min_value=0., widget=forms.NumberInput(attrs=htmx_attributes_delayed))
    has_ns_min = django_filters.NumberFilter(
        'details__properties__HasNS__gte', method='last_sequence_filter', label='HasNS',
        min_value=0., max_value=100., widget=forms.NumberInput(attrs=htmx_attributes_delayed))
    has_remnant_min = django_filters.NumberFilter(
        'details__properties__HasRemnant__gte', method='last_sequence_filter', label='HasRemnant',
        min_value=0., max_value=100., widget=forms.NumberInput(attrs=htmx_attributes_delayed))

    def _layout_rows(self) -> list[Row]:
        return [
            Row(
                Column('query', css_class='col-12 col-md-4'),
                Column('prefix', css_class='col-12 col-md-4'),
                Column('state', css_class='col-12 col-md-4'),
            ),
            Row(
                Column(PrependedAppendedText('inv_far_min', '>', 'yr'), css_class='col-12 col-md'),
                Column(PrependedAppendedText('distance_max', '<', 'Mpc'), css_class='col-12 col-md'),
                Column(PrependedAppendedText('has_ns_min', '>', '%'), css_class='col-12 col-md'),
                Column(PrependedAppendedText('has_remnant_min', '>', '%'), css_class='col-12 col-md'),
                self._clear_filters_column(),
            ),
        ]


class GravitationalWaveEventFilterSet(NonLocalizedEventFilterSet):
    """The GW tab's filters: currently identical to the All tab's science set."""


class GammaRayBurstEventFilterSet(BaseEventFilterSet):
    """Stub: gamma-ray-burst-specific filters land here."""


class NeutrinoEventFilterSet(BaseEventFilterSet):
    """Stub: neutrino-specific filters land here."""


class XRayTransientEventFilterSet(BaseEventFilterSet):
    """Stub: X-ray-transient-specific filters land here."""


class UnknownEventFilterSet(BaseEventFilterSet):
    """Stub: filters for events of unknown type land here."""

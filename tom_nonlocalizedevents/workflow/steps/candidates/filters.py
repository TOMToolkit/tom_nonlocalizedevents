"""The filters of the candidates table: Active / Retired / All, and a name search."""
from __future__ import annotations

from django import forms
from django.db.models import QuerySet

import django_filters
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row

from tom_common.htmx_table import HTMXTableFilterSet, htmx_attributes_instant

from tom_nonlocalizedevents.models import EventCandidate

VIABILITY_CHOICES = (('true', 'Active'), ('false', 'Retired'), ('all', 'All'))


class CandidateFilterSet(HTMXTableFilterSet):
    """Filters of the candidates table; ``viable`` defaults to Active (the view supplies the default)."""

    viable = django_filters.ChoiceFilter(choices=VIABILITY_CHOICES, label='Show', empty_label=None,
                                         method='filter_viable',
                                         widget=forms.Select(attrs=htmx_attributes_instant))

    def filter_viable(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        if value == 'all':
            return queryset
        return queryset.filter(viable=(value == 'true'))

    def general_search(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        """The general search matches the target's name and aliases."""
        if not value:
            return queryset
        return queryset.filter(target__name__icontains=value) | queryset.filter(target__aliases__name__icontains=value)

    @property
    def form(self) -> forms.Form:
        # the helper is replaced unconditionally, as in filters.py (the parent pre-attaches its own)
        form = super(HTMXTableFilterSet, self).form
        form.helper = FormHelper()
        form.helper.form_tag = False
        form.helper.disable_csrf = True
        form.helper.layout = Layout(Row(Column('viable', css_class='col-6 col-md-2'),
                                        Column('query', css_class='col-6 col-md-4')))
        return form

    class Meta:
        model = EventCandidate
        fields = []

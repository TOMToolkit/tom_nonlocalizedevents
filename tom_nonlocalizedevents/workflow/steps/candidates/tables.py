"""The candidates table of the Candidates workflow step."""
from __future__ import annotations

from django.db.models import F, QuerySet
from django.urls import reverse
from django.utils.html import format_html

import django_tables2 as tables

from tom_common.htmx_table import HTMXTable

from tom_nonlocalizedevents.models import EventCandidate


class CandidateTable(HTMXTable):
    """One row per EventCandidate of the event; the credible-region column is for the selected alert.

    ``smallest_percent`` is an annotation of the view's queryset (the CredibleRegion row for the selected
    alert's localization), so it sorts in SQL; it is blank when the alert has no localization or the
    candidate lies outside every recorded level.
    """
    target = tables.Column(verbose_name='Target', accessor='target__name', order_by='target__name')
    ra = tables.Column(verbose_name='RA', accessor='target__ra', order_by='target__ra')
    dec = tables.Column(verbose_name='Dec', accessor='target__dec', order_by='target__dec')
    smallest_percent = tables.Column(verbose_name='CR %', empty_values=(None,),
                                     attrs={'th': {'title': 'smallest credible region of the selected alert '
                                                            'containing the target'}})
    priority = tables.Column(verbose_name='Priority')
    viability_reason = tables.Column(verbose_name='Reason', orderable=False)
    modified = tables.DateTimeColumn(verbose_name='Modified', format='Y-m-d H:i')

    partial_template_name = 'tom_nonlocalizedevents/partials/candidate_table_partial.html'

    class Meta(HTMXTable.Meta):
        model = EventCandidate
        fields = ('target', 'ra', 'dec', 'smallest_percent', 'priority', 'viability_reason', 'modified')
        sequence = fields
        exclude = ('selection',)
        row_attrs = {'class': lambda record: '' if record.viable else 'text-muted'}

    def render_target(self, record: EventCandidate, value: str) -> str:
        # hx-boost="false": the table carries hx-boost for sorting; this link navigates
        return format_html('<a href="{}" hx-boost="false">{}</a>{}',
                           reverse('targets:detail', args=(record.target.pk,)), value,
                           '' if record.viable else ' (retired)')

    def render_ra(self, value: float) -> str:
        return f'{value:.5f}'

    def render_dec(self, value: float) -> str:
        return f'{value:+.5f}'

    def render_smallest_percent(self, value: int) -> str:
        return f'{value}%'

    def order_smallest_percent(self, queryset: QuerySet, is_descending: bool) -> tuple[QuerySet, bool]:
        # candidates outside every level (NULL) sort last either way; Postgres would put them first descending
        ordering = F('smallest_percent').desc(nulls_last=True) if is_descending else F('smallest_percent').asc(
            nulls_last=True)
        return queryset.order_by(ordering, 'target__name'), True

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
    actions = tables.Column(verbose_name='', orderable=False, empty_values=())

    partial_template_name = 'tom_nonlocalizedevents/partials/candidate_table_partial.html'

    class Meta(HTMXTable.Meta):
        """Recall that render_<field> methods determine how fields are rendered in the table.
        """
        model = EventCandidate
        fields = ('target', 'ra', 'dec', 'smallest_percent', 'priority', 'viability_reason', 'modified', 'actions')
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

    def render_actions(self, record: EventCandidate) -> str:
        """Retire or activate, and delete, as htmx requests.
        """
        event_id = record.nonlocalizedevent.event_id
        viability_url = reverse('nonlocalizedevents:candidate-viability', args=(event_id, record.pk))
        delete_url = reverse('nonlocalizedevents:candidate-delete', args=(event_id, record.pk))
        if record.viable:
            viability_button = format_html(
                '<button type="button" class="btn btn-outline-secondary btn-sm" hx-post="{}" '
                'hx-vals=\'{{"viable": "false"}}\' hx-prompt="Reason for retiring {}" hx-swap="none">Retire</button>',
                viability_url, record.target.name)
        else:
            viability_button = format_html(
                '<button type="button" class="btn btn-outline-primary btn-sm" hx-post="{}" '
                'hx-vals=\'{{"viable": "true"}}\' hx-swap="none">Activate</button>', viability_url)
        delete_button = format_html(
            '<button type="button" class="btn btn-outline-danger btn-sm ms-1" hx-delete="{}" '
            'hx-confirm="Remove {} from the candidates of {}? The target is kept." hx-swap="none">Delete</button>',
            delete_url, record.target.name, event_id)
        return format_html('{}{}', viability_button, delete_button)

    def order_smallest_percent(self, queryset: QuerySet, is_descending: bool) -> tuple[QuerySet, bool]:
        # candidates outside every level (NULL) sort last either way; Postgres would put them first descending
        ordering = F('smallest_percent').desc(nulls_last=True) if is_descending else F('smallest_percent').asc(
            nulls_last=True)
        return queryset.order_by(ordering, 'target__name'), True

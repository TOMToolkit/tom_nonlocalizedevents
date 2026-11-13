"""The Candidates workflow step page, and the text of its cell on the event list."""
from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import IntegerField, OuterRef, QuerySet, Subquery, Value
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views import View

from django_filters.views import FilterView
from django_htmx.http import trigger_client_event

from tom_common.htmx_table import HTMXTableViewMixin
from tom_targets.models import Target

from tom_nonlocalizedevents.event_summary.views import EventSummaryMixin
from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate, NonLocalizedEvent
from tom_nonlocalizedevents.services.candidates import add_candidate, find_target, remove_candidate
from tom_nonlocalizedevents.workflow.steps.candidates.filters import CandidateFilterSet
from tom_nonlocalizedevents.workflow.steps.candidates.tables import CandidateTable


class CandidatesWorkflowStepView(LoginRequiredMixin, EventSummaryMixin, HTMXTableViewMixin, FilterView):
    """The event's candidates as an htmx table under the event summary.

    In the tuple of supers, ``EventSummaryMixin`` comes before ``HTMXTableViewMixin`` so an
    alert-tab click gets the summary update (header, sky-map data, this table out of band)
    while sorting, paging and filtering get the table partial.
    """
    template_name = 'tom_nonlocalizedevents/workflow_steps/candidates.html'
    model = EventCandidate
    table_class = CandidateTable
    filterset_class = CandidateFilterSet
    paginate_by = 25
    strict = False
    ordering = ['target__name']

    def get_page_template_names(self) -> list[str]:
        # HTMXTableViewMixin would answer any htmx request with the table partial; the mixin asks for the
        # page explicitly on a history restore
        return [self.template_name]

    def get_queryset(self) -> QuerySet:
        queryset = (super().get_queryset().filter(nonlocalizedevent=self.event)
                    .select_related('target', 'nonlocalizedevent'))

        # Annotate the queryset with the credible-region percentage
        localization = self.selected_sequence.localization if self.selected_sequence else None
        if localization is None:
            return queryset.annotate(smallest_percent=Value(None, output_field=IntegerField()))
        percent = CredibleRegion.objects.filter(candidate=OuterRef('pk'),
                                                localization=localization).values('smallest_percent')[:1]
        return queryset.annotate(smallest_percent=Subquery(percent, output_field=IntegerField()))

    def get_filterset_kwargs(self, filterset_class: type) -> dict[str, Any]:
        # the page opens on every candidate, active and retired, so a reader sees the whole record of the event;
        # the default is written into the data so the Show dropdown renders with All selected (an unbound
        # ChoiceFilter would filter nothing but show its first choice)
        kwargs = super().get_filterset_kwargs(filterset_class)
        data = kwargs['data'].copy() if kwargs['data'] is not None else {}
        data.setdefault('viable', 'all')
        kwargs['data'] = data
        return kwargs


class CandidateViabilityView(LoginRequiredMixin, View):
    """Retire a candidate or activate it again. This is an htmx POST from the candidates table.

    The response has HTTP status code 204 (No Content) and no body, so htmx swaps nothing into the page.
    However, the response carries the response header ``HX-Trigger: candidatesChanged``, and that header
    starts a chain of three steps, each caused by the one before it:
      1. htmx dispatches a ``candidatesChanged`` event.
      2. The table container on the page listens for that event (its ``hx-trigger`` attribute) and reloads
         itself from the table partial.
      3. The reloaded table partial carries the sky map's data element out of band, and the sky map's
         JavaScript notices the replaced element and redraws the markers.

    The reason comes from either the ``viability_reason`` form field or from the ``HX-Prompt``
    request header for the table's prompt button.
    """

    def post(self, request: HttpRequest, event_id: str, pk: int) -> HttpResponse:
        candidate = get_object_or_404(EventCandidate, pk=pk, nonlocalizedevent__event_id=event_id)
        viable = request.POST.get('viable') == 'true'
        reason = (request.POST.get('viability_reason') or request.headers.get('HX-Prompt') or '').strip()
        if not viable and not reason:
            return HttpResponseBadRequest('A reason is required to retire a candidate.')
        candidate.viable = viable
        candidate.viability_reason = '' if viable else reason
        candidate.save()
        return trigger_client_event(HttpResponse(status=204), 'candidatesChanged')


class CandidateAddView(LoginRequiredMixin, View):
    """Make a target a candidate of the event, from the form on the Candidates workflow step.

    The form posts the target's name, alias or primary key with htmx. The answer (what was added, that the target
    already was a candidate, that no target matched, or that several did) is a Django message, reported where every
    other form in a TOM reports: the messages area at the top of the page. The response body is tom_base's messages
    partial, tom_common/partials/messages.html, whose div is marked ``hx-swap-oob="true"``, so htmx swaps it into
    that area whatever the form's own target is (the form has ``hx-swap="none"``). When a candidate was created the
    response also carries the header ``HX-Trigger: candidatesChanged``, on which the table container reloads
    itself (see CandidateViabilityView).
    """
    messages_template_name = 'tom_common/partials/messages.html'

    def post(self, request: HttpRequest, event_id: str) -> HttpResponse:
        event = get_object_or_404(NonLocalizedEvent, event_id=event_id)
        name_or_pk = (request.POST.get('target') or '').strip()
        created = False
        if not name_or_pk:
            messages.error(request, "A target's name, alias or primary key is required.")
        else:
            try:
                target = find_target(name_or_pk)
            except (Target.DoesNotExist, Target.MultipleObjectsReturned) as error:
                messages.error(request, str(error))
            else:
                candidate, created = add_candidate(event, target)
                if created:
                    messages.success(request, f'{target.name} added as a candidate of {event.event_id}.')
                else:
                    messages.info(request, f'{target.name} already is a candidate of {event.event_id}.')
        response = render(request, self.messages_template_name)
        return trigger_client_event(response, 'candidatesChanged') if created else response


class CandidateDeleteView(LoginRequiredMixin, View):
    """Remove a candidate from the event, from the Delete button of the candidates table (an htmx DELETE).

    The candidate row and its credible regions go; the target stays. The answer is a Django message in tom_base's
    messages partial, as for CandidateAddView, and the response carries ``HX-Trigger: candidatesChanged`` so the
    table reloads and the sky map redraws (see CandidateViabilityView).
    """
    messages_template_name = 'tom_common/partials/messages.html'

    def delete(self, request: HttpRequest, event_id: str, pk: int) -> HttpResponse:
        candidate = get_object_or_404(EventCandidate, pk=pk, nonlocalizedevent__event_id=event_id)
        target_name = candidate.target.name
        remove_candidate(candidate)
        messages.success(request, f'{target_name} is no longer a candidate of {event_id}; the target is kept.')
        return trigger_client_event(render(request, self.messages_template_name), 'candidatesChanged')


def cell_text(event: NonLocalizedEvent) -> str:
    """The event list cell: "N active / M retired", from the counts the list view annotates when it can."""
    active = getattr(event, 'active_candidate_count', None)
    retired = getattr(event, 'retired_candidate_count', None)
    if active is None or retired is None:
        active = event.candidates.filter(viable=True).count()
        retired = event.candidates.filter(viable=False).count()
    return f'{active} active / {retired} retired'

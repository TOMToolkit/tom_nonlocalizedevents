import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView
from django.views.generic.edit import FormView

from django_filters.rest_framework import DjangoFilterBackend
from django_filters.views import FilterView
from rest_framework import permissions, viewsets

from tom_common.htmx_table import HTMXTableViewMixin

from tom_nonlocalizedevents.filters import (GammaRayBurstEventFilterSet, GravitationalWaveEventFilterSet,
                                            NeutrinoEventFilterSet, NonLocalizedEventFilterSet,
                                            UnknownEventFilterSet, XRayTransientEventFilterSet)
from tom_nonlocalizedevents.forms import GraceDBEventIngestionForm
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, NonLocalizedEvent
from tom_nonlocalizedevents.serializers import (EventCandidateSerializer, EventLocalizationSerializer,
                                                NonLocalizedEventSerializer)
from tom_nonlocalizedevents.services.gracedb import ingest_event_from_gracedb
from tom_nonlocalizedevents.tables import (GammaRayBurstEventTable, GravitationalWaveEventTable,
                                           NeutrinoEventTable, NonLocalizedEventTable,
                                           UnknownEventTable, XRayTransientEventTable)


logger = logging.getLogger(__name__)

# The event-type tabs of the list page, in display order: URL slug -> (event
# type, Table, FilterSet). 'all' (event type None) shows every event; the GW
# tab carries the full science table/filters; the others are stubs awaiting
# type-specific columns and filters. The index lands on the GW tab.
EVENT_TYPE_TABS = {
    'all': (None, NonLocalizedEventTable, NonLocalizedEventFilterSet),
    'gw': (NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE,
           GravitationalWaveEventTable, GravitationalWaveEventFilterSet),
    'grb': (NonLocalizedEvent.NonLocalizedEventType.GAMMA_RAY_BURST,
            GammaRayBurstEventTable, GammaRayBurstEventFilterSet),
    'neutrino': (NonLocalizedEvent.NonLocalizedEventType.NEUTRINO,
                 NeutrinoEventTable, NeutrinoEventFilterSet),
    'xray': (NonLocalizedEvent.NonLocalizedEventType.X_RAY_TRANSIENT,
             XRayTransientEventTable, XRayTransientEventFilterSet),
    'unknown': (NonLocalizedEvent.NonLocalizedEventType.UNKNOWN,
                UnknownEventTable, UnknownEventFilterSet),
}

# reverse map for per-event-type detail template resolution ('all' has no type)
SLUG_BY_EVENT_TYPE = {entry[0].value: slug for slug, entry in EVENT_TYPE_TABS.items()
                      if entry[0] is not None}


class NonLocalizedEventListView(LoginRequiredMixin, HTMXTableViewMixin, FilterView):
    """Filterable, sortable event list on the TOM Toolkit htmx-table machinery.

    Columns and filters follow SAGUARO's event list pages (see tables.py /
    filters.py). The index lands on the Gravitational Wave tab; the other
    event-type tabs (and All) lazy-load via EventTypeTabView. The rendering
    contract for TOMs that override index.html is unchanged in shape:
    template name tom_nonlocalizedevents/index.html with object_list in the
    context -- though object_list is now the GW page, not all events.
    """
    template_name = 'tom_nonlocalizedevents/index.html'
    model = NonLocalizedEvent
    paginate_by = 20
    strict = False  # an empty or partial GET applies no filters, rather than matching nothing
    ordering = ['-created']

    # the index's default tab configuration (GW); EventTypeTabView overrides
    # these per URL slug from EVENT_TYPE_TABS
    tab_slug = 'gw'
    event_type = NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE
    table_class = GravitationalWaveEventTable
    filterset_class = GravitationalWaveEventFilterSet

    def get_queryset(self) -> QuerySet:
        # prefetch what the table's latest-sequence columns render, so the page
        # issues a constant number of queries instead of one per row
        queryset = super().get_queryset().prefetch_related('sequences__localization')
        if self.event_type is not None:  # None: the All tab
            queryset = queryset.filter(event_type=self.event_type)
        return queryset

    def get_template_names(self) -> list[str]:
        # a tab click (HX-Target: event-tabs-region) receives the whole region
        # (nav + filter form + table); other htmx requests (sorting, filtering,
        # pagination) receive just the table partial from the mixin
        if self.request.htmx and self.request.htmx.target == 'event-tabs-region':
            return ['tom_nonlocalizedevents/partials/event_tabs_region.html']
        return super().get_template_names()

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context['tabs'] = self.tab_navigation()
        context['active_tab'] = self.tab_slug
        return context

    @staticmethod
    def tab_navigation() -> list[dict]:
        """The tab strip, in EVENT_TYPE_TABS order: All left-most, then the types."""
        return [{'slug': slug,
                 'label': entry[0].label if entry[0] is not None else 'All',
                 'url': reverse('nonlocalizedevents:tab', args=(slug,))}
                for slug, entry in EVENT_TYPE_TABS.items()]


class EventTypeTabView(NonLocalizedEventListView):
    """One tab of the list page, configured per URL slug from EVENT_TYPE_TABS.

    The same machinery as the index, over the tab's queryset with the tab's
    own Table and FilterSet. Non-htmx GETs render the full page with this tab
    active (the tabs are bookmarkable); htmx tab clicks receive just the tabs
    region.
    """

    def dispatch(self, request, *args, **kwargs):
        self.tab_slug = kwargs['event_type_slug']
        if self.tab_slug not in EVENT_TYPE_TABS:
            raise Http404(f'Unknown event-type tab: {self.tab_slug}')
        # instance attributes shadow the class-level (GW) configuration; the
        # inherited get_queryset/get_table_class/get_filterset_class read them
        self.event_type, self.table_class, self.filterset_class = EVENT_TYPE_TABS[self.tab_slug]
        return super().dispatch(request, *args, **kwargs)


#
# Django Rest Framework Views
#

class NonLocalizedEventViewSet(viewsets.ModelViewSet):
    """
    DRF API endpoint that allows NonLocalizedEvents to be viewed or edited.
    """
    queryset = NonLocalizedEvent.objects.all()
    serializer_class = NonLocalizedEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['event_id', 'event_type']


class EventCandidateViewSet(viewsets.ModelViewSet):
    """
    DRF API endpoint for EventCandidate model.

    Implementation has changes for bulk_create and update/PATCH EventCandidate instances.
    """
    queryset = EventCandidate.objects.all()
    serializer_class = EventCandidateSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['nonlocalizedevent', 'viable', 'priority']

    def get_serializer(self, *args, **kwargs):
        # In order to ensure the list_serializer_class is used for bulk_create, we check that the POST data is a list
        # and add `many = True` to the kwargs
        if isinstance(kwargs.get('data', {}), list):
            kwargs['many'] = True

        return super().get_serializer(*args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Provide support for the PATCH HTTP verb to update individual model fields.

        An example request might look like:

            PATCH http://localhost:8000/api/eventcandidates/18/

        with a Request Body of:

            {
                "viability": false
            }

        """
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)


class EventLocalizationViewSet(viewsets.ModelViewSet):
    """
    DRF API endpoint that allows EventLocalizations to be viewed or edited.
    """
    queryset = EventLocalization.objects.all()
    serializer_class = EventLocalizationSerializer
    permission_classes = [permissions.IsAuthenticated]


class NonLocalizedEventDetailView(LoginRequiredMixin, DetailView):
    """Server-rendered detail page for a single NonLocalizedEvent.

    Renders entirely from local data -- no external API calls at request time.
    Resolves its object by either primary key or ``event_id``, consolidating
    the Pk- and Id-based lookups. This view will take over the ``detail`` and
    ``event-detail`` URL names when the Vue frontend is removed; until then it
    is exercised only by its tests.
    """
    model = NonLocalizedEvent
    context_object_name = 'nonlocalizedevent'

    def get_template_names(self) -> list[str]:
        # per-event-type template first (stubs today, extending the generic
        # page), with the generic template as fallback
        type_slug = SLUG_BY_EVENT_TYPE.get(self.object.event_type, 'unknown')
        return [f'tom_nonlocalizedevents/detail/{type_slug}_detail.html',
                'tom_nonlocalizedevents/nonlocalizedevent_detail.html']

    def get_object(self, queryset: QuerySet | None = None) -> NonLocalizedEvent:
        """Retrieve the NonLocalizedEvent by pk or event_id, raising Http404 on a miss."""
        if queryset is None:
            queryset = self.get_queryset()
        if 'pk' in self.kwargs:
            return get_object_or_404(queryset, pk=self.kwargs['pk'])
        if 'event_id' in self.kwargs:
            return get_object_or_404(queryset, event_id=self.kwargs['event_id'])
        raise Http404('No pk or event_id provided')

    def get_context_data(self, **kwargs: dict) -> dict:
        context = super().get_context_data(**kwargs)
        # one query for the sequence table; each row also shows its localization's numbers
        context['sequences'] = self.object.sequences.select_related('localization')
        return context


class IngestFromGraceDBView(LoginRequiredMixin, FormView):
    """Present a form for manually ingesting an event from GraceDB by event ID.

    The work is delegated to services.gracedb.ingest_event_from_gracedb, which
    catches its own failures and reports them back as a list of error strings,
    so no exception handling is needed here -- outcomes are relayed to the
    user through the messages framework.
    """
    template_name = 'tom_nonlocalizedevents/ingest_gracedb_form.html'
    form_class = GraceDBEventIngestionForm
    success_url = reverse_lazy('nonlocalizedevents:index')

    def form_valid(self, form: GraceDBEventIngestionForm) -> HttpResponse:
        event_id = form.cleaned_data['event_id']
        logger.info(f"User {self.request.user} initiated GraceDB ingestion for event: {event_id}")

        success_count, errors = ingest_event_from_gracedb(event_id)

        if success_count > 0:
            messages.success(self.request, f"Successfully ingested {success_count} new sequence(s) for {event_id}.")
        if not errors and success_count == 0:
            messages.warning(self.request, f"No new sequences were ingested for {event_id}. "
                                           f"It may already be up to date.")
        for error in errors:
            messages.error(self.request, f"Error for {event_id}: {error}")

        return super().form_valid(form)


# The SupereventPkView and SupereventIdView are retained for
# backwards compatibity (SAGUARO uses the -IdView)
SupereventPkView = NonLocalizedEventDetailView
SupereventIdView = NonLocalizedEventDetailView

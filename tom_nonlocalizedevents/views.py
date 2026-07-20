import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import FormView

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, viewsets

from tom_nonlocalizedevents.forms import GraceDBEventIngestionForm
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, NonLocalizedEvent
from tom_nonlocalizedevents.serializers import (EventCandidateSerializer, EventLocalizationSerializer,
                                                NonLocalizedEventSerializer)
from tom_nonlocalizedevents.services.gracedb import ingest_event_from_gracedb


logger = logging.getLogger(__name__)


class NonLocalizedEventListView(LoginRequiredMixin, ListView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    model = NonLocalizedEvent
    template_name = 'tom_nonlocalizedevents/index.html'

    def get_queryset(self):
        # '-created' is most recent first
        qs = NonLocalizedEvent.objects.order_by('-created')
        return qs


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
    template_name = 'tom_nonlocalizedevents/nonlocalizedevent_detail.html'
    context_object_name = 'nonlocalizedevent'

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

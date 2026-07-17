import json
import logging
import requests

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView
from django.views.generic.base import View, TemplateView
from django.views.generic.edit import FormView, UpdateView
from django.urls import reverse, reverse_lazy


from rest_framework import permissions, viewsets
from django_filters.rest_framework import DjangoFilterBackend

from tom_nonlocalizedevents.forms import GraceDBEventIngestionForm, NonLocalizedEventsProfileForm
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, NonLocalizedEvent, NonLocalizedEventsProfile
from tom_nonlocalizedevents.services.base import ingest_igwn_event_from_data
from tom_nonlocalizedevents.serializers import (EventCandidateSerializer, EventLocalizationSerializer,
                                                NonLocalizedEventSerializer)


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


# from the tom_alerts query_result.html
class CreateEventFromHermesAlertView(View):
    """
    Creates the models.NonLocalizedEvent instance and redirect to NonLocalizedEventDetailView
    """

    def post(self, request, *args, **kwargs):
        """
        """
        # the request.POST is a QueryDict object;
        query_id = self.request.POST['query_id']

        # events is a list[str] of NonLocalizedEvent event_id's: (e.g. 'MS230417a')
        # (i.e the selected events from the query result form)
        events = request.POST.getlist('events', [])

        # if the user didn't select an alert; warn and re-direct back
        if not events:
            messages.warning(request, 'Please select at least one Event to create.')
            reverse_url: str = reverse('tom_alerts:run', kwargs={'pk': query_id})
            return redirect(reverse_url)

        # Create NonLocalizedEvents for each of the selected events.
        for event_id in events:
            logger.debug(f'Creating event {event_id}...')
            # extract the Hermes event from the cache
            # (it was cached by tom_alerts.views.py::RunQueryView)
            cached_event = json.loads(cache.get(f'alert_{event_id}'))

            # early return: alert not in cache
            if not cached_event:
                messages.error(request, 'Could not createn event(s). Try re-running the query to refresh the cache.')
                return redirect(reverse('tom_alerts:run', kwargs={'pk': query_id}))

            # the NonLocalizedEvent is created by handling all the messages from
            # the event sequence as if they were ingested
            for alert_data in cached_event['sequences']:
                logger.debug(f"Creating sequence from HermesBroker: {alert_data}")
                # The cached data from Hermes does not contain the raw skymap bytes, only the URLs.
                # The service function is designed to handle this, fetching the data via HTTP if needed.
                ingest_igwn_event_from_data(alert_data, ingestor_source='hop')

        return redirect(reverse('nonlocalizedevents:index'))


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
    """
    Displays a single NonLocalizedEvent. This view follows the standard Django
    DetailView pattern and is responsible for fetching the object and any
    related data for the template.
    """
    model = NonLocalizedEvent
    template_name = 'tom_nonlocalizedevents/nonlocalizedevent_detail.html'
    context_object_name = 'nonlocalizedevent'  # Provides a clear name for the object in the template

    def get_object(self, queryset=None):
        """
        This override allows the view to retrieve the NonLocalizedEvent object
        using either the primary key (`pk`) or the `event_id` (as a slug),
        making the view more flexible and consolidating the logic from the
        previous Pk- and Id-based views.
        """
        if 'pk' in self.kwargs:
            return self.get_queryset().get(pk=self.kwargs['pk'])
        elif 'event_id' in self.kwargs:
            return self.get_queryset().get(event_id=self.kwargs['event_id'])
        raise Http404("No pk or event_id found in URL")

    def get_context_data(self, **kwargs):
        """
        This method is extended to add supplementary data to the template context.
        Specifically, it fetches additional event details (references, sequences)
        from the external Hermes API, decoupling this logic from the primary
        object retrieval.
        """
        context = super().get_context_data(**kwargs)
        nonlocalizedevent = self.object  # The object is already fetched by DetailView

        # Fetch data from Hermes API
        hermes_url = f"{settings.HERMES_API_URL}/api/v0/nonlocalizedevents/{nonlocalizedevent.event_id}/"
        try:
            response = requests.get(hermes_url)
            response.raise_for_status()  # Raise an exception for bad status codes
            hermes_data = response.json()
            context['references'] = hermes_data.get('references', [])
            context['sequences'] = hermes_data.get('sequences', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching data from Hermes API: {e}")
            messages.error(self.request, f"Could not fetch data from Hermes for {nonlocalizedevent.event_id}.")
            context['references'] = []
            context['sequences'] = []

        # The logic for 'expanded_sequences' can be moved to the template or a template tag
        # for better separation of concerns, but is kept here for now to match existing functionality.
        # TODO: Consider refactoring this logic into a template tag.
        context['expanded_sequences'] = self._get_expanded_sequences(context.get('sequences', []))

        return context

    def _get_expanded_sequences(self, sequences):
        expanded_sequences = []
        for seq in sequences:
            expanded_sequences.append(seq)
            if seq.get('external_coincidences'):
                for ext_coinc in seq['external_coincidences']:
                    if ext_coinc.get('retractions'):
                        continue
                    combined_seq = seq.copy()
                    combined_seq['localization_name'] = ext_coinc['localization_name']
                    combined_seq['is_combined'] = True
                    expanded_sequences.append(combined_seq)
        return expanded_sequences


class ProfileUpdateView(UpdateView):
    """
    View that handles updating of a user's ``NonLocalizedEventsProfile``.

    The tom_nonlocalizedevents App has an ``NonLocalizedEventsProfile`` model (see ``models.py``).
    This view updates the properties of that model.

    The ``NonLocalizedEventsProfile`` properties are displayed by the ``nonlocalizedevents_user_profile.html`` template.
    This typically happens on the on the User Profile page via the ``show_app_profiles``
    inclusion tag (see ``tom_base/tom_common/templates/tom_common/user_profile.html`` and
    ``tom_base/tom_common/templatetags/user_extras.py::show_app_profiles``).
    """
    model = NonLocalizedEventsProfile
    template_name = 'tom_nonlocalizedevents/nonlocalizedevents_update_user_profile.html'

    # we need a custom form class to handle the encrypted field
    form_class = NonLocalizedEventsProfileForm

    def get_success_url(self):
        return reverse_lazy('user-profile')


class IngestFromGraceDBView(LoginRequiredMixin, FormView):
    """
    A view that provides a form for users to manually ingest an event
    from GraceDB by providing its event ID. It uses the centralized
    ingestion service to perform the core logic.
    """
    template_name = 'tom_nonlocalizedevents/ingest_gracedb_form.html'
    form_class = GraceDBEventIngestionForm
    success_url = reverse_lazy('nonlocalizedevents:index')

    def form_valid(self, form):
        """
        This method is called when valid form data has been POSTed. It calls
        the ingestion service and uses the Django messages framework to provide
        feedback to the user.
        """
        event_id = form.cleaned_data['event_id'].strip()
        logger.info(f"User {self.request.user} initiated GraceDB ingestion for event: {event_id}")

        try:
            from .services import ingest_event_from_gracedb
            success_count, errors = ingest_event_from_gracedb(event_id)

            if success_count > 0:
                messages.success(self.request, f"Successfully ingested {success_count} new sequence(s) for {event_id}.")
            if not errors and success_count == 0:
                messages.warning(self.request, f"No new sequences were ingested for {event_id}. It may already be up to date.")
            for error in errors:
                messages.error(self.request, f"Error for {event_id}: {error}")
        except Exception as e:
            messages.error(self.request, f"A critical error occurred during ingestion for {event_id}: {e}")
            logger.exception(f"Critical failure during user-initiated ingestion for event {event_id}")

        return super().form_valid(form)

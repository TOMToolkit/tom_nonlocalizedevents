from unittest import mock

from django.contrib.auth.models import AnonymousUser, User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from tom_nonlocalizedevents.tests.factories import (NonLocalizedEventFactory, EventLocalizationFactory,
                                                    EventSequenceFactory)
from tom_nonlocalizedevents.views import NonLocalizedEventDetailView


class NonLocalizedEventAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username='test_user')
        self.superevent1 = NonLocalizedEventFactory.create(event_id='superevent1')
        self.superevent2 = NonLocalizedEventFactory.create(event_id='superevent2')
        self.eventlocalization1 = EventLocalizationFactory.create(nonlocalizedevent=self.superevent1)
        self.eventlocalization2 = EventLocalizationFactory.create(nonlocalizedevent=self.superevent2)
        self.sequence11 = EventSequenceFactory.create(
            nonlocalizedevent=self.superevent1, localization=self.eventlocalization1
        )
        self.sequence21 = EventSequenceFactory.create(
            nonlocalizedevent=self.superevent2, localization=self.eventlocalization2
        )
        self.client.force_login(self.user)


class TestNonLocalizedEventViewSet(NonLocalizedEventAPITestCase):

    def test_nonlocalizedevent_list_api(self):
        """Test NonLocalizedEvent API list endpoint."""
        response = self.client.get(reverse('api:nonlocalizedevent-list'))

        self.assertEqual(response.json()['count'], 2)
        self.assertContains(response, f'"event_id":"{self.superevent1.event_id}"')
        self.assertContains(response, f'"skymap_url":"{self.eventlocalization1.skymap_url}"')
        self.assertContains(response, f'"event_id":"{self.superevent2.event_id}"')
        self.assertContains(response, f'"skymap_url":"{self.eventlocalization2.skymap_url}"')

    def test_nonlocalizedevent_index_view(self):
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertContains(response, self.superevent1.event_id)
        self.assertContains(response, self.superevent2.event_id)
        self.assertContains(response, reverse('nonlocalizedevents:detail', args=(self.superevent1.pk,)))
        self.assertContains(response, reverse('nonlocalizedevents:detail', args=(self.superevent2.pk,)))

    def test_superevent_detail_view(self):
        """Both detail URL names now render the server-side page; no Vue remnants."""
        response = self.client.get(reverse('nonlocalizedevents:detail', args=(self.superevent1.pk,)))

        self.assertContains(response, self.superevent1.event_id)
        self.assertNotContains(response, 'vue')

        response = self.client.get(reverse('nonlocalizedevents:event-detail', args=(self.superevent1.event_id,)))

        self.assertContains(response, self.superevent1.event_id)
        self.assertNotContains(response, 'vue')


class TestEventLocalizationViewSet(NonLocalizedEventAPITestCase):
    def test_eventlocalization_list(self):
        """Test EventLocalization API list endpoint."""
        response = self.client.get(reverse('api:eventlocalization-list'))

        self.assertEqual(response.json()['count'], 2)


class TestNonLocalizedEventDetailView(NonLocalizedEventAPITestCase):
    """Exercise the server-rendered DetailView directly via RequestFactory.

    Routing of the detail/event-detail URL names to this view is covered by
    test_superevent_detail_view above.
    """

    def setUp(self):
        super().setUp()
        # give one sequence a realistic alert payload so content assertions
        # can target values that cannot appear in the page by accident
        self.sequence11.details = {
            'time': '2026-07-17T01:23:45Z',
            'far': 1.2e-9,
            'significant': True,
            'instruments': ['H1', 'L1', 'V1'],
        }
        self.sequence11.save()

    def _render_detail(self, user=None, **url_kwargs):
        request = RequestFactory().get('/nonlocalizedevents/dummy/')
        request.user = user if user is not None else self.user
        response = NonLocalizedEventDetailView.as_view()(request, **url_kwargs)
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_detail_by_pk(self):
        response = self._render_detail(pk=self.superevent1.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.superevent1.event_id)

    def test_detail_by_event_id(self):
        response = self._render_detail(event_id=self.superevent2.event_id)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.superevent2.event_id)

    def test_detail_renders_sequence_and_localization_data(self):
        response = self._render_detail(pk=self.superevent1.pk)

        self.assertContains(response, '1.20e-09')  # details.far
        self.assertContains(response, 'H1, L1, V1')  # details.instruments
        self.assertContains(response, self.eventlocalization1.skymap_url)

    def test_detail_missing_event_raises_404(self):
        with self.assertRaises(Http404):
            self._render_detail(pk=999999)
        with self.assertRaises(Http404):
            self._render_detail(event_id='no-such-event')

    def test_detail_requires_login(self):
        response = self._render_detail(user=AnonymousUser(), pk=self.superevent1.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)


class TestNonLocalizedEventListViewEmpty(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='empty_list_user')
        self.client.force_login(self.user)

    def test_empty_list_renders(self):
        """Regression: the empty-list branch referenced tom_alerts:list, which is a
        NoReverseMatch on any TOM Toolkit 3 TOM (tom_alerts is unmounted there)."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No events have been created')


class TestIngestFromGraceDBView(TestCase):
    """Uses Django's TestCase (not APITestCase): the ingest view is a plain form
    view, and APIClient would POST JSON, which request.POST never sees."""

    def setUp(self):
        self.user = User.objects.create(username='ingest_user')
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('nonlocalizedevents:ingest-gracedb'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_get_renders_form(self):
        response = self.client.get(reverse('nonlocalizedevents:ingest-gracedb'))

        self.assertContains(response, 'GraceDB SuperEvent ID')

    @mock.patch('tom_nonlocalizedevents.views.ingest_event_from_gracedb')
    def test_post_valid_event_id_ingests_and_redirects(self, mock_ingest):
        mock_ingest.return_value = (2, [])

        response = self.client.post(reverse('nonlocalizedevents:ingest-gracedb'), {'event_id': 'S230518h'})

        self.assertRedirects(response, reverse('nonlocalizedevents:index'))
        mock_ingest.assert_called_once_with('S230518h')

    @mock.patch('tom_nonlocalizedevents.views.ingest_event_from_gracedb')
    def test_post_reports_service_errors(self, mock_ingest):
        mock_ingest.return_value = (0, ['GraceDB unreachable'])

        response = self.client.post(reverse('nonlocalizedevents:ingest-gracedb'),
                                    {'event_id': 'S000000a'}, follow=True)

        rendered_messages = [m.message for m in response.context['messages']]
        self.assertTrue(any('GraceDB unreachable' in m for m in rendered_messages))

    def test_post_empty_event_id_rerenders_form(self):
        response = self.client.post(reverse('nonlocalizedevents:ingest-gracedb'), {'event_id': ''})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')

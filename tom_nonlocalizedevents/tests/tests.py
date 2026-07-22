from unittest import mock

from django.contrib.auth.models import AnonymousUser, User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from rest_framework.test import APITestCase

from tom_targets.models import Target

from tom_nonlocalizedevents.healpix_utils import SaEventCandidate, SaSkymap, SaSkymapTile, SaTarget
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.tests.factories import (NonLocalizedEventFactory, EventLocalizationFactory,
                                                    EventSequenceFactory)
from tom_nonlocalizedevents.views import NonLocalizedEventDetailView


class TestSaTableNames(TestCase):
    def test_satarget_tablename_matches_the_distance_owning_model(self):
        """Regression: the hardcoded 'tom_targets_target' name broke silently when
        TOM Toolkit 3 split targets into tom_targets_basetarget."""
        self.assertEqual(SaTarget.__tablename__,
                         Target._meta.get_field('distance').model._meta.db_table)
        self.assertEqual(SaTarget.__tablename__, 'tom_targets_basetarget')

    def test_own_table_mappings_match_the_django_models(self):
        """The plugin-owned SA mappings hardcode names that are frozen by contract
        (customer raw SQL) -- assert they stay in step with the Django models."""
        self.assertEqual(SaSkymap.__tablename__, EventLocalization._meta.db_table)
        self.assertEqual(SaSkymapTile.__tablename__, SkymapTile._meta.db_table)
        self.assertEqual(SaEventCandidate.__tablename__, EventCandidate._meta.db_table)


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
        # the table's Event ID column links to the event-detail (str event_id) URL name
        self.assertContains(response, reverse('nonlocalizedevents:event-detail', args=(self.superevent1.event_id,)))
        self.assertContains(response, reverse('nonlocalizedevents:event-detail', args=(self.superevent2.event_id,)))

    def test_superevent_detail_view(self):
        """Both detail URL names now render the server-side page; no Vue remnants."""
        response = self.client.get(reverse('nonlocalizedevents:detail', args=(self.superevent1.pk,)))

        self.assertContains(response, self.superevent1.event_id)
        self.assertNotContains(response, 'vue')
        # a GW event resolves its per-type detail stub, which extends the generic page
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/detail/gw_detail.html')
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/nonlocalizedevent_detail.html')

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


class TestNonLocalizedEventTablePage(TestCase):
    """The SAGUARO-shaped htmx-table list page: columns, filters, partial refresh."""

    def setUp(self):
        self.user = User.objects.create(username='table_user')
        self.client.force_login(self.user)
        # a significant, close CBC event
        self.significant = NonLocalizedEventFactory.create(event_id='S250721aa')
        loc1 = EventLocalizationFactory.create(nonlocalizedevent=self.significant,
                                               distance_mean=40., distance_std=10.)
        EventSequenceFactory.create(
            nonlocalizedevent=self.significant, localization=loc1, sequence_id=1,
            event_subtype='INITIAL',
            details={'far': 1e-10, 'significant': True, 'group': 'CBC',
                     'classification': {'BNS': 0.95, 'BBH': 0.04, 'Terrestrial': 0.01},
                     'properties': {'HasNS': 0.97, 'HasRemnant': 0.9}},
        )
        # an insignificant, distant test event
        self.marginal = NonLocalizedEventFactory.create(event_id='MS250721bb')
        loc2 = EventLocalizationFactory.create(nonlocalizedevent=self.marginal,
                                               distance_mean=3000., distance_std=800.)
        EventSequenceFactory.create(
            nonlocalizedevent=self.marginal, localization=loc2, sequence_id=1,
            event_subtype='PRELIMINARY',
            details={'far': 1e-6, 'significant': False, 'group': 'CBC',
                     'classification': {'BBH': 0.7, 'Terrestrial': 0.3},
                     'properties': {'HasNS': 0.01, 'HasRemnant': 0.0}},
        )

    def test_science_columns_render(self):
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertContains(response, 'BNS')          # most likely classification
        self.assertContains(response, '40 ± 10 Mpc')  # SAGUARO-style distance
        self.assertContains(response, '97%')          # HasNS percent
        self.assertContains(response, 'fw-bold')      # significant row is bold
        self.assertContains(response, '3.0 ± 0.8 Gpc')  # Gpc conversion

    def test_flat_filter_form_no_advanced_collapse(self):
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertContains(response, 'filter-form')
        self.assertNotContains(response, 'advancedFilters')
        self.assertNotContains(response, 'Advanced')

    def test_filter_form_uses_our_crispy_layout_without_submit(self):
        """Regression: the DRF FilterSet underneath HTMXTableFilterSet pre-attaches
        its own crispy helper (stacked fields, bootstrap3, a Submit button) on every
        form access -- our flat layout must REPLACE it, not defer to it."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertNotContains(response, 'value="Submit"')  # htmx filtering has no submit button
        self.assertNotContains(response, 'btn-default')     # the DRF helper's bootstrap3 styling
        self.assertContains(response, 'col-12 col-md')      # our single-line flexible-width layout

    def test_general_search_matches_event_id_only(self):
        """The query field searches event_id -- not every model field, where common
        substrings match all rows and the search appears dead."""
        response = self.client.get(reverse('nonlocalizedevents:index'), {'query': 'MS2507'})
        self.assertContains(response, self.marginal.event_id)
        self.assertNotContains(response, self.significant.event_id)

        # 'Gravitational' matches both events' event_type; event_id-only search must not
        response = self.client.get(reverse('nonlocalizedevents:index'), {'query': 'Gravitational'})
        self.assertNotContains(response, self.marginal.event_id)
        self.assertNotContains(response, self.significant.event_id)

    def test_clear_filters_button_present(self):
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertContains(response, 'Clear Filters')
        self.assertContains(response, f'href="{reverse("nonlocalizedevents:index")}"')

    def test_column_headers_match_filter_labels(self):
        """HasNS/HasRemnant appear as both a column header and a filter label, so the
        correspondence is visible (SAGUARO's 'NS?'/'Bright?' abbreviations obscured it)."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertContains(response, 'HasNS')
        self.assertContains(response, 'HasRemnant')

    def test_event_link_opts_out_of_htmx_boost(self):
        """Regression: the table element carries hx-boost for sorting/pagination;
        without the opt-out, clicking an event link swaps the detail page INTO
        the table container instead of navigating to it."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        detail_url = reverse('nonlocalizedevents:event-detail', args=(self.significant.event_id,))
        self.assertContains(response, f'href="{detail_url}" hx-boost="false"')

    def test_htmx_request_returns_partial(self):
        response = self.client.get(reverse('nonlocalizedevents:index'), HTTP_HX_REQUEST='true')

        self.assertTemplateUsed(
            response, 'tom_nonlocalizedevents/partials/nonlocalizedevent_table_partial.html')
        # the partial carries no <form> element (the table attrs may reference
        # "#filter-form", so match the element, not the bare string)
        self.assertNotContains(response, '<form id="filter-form"')

    def test_inverse_far_filter_uses_latest_sequence(self):
        """1/FAR > 1 yr keeps only the far=1e-10 event (1/FAR ~ 300 yr)."""
        response = self.client.get(reverse('nonlocalizedevents:index'), {'inv_far_min': '1'})

        self.assertContains(response, self.significant.event_id)
        self.assertNotContains(response, self.marginal.event_id)

    def test_distance_filter(self):
        response = self.client.get(reverse('nonlocalizedevents:index'), {'distance_max': '100'})

        self.assertContains(response, self.significant.event_id)
        self.assertNotContains(response, self.marginal.event_id)

    def test_prefix_filter_test_events(self):
        response = self.client.get(reverse('nonlocalizedevents:index'), {'prefix': 'MS'})

        self.assertContains(response, self.marginal.event_id)
        self.assertNotContains(response, self.significant.event_id)

    def test_state_filter(self):
        self.marginal.state = 'RETRACTED'
        self.marginal.save()

        response = self.client.get(reverse('nonlocalizedevents:index'), {'state': 'RETRACTED'})

        self.assertContains(response, self.marginal.event_id)
        self.assertNotContains(response, self.significant.event_id)

    def test_object_list_contract_for_template_overrides(self):
        """SNEx2 overrides index.html and iterates object_list -- keep it in context."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        self.assertIn('object_list', response.context)
        self.assertEqual(len(response.context['object_list']), 2)


class TestEventTypeTabs(TestCase):
    """The lazy-loading event-type tabs: per-type querysets, stub tables and
    filters, the htmx region swap, and the URL-ordering guard."""

    def setUp(self):
        self.user = User.objects.create(username='tabs_user')
        self.client.force_login(self.user)
        self.gw = NonLocalizedEventFactory.create(event_id='S250721gw')  # model default type is GW
        self.grb = NonLocalizedEventFactory.create(event_id='S250721gr', event_type='GRB')
        self.xrt = NonLocalizedEventFactory.create(event_id='S250721xr', event_type='XRT')

    def test_index_lands_on_gw_tab(self):
        """The index IS the GW tab (William's default), with the full tab nav."""
        response = self.client.get(reverse('nonlocalizedevents:index'))

        for label in ('All', 'Gravitational Wave', 'Gamma-ray Burst', 'Neutrino',
                      'X-ray Transient', 'Unknown'):
            self.assertContains(response, label)
        self.assertContains(response, self.gw.event_id)
        self.assertNotContains(response, self.grb.event_id)  # GW tab: no other types
        self.assertContains(response, '1/FAR')               # the GW science table
        # lazy loading: exactly one table renders on page load
        self.assertEqual(response.content.decode().count('<table'), 1)

    def test_all_tab_shows_every_type(self):
        """All is the left-most tab, at tab/all/, with the generic table."""
        response = self.client.get(reverse('nonlocalizedevents:tab', args=('all',)))

        for event in (self.gw, self.grb, self.xrt):
            self.assertContains(response, event.event_id)

    def test_gw_tab_filters_type_and_uses_science_table(self):
        response = self.client.get(reverse('nonlocalizedevents:tab', args=('gw',)))

        self.assertContains(response, self.gw.event_id)
        self.assertNotContains(response, self.grb.event_id)
        self.assertContains(response, '1/FAR')                 # science columns present
        self.assertNotContains(response, '?sort=event_type')   # type column dropped on a typed tab

    def test_stub_tab_uses_generic_table_and_filters(self):
        response = self.client.get(reverse('nonlocalizedevents:tab', args=('grb',)))

        self.assertContains(response, self.grb.event_id)
        self.assertNotContains(response, self.gw.event_id)
        self.assertNotContains(response, '1/FAR')        # GW science columns absent
        self.assertNotContains(response, 'HasNS')        # GW science filters absent
        self.assertContains(response, 'General search')  # common filters present

    def test_xray_transient_tab(self):
        response = self.client.get(reverse('nonlocalizedevents:tab', args=('xray',)))

        self.assertContains(response, self.xrt.event_id)
        self.assertNotContains(response, self.gw.event_id)

    def test_unknown_tab_slug_404s(self):
        response = self.client.get(reverse('nonlocalizedevents:tab', args=('bogus',)))

        self.assertEqual(response.status_code, 404)

    def test_htmx_tab_click_returns_region_partial(self):
        response = self.client.get(
            reverse('nonlocalizedevents:tab', args=('gw',)),
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='event-tabs-region')

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/partials/event_tabs_region.html')
        self.assertContains(response, 'nav-tabs')     # the nav travels with the region
        self.assertContains(response, 'filter-form')  # so does the tab's filter form
        self.assertNotContains(response, '<h2>')      # but not the page chrome

    def test_tab_route_precedes_event_id_catch_all(self):
        """'tab' is a literal path segment the '<str:event_id>/' catch-all would
        otherwise swallow -- this guards the registration order."""
        match = resolve('/nonlocalizedevents/tab/gw/')

        self.assertEqual(match.view_name, 'nonlocalizedevents:tab')

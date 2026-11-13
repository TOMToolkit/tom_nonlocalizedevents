"""Tests of the Candidates workflow step: the table, its Active/Retired/All filter, the credible-region column and
the cell text.
"""
import json

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tom_targets.models import Target

from tom_nonlocalizedevents.models import CredibleRegion
from tom_nonlocalizedevents.tests.factories import EventCandidateFactory, TargetFactory
from tom_nonlocalizedevents.tests.test_event_summary import TAB_CLICK_HEADERS, EventPageTestCase
from tom_nonlocalizedevents.workflow.steps.candidates.views import cell_text

SORT_HEADERS = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'div.table-container'}


class CandidatesStepTestCase(EventPageTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inside = EventCandidateFactory.create(nonlocalizedevent=cls.event,
                                                  target=TargetFactory.create(name='AT2026inside'))
        cls.outside = EventCandidateFactory.create(nonlocalizedevent=cls.event,
                                                   target=TargetFactory.create(name='AT2026outside'))
        cls.retired = EventCandidateFactory.create(nonlocalizedevent=cls.event, viable=False,
                                                   viability_reason='host galaxy too distant',
                                                   target=TargetFactory.create(name='AT2026retired'))
        # the inside candidate's percentage differs per alert; the outside one has none
        for sequence, percent in zip(cls.sequences, (90, 50, 25)):
            CredibleRegion.objects.create(localization=sequence.localization, candidate=cls.inside,
                                          smallest_percent=percent)
        CredibleRegion.objects.create(localization=cls.sequences[-1].localization, candidate=cls.retired,
                                      smallest_percent=95)
        cls.candidates_url = reverse('nonlocalizedevents:candidates', args=(cls.event.event_id,))


class TestCandidatesStep(CandidatesStepTestCase):
    def test_page_shows_active_candidates_with_the_selected_alerts_percentages(self):
        response = self.client.get(self.candidates_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/workflow_steps/candidates.html')
        self.assertIn('id="event-header"', content)
        # the sky-map data lists every candidate, so the table rows are told apart by their link markup
        self.assertIn('>AT2026inside</a>', content)
        self.assertIn('>AT2026outside</a>', content)
        self.assertNotIn('>AT2026retired</a>', content)
        self.assertIn('<td >25%</td>', content)
        self.assertIn('credible regions of alert 3', content)
        self.assertIn('<input type="hidden" name="sequence" value="3">', content)

    def test_sequence_parameter_changes_the_percentages(self):
        content = self.client.get(self.candidates_url, {'sequence': '1'}).content.decode()

        self.assertIn('<td >90%</td>', content)
        self.assertNotIn('25%', content)

    def test_filter_form_is_not_restored_by_the_browser_on_reload(self):
        # the server renders Active selected and an Active table; a browser that restored the dropdown's last
        # value on reload would show "All" over a table of active candidates only
        content = self.client.get(self.candidates_url).content.decode()

        self.assertIn('<option value="true" selected>Active</option>', content)
        self.assertRegex(content, r'<form id="filter-form"[^>]*autocomplete="off"')

    def test_retired_and_all_filters(self):
        retired = self.client.get(self.candidates_url, {'viable': 'false'}).content.decode()
        everything = self.client.get(self.candidates_url, {'viable': 'all'}).content.decode()

        self.assertIn('>AT2026retired</a> (retired)', retired)
        self.assertIn('host galaxy too distant', retired)
        self.assertNotIn('>AT2026inside</a>', retired)
        for name in ('AT2026inside', 'AT2026outside', 'AT2026retired'):
            self.assertIn(f'>{name}</a>', everything)

    def test_name_search(self):
        content = self.client.get(self.candidates_url, {'query': 'outside'}).content.decode()

        self.assertIn('>AT2026outside</a>', content)
        self.assertNotIn('>AT2026inside</a>', content)

    def test_sorting_returns_the_table_partial_with_the_alert_preserved(self):
        response = self.client.get(self.candidates_url, {'sequence': '1', 'sort': '-smallest_percent'},
                                   **SORT_HEADERS)
        content = response.content.decode()

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/partials/candidate_table_partial.html')
        self.assertNotIn('id="event-header"', content)
        self.assertIn('90%', content)
        self.assertLess(content.index('AT2026inside'), content.index('AT2026outside'))
        self.assertIn('sequence=1', content)  # the sort links keep the selected alert

    def test_tab_click_returns_the_summary_update_with_the_table_out_of_band(self):
        response = self.client.get(self.candidates_url, {'sequence': '2'}, **TAB_CLICK_HEADERS)
        content = response.content.decode()

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/partials/event_summary_update.html')
        self.assertIn('id="event-header"', content)
        self.assertIn('<div id="workflow-step-content" hx-swap-oob="true">', content)
        self.assertIn('credible regions of alert 2', content)
        self.assertIn('<td >50%</td>', content)

    def test_history_restore_gets_the_full_page(self):
        response = self.client.get(self.candidates_url, HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true')

        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/workflow_steps/candidates.html')

    def test_alert_without_localization_has_no_percentages(self):
        self.sequences[-1].localization = None
        self.sequences[-1].save()

        content = self.client.get(self.candidates_url).content.decode()

        self.assertIn('>AT2026inside</a>', content)
        self.assertNotIn('%</td>', content)

    def test_login_required(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.candidates_url).status_code, 302)


class TestCandidatesCellText(CandidatesStepTestCase):
    def test_counts_from_annotations_or_queries(self):
        self.assertEqual(cell_text(self.event), '2 active / 1 retired')
        self.event.active_candidate_count = 7
        self.event.retired_candidate_count = 1
        self.assertEqual(cell_text(self.event), '7 active / 1 retired')

    def test_list_page_shows_the_counts_in_the_candidates_column_with_constant_queries(self):
        url = reverse('nonlocalizedevents:tab', args=('gw',))

        with CaptureQueriesContext(connection) as captured:
            content = self.client.get(url).content.decode()

        self.assertIn(f'href="{self.candidates_url}" hx-boost="false">2 active / 1 retired</a>', content)
        # the counts come from annotations of the list query, not from a count query per row
        per_row_count_queries = [query['sql'] for query in captured.captured_queries
                                 if query['sql'].startswith('SELECT COUNT(*)')
                                 and 'tom_nonlocalizedevents_eventcandidate' in query['sql']]
        self.assertEqual(per_row_count_queries, [])


class TestCandidateViability(CandidatesStepTestCase):
    def viability_url(self, candidate):
        return reverse('nonlocalizedevents:candidate-viability', args=(self.event.event_id, candidate.pk))

    def test_retire_with_the_prompted_reason(self):
        response = self.client.post(self.viability_url(self.inside), {'viable': 'false'}, HTTP_HX_REQUEST='true',
                                    HTTP_HX_PROMPT='host galaxy at z=0.21')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(json.loads(response['HX-Trigger']), {'candidatesChanged': {}})
        self.inside.refresh_from_db()
        self.assertFalse(self.inside.viable)
        self.assertEqual(self.inside.viability_reason, 'host galaxy at z=0.21')

    def test_retire_with_a_form_field_reason_and_activate(self):
        self.client.post(self.viability_url(self.inside), {'viable': 'false', 'viability_reason': 'too faint'})
        self.inside.refresh_from_db()
        self.assertFalse(self.inside.viable)

        response = self.client.post(self.viability_url(self.inside), {'viable': 'true'})

        self.assertEqual(response.status_code, 204)
        self.inside.refresh_from_db()
        self.assertTrue(self.inside.viable)
        self.assertEqual(self.inside.viability_reason, '')

    def test_retiring_needs_a_reason(self):
        response = self.client.post(self.viability_url(self.inside), {'viable': 'false'}, HTTP_HX_PROMPT='  ')

        self.assertEqual(response.status_code, 400)
        self.inside.refresh_from_db()
        self.assertTrue(self.inside.viable)

    def test_candidate_must_belong_to_the_event_in_the_url(self):
        other_event_url = reverse('nonlocalizedevents:candidate-viability', args=('S000000xx', self.inside.pk))

        self.assertEqual(self.client.post(other_event_url, {'viable': 'true'}).status_code, 404)

    def test_login_and_csrf_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.viability_url(self.inside), {'viable': 'true'}).status_code, 302)

        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.user)
        page = strict_client.get(self.candidates_url)
        token = page.cookies['csrftoken'].value
        self.assertIn('hx-headers=\'{"X-CSRFToken": "', page.content.decode())
        # tom_base's CSRF failure view redirects to the login page rather than answering 403
        rejected = strict_client.post(self.viability_url(self.inside), {'viable': 'true'})
        self.assertEqual(rejected.status_code, 302)
        self.assertIn('/accounts/login/', rejected['Location'])
        self.assertEqual(strict_client.post(self.viability_url(self.inside), {'viable': 'true'},
                                            HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_table_has_the_buttons_and_reloads_on_the_event(self):
        content = self.client.get(self.candidates_url, {'viable': 'all'}).content.decode()

        self.assertIn(f'hx-post="{self.viability_url(self.inside)}"', content)
        self.assertIn('hx-prompt="Reason for retiring AT2026inside"', content)
        self.assertIn('>Activate</button>', content)
        self.assertIn('hx-trigger="candidatesChanged from:body"', content)

    def test_table_reload_carries_the_sky_map_data_out_of_band(self):
        content = self.client.get(self.candidates_url, **SORT_HEADERS).content.decode()

        self.assertIn('<div id="event-sky-map-data-holder" hx-swap-oob="true">', content)


class TestCandidateAddForm(CandidatesStepTestCase):
    def setUp(self):
        super().setUp()
        self.add_url = reverse('nonlocalizedevents:candidate-add', args=(self.event.event_id,))

    def test_the_page_carries_the_form(self):
        content = self.client.get(self.candidates_url).content.decode()

        self.assertIn(f'hx-post="{self.add_url}"', content)
        self.assertIn('Add candidate', content)

    def test_adding_a_target_by_name_creates_the_candidate_and_reloads_the_table(self):
        target = TargetFactory.create(name='AT2026new')

        response = self.client.post(self.add_url, {'target': 'AT2026new'}, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        # the answer is a Django message in tom_base's messages partial, which swaps itself into the page's
        # messages area; the form itself swaps nothing
        self.assertContains(response, 'id="messages" hx-swap-oob="true"')
        self.assertContains(response, f'AT2026new added as a candidate of {self.event.event_id}.')
        self.assertEqual(json.loads(response['HX-Trigger']), {'candidatesChanged': {}})
        self.assertTrue(self.event.candidates.filter(target=target).exists())

    def test_adding_an_existing_candidate_changes_nothing_and_says_so(self):
        response = self.client.post(self.add_url, {'target': 'AT2026inside'})

        self.assertContains(response, f'AT2026inside already is a candidate of {self.event.event_id}.')
        self.assertNotIn('HX-Trigger', response)
        self.assertEqual(self.event.candidates.filter(target=self.inside.target).count(), 1)

    def test_an_unknown_target_is_reported_as_an_error_message(self):
        response = self.client.post(self.add_url, {'target': 'nobody'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No target named nobody, and no target with that primary key.')
        self.assertContains(response, 'alert-danger')
        self.assertNotIn('HX-Trigger', response)

    def test_a_name_matching_several_targets_is_reported_as_an_error_message(self):
        TargetFactory.create(name='AT2026 inside')

        response = self.client.post(self.add_url, {'target': 'at2026inside'})

        self.assertContains(response, 'Several targets match at2026inside: AT2026 inside, AT2026inside.')
        self.assertNotIn('HX-Trigger', response)

    def test_an_empty_name_is_reported_as_an_error_message(self):
        response = self.client.post(self.add_url, {'target': '  '})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A target&#x27;s name, alias or primary key is required.')
        self.assertNotIn('HX-Trigger', response)

    def test_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.post(self.add_url, {'target': 'AT2026inside'}).status_code, 302)


class TestCandidateDelete(CandidatesStepTestCase):
    def delete_url(self, candidate):
        return reverse('nonlocalizedevents:candidate-delete', args=(self.event.event_id, candidate.pk))

    def test_the_table_has_a_delete_button_that_asks_for_confirmation(self):
        content = self.client.get(self.candidates_url).content.decode()

        self.assertIn(f'hx-delete="{self.delete_url(self.inside)}"', content)
        self.assertIn(f'hx-confirm="Remove AT2026inside from the candidates of {self.event.event_id}? '
                      'The target is kept."', content)

    def test_deleting_removes_the_candidate_keeps_the_target_and_reloads_the_table(self):
        response = self.client.delete(self.delete_url(self.inside), HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="messages" hx-swap-oob="true"')
        self.assertContains(response, f'AT2026inside is no longer a candidate of {self.event.event_id}; '
                                      'the target is kept.')
        self.assertEqual(json.loads(response['HX-Trigger']), {'candidatesChanged': {}})
        self.assertFalse(self.event.candidates.filter(pk=self.inside.pk).exists())
        self.assertFalse(CredibleRegion.objects.filter(candidate_id=self.inside.pk).exists())
        self.assertTrue(Target.objects.filter(pk=self.inside.target_id).exists())

    def test_candidate_must_belong_to_the_event_in_the_url(self):
        other_url = reverse('nonlocalizedevents:candidate-delete', args=('S000000zz', self.inside.pk))

        self.assertEqual(self.client.delete(other_url).status_code, 404)
        self.assertTrue(self.event.candidates.filter(pk=self.inside.pk).exists())

    def test_login_and_csrf_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.delete(self.delete_url(self.inside)).status_code, 302)

        # as for the retire button: tom_base's CSRF failure view redirects to the login page rather than answering 403
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.user)
        token = strict_client.get(self.candidates_url).cookies['csrftoken'].value
        rejected = strict_client.delete(self.delete_url(self.inside))
        self.assertEqual(rejected.status_code, 302)
        self.assertIn('/accounts/login/', rejected['Location'])
        self.assertTrue(self.event.candidates.filter(pk=self.inside.pk).exists())
        self.assertEqual(strict_client.delete(self.delete_url(self.inside), HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertFalse(self.event.candidates.filter(pk=self.inside.pk).exists())

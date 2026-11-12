"""Tests of the Candidates workflow step: the table, its Active/Retired/All filter, the credible-region column and
the cell text.
"""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

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

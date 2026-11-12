"""Tests of the workflow step registry, the workflow step links in the header, the list columns and the stub
workflow step."""
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_nonlocalizedevents.tests.factories import NonLocalizedEventFactory
from tom_nonlocalizedevents.tests.test_event_summary import TAB_CLICK_HEADERS, EventPageTestCase
from tom_nonlocalizedevents.workflow.registry import (DEFAULT_WORKFLOW_STEPS, get_workflow_steps, workflow_step_columns,
                                                      workflow_step_links)


def fixed_cell_text(event):
    return f'{event.event_id[-2:]} obs'


# two entries that reuse the plugin's own pages under other labels, as a TOM's settings would list its pages
OVERVIEW_AS_OBSERVATIONS = {'label': 'Observations', 'url_name': 'nonlocalizedevents:event-detail',
                            'cell_text': 'tom_nonlocalizedevents.tests.test_workflow_steps.fixed_cell_text'}
STUB_AS_PLANS = {'label': 'Plans', 'url_name': 'nonlocalizedevents:stub'}


class TestRegistry(TestCase):
    def setUp(self):
        get_workflow_steps.cache_clear()

    def test_default_is_candidates_then_the_stub(self):
        workflow_steps = get_workflow_steps()

        self.assertEqual([workflow_step.label for workflow_step in workflow_steps], ['Candidates', 'Stub'])
        self.assertEqual(DEFAULT_WORKFLOW_STEPS[1]['url_name'], 'nonlocalizedevents:stub')
        event = NonLocalizedEventFactory.create(event_id='S260901zz')
        self.assertEqual(workflow_steps[0].url(event), '/nonlocalizedevents/S260901zz/candidates/')
        self.assertEqual(workflow_steps[0].cell_text(event), '0 active / 0 retired')
        self.assertEqual(workflow_steps[1].url(event), '/nonlocalizedevents/S260901zz/stub/')
        self.assertEqual(workflow_steps[1].cell_text(event), 'open')
        self.assertEqual([workflow_step.column_name for workflow_step in workflow_steps], ['candidates', 'stub'])

    @override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[OVERVIEW_AS_OBSERVATIONS, STUB_AS_PLANS])
    def test_a_tom_replaces_the_list(self):
        workflow_steps = get_workflow_steps()
        event = NonLocalizedEventFactory.build(event_id='S260901zz')

        self.assertEqual([workflow_step.label for workflow_step in workflow_steps], ['Observations', 'Plans'])
        self.assertEqual(workflow_steps[0].url(event), '/nonlocalizedevents/S260901zz/')
        self.assertEqual(workflow_steps[0].cell_text(event), 'zz obs')
        self.assertEqual(workflow_steps[1].url(event), '/nonlocalizedevents/S260901zz/stub/')
        self.assertEqual(workflow_steps[1].cell_text(event), 'Plans')  # no cell_text: the label

    def test_the_cache_follows_the_setting(self):
        self.assertEqual(len(get_workflow_steps()), 2)
        with override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[]):
            self.assertEqual(get_workflow_steps(), [])
        self.assertEqual(len(get_workflow_steps()), 2)

    def test_configuration_errors_name_the_entry(self):
        event = NonLocalizedEventFactory.build(event_id='S260901zz')
        cases = {
            'no label': [{'url_name': 'nonlocalizedevents:stub'}],
            'no url_name': [{'label': 'A'}],
            'bad cell_text path': [{'label': 'A', 'url_name': 'nonlocalizedevents:stub',
                                    'cell_text': 'no.such.module'}],
            'cell_text not callable': [{'label': 'A', 'url_name': 'nonlocalizedevents:stub',
                                        'cell_text': f'{__name__}.STUB_AS_PLANS'}],
            'duplicate labels': [STUB_AS_PLANS, STUB_AS_PLANS],
            'not a list': {'label': 'A'},
        }
        for name, value in cases.items():
            with self.subTest(name=name), override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=value):
                with self.assertRaises(ImproperlyConfigured):
                    get_workflow_steps()
        with override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[{'label': 'A', 'url_name': 'no-such-url-name'}]):
            with self.assertRaisesRegex(ImproperlyConfigured, 'no-such-url-name'):
                get_workflow_steps()[0].url(event)

    @override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[OVERVIEW_AS_OBSERVATIONS, STUB_AS_PLANS])
    def test_links_mark_the_current_workflow_step_by_path(self):
        event = NonLocalizedEventFactory.build(event_id='S260901zz')

        links = workflow_step_links(event, '/nonlocalizedevents/S260901zz/stub/')

        self.assertEqual(links, [
            {'label': 'Observations', 'url': '/nonlocalizedevents/S260901zz/', 'current': False},
            {'label': 'Plans', 'url': '/nonlocalizedevents/S260901zz/stub/', 'current': True},
        ])
        self.assertTrue(workflow_step_links(event, '/nonlocalizedevents/S260901zz/')[0]['current'])

    @override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[OVERVIEW_AS_OBSERVATIONS, STUB_AS_PLANS])
    def test_columns_render_the_cell_text_linked_to_the_workflow_step(self):
        event = NonLocalizedEventFactory.build(event_id='S260901zz')
        columns = workflow_step_columns()

        self.assertEqual([key for key, _ in columns], ['observations', 'plans'])
        self.assertEqual(columns[0][1].verbose_name, 'Observations')
        self.assertFalse(columns[0][1].orderable)
        self.assertEqual(columns[0][1].render(event),
                         '<a href="/nonlocalizedevents/S260901zz/" hx-boost="false">zz obs</a>')


class TestWorkflowStepsOnThePages(EventPageTestCase):
    def setUp(self):
        super().setUp()
        get_workflow_steps.cache_clear()

    def test_header_lists_overview_first_then_the_steps_with_the_current_one_marked(self):
        response = self.client.get(self.url)
        content = response.content.decode()

        overview = content.index('>Overview</a>')
        stub = content.index('>Stub</a>')
        self.assertLess(overview, stub)
        self.assertIn('btn-primary rounded-pill">Overview', content)
        self.assertIn('btn-outline-primary rounded-pill">Stub', content)
        self.assertIn(f'href="/nonlocalizedevents/{self.event.event_id}/stub/?sequence=3"', content)

    def test_stub_page_renders_with_the_summary_and_answers_tab_clicks(self):
        url = reverse('nonlocalizedevents:stub', args=(self.event.event_id,))

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/workflow_steps/stub.html')
        self.assertTemplateUsed(response, 'tom_nonlocalizedevents/event_page_base.html')
        self.assertContains(response, 'btn-primary rounded-pill">Stub')
        self.assertContains(response, '<strong>3: INITIAL</strong>')

        update = self.client.get(url, {'sequence': '1'}, **TAB_CLICK_HEADERS).content.decode()
        self.assertIn('<div id="workflow-step-content" hx-swap-oob="true">', update)
        self.assertIn('<strong>1: EARLYWARNING</strong>', update)

    def test_stub_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.get(reverse('nonlocalizedevents:stub', args=(self.event.event_id,))).status_code,
                         302)

    @override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[OVERVIEW_AS_OBSERVATIONS, STUB_AS_PLANS])
    def test_gw_tab_gets_one_column_per_workflow_step_and_other_tabs_do_not(self):
        NonLocalizedEventFactory.create(event_id='S260901gr',
                                        event_type=NonLocalizedEvent.NonLocalizedEventType.GAMMA_RAY_BURST)
        workflow_step_cell = f'href="/nonlocalizedevents/{self.event.event_id}/" hx-boost="false">aa obs</a>'

        gw = self.client.get(reverse('nonlocalizedevents:tab', args=('gw',))).content.decode()
        grb = self.client.get(reverse('nonlocalizedevents:tab', args=('grb',))).content.decode()

        self.assertIn(workflow_step_cell, gw)
        self.assertIn(f'href="/nonlocalizedevents/{self.event.event_id}/stub/" hx-boost="false">Plans</a>', gw)
        self.assertNotIn('aa obs', grb)
        self.assertNotIn('>Plans<', grb)
        with override_settings(NONLOCALIZEDEVENTS_WORKFLOW_STEPS=[]):
            gw_without_workflow_steps = self.client.get(reverse('nonlocalizedevents:tab',
                                                                args=('gw',))).content.decode()
        self.assertEqual(gw.count('<th'), gw_without_workflow_steps.count('<th') + 2)

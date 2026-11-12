"""Tests of services.alerts: the typed views of EventSequence.details."""
from datetime import datetime, timezone

from django.test import TestCase

from tom_nonlocalizedevents.models import ExternalCoincidence, NonLocalizedEvent
from tom_nonlocalizedevents.services.alerts import (ExternalCoincidenceAlert, GenericAlert, GravitationalWaveAlert,
                                                    parse_alert, parse_external_coincidence)
from tom_nonlocalizedevents.tests.factories import EventSequenceFactory, NonLocalizedEventFactory

CBC_ALERT = {
    'far': 9.11e-14, 'time': '2026-07-25T16:01:06.669Z', 'group': 'CBC', 'search': 'AllSky', 'duration': None,
    'pipeline': 'gstlal', 'properties': {'HasNS': 1.0, 'HasMassGap': 0.0, 'HasRemnant': 1.0},
    'instruments': ['H1', 'L1'], 'significant': True,
    'classification': {'BBH': 0.0, 'BNS': 0.9999, 'NSBH': 0.0, 'Terrestrial': 0.0001},
    'skymap_filename': 'bayestar.multiorder.fits,0', 'central_frequency': None,
}
SSM_ALERT = {
    'far': 3e-9, 'time': '2026-11-02T01:02:03.000Z', 'group': 'CBC', 'search': 'SSM', 'pipeline': 'MBTA',
    'properties': {'HasSSM': 0.8}, 'classification': None, 'instruments': ['H1', 'L1', 'V1'], 'significant': False,
}
BURST_ALERT = {
    'far': 1e-8, 'time': '2026-11-03T04:05:06.000Z', 'group': 'Burst', 'search': 'AllSky', 'pipeline': 'CWB',
    'properties': None, 'classification': None, 'instruments': ['H1', 'L1'], 'significant': True,
    'duration': 0.012, 'central_frequency': 180.5,
}


class TestGravitationalWaveAlert(TestCase):
    def test_cbc_alert(self):
        alert = GravitationalWaveAlert.model_validate(CBC_ALERT)

        self.assertEqual(alert.time, datetime(2026, 7, 25, 16, 1, 6, 669000, tzinfo=timezone.utc))
        self.assertEqual(alert.far, 9.11e-14)
        self.assertTrue(alert.significant)
        self.assertEqual(alert.instruments, ['H1', 'L1'])
        self.assertEqual(alert.classification_by_probability()[0], ('BNS', 0.9999))
        self.assertEqual(alert.most_likely_classification(), 'BNS')
        self.assertFalse(alert.is_burst)
        self.assertFalse(alert.is_ssm)
        self.assertIsNone(alert.duration)

    def test_ssm_alert_has_no_classification(self):
        alert = GravitationalWaveAlert.model_validate(SSM_ALERT)

        self.assertTrue(alert.is_ssm)
        self.assertEqual(alert.classification, {})
        self.assertEqual(alert.properties, {'HasSSM': 0.8})
        self.assertEqual(alert.most_likely_classification(), 'SSM')

    def test_burst_alert(self):
        alert = GravitationalWaveAlert.model_validate(BURST_ALERT)

        self.assertTrue(alert.is_burst)
        self.assertEqual(alert.properties, {})
        self.assertEqual(alert.duration, 0.012)
        self.assertEqual(alert.central_frequency, 180.5)
        self.assertEqual(alert.most_likely_classification(), 'Burst')

    def test_null_entries_inside_dictionaries_are_dropped(self):
        alert = GravitationalWaveAlert.model_validate({'classification': {'BNS': 0.5, 'Terrestrial': None},
                                                      'properties': {'HasNS': None}})

        self.assertEqual(alert.classification, {'BNS': 0.5})
        self.assertEqual(alert.properties, {})

    def test_empty_and_graceDB_backfill_payloads(self):
        self.assertEqual(GravitationalWaveAlert.model_validate({}).instruments, [])
        self.assertEqual(GravitationalWaveAlert.model_validate({'pipeline': 'gstlal'}).pipeline, 'gstlal')
        self.assertEqual(GravitationalWaveAlert.model_validate({}).most_likely_classification(), '')

    def test_unknown_keys_are_ignored(self):
        alert = GravitationalWaveAlert.model_validate({'far': 1e-9, 'new_field_from_a_future_schema': 1})

        self.assertEqual(alert.far, 1e-9)


class TestParseAlert(TestCase):
    def test_gravitational_wave_sequences_get_the_igwn_schema(self):
        sequence = EventSequenceFactory.create(details=CBC_ALERT)

        alert = parse_alert(sequence)

        self.assertIsInstance(alert, GravitationalWaveAlert)
        self.assertEqual(alert.pipeline, 'gstlal')

    def test_other_event_types_get_the_generic_alert(self):
        event = NonLocalizedEventFactory.create(event_type=NonLocalizedEvent.NonLocalizedEventType.GAMMA_RAY_BURST)
        sequence = EventSequenceFactory.create(nonlocalizedevent=event,
                                               details={'trigger': 1234, 'nested': {'a': 1}, 'empty': None})

        alert = parse_alert(sequence)

        self.assertIsInstance(alert, GenericAlert)
        self.assertEqual(alert.scalar_items(), [('trigger', 1234)])

    def test_none_and_non_dictionary_details_parse_as_empty(self):
        for details in [None, {}, 'text', 7]:
            with self.subTest(details=details):
                alert = parse_alert(EventSequenceFactory.create(details=details))
                self.assertEqual(alert, GravitationalWaveAlert())

    def test_malformed_details_are_logged_and_parse_as_empty(self):
        sequence = EventSequenceFactory.create(details={'far': 'not a number'})

        with self.assertLogs('tom_nonlocalizedevents.services.alerts', level='WARNING'):
            alert = parse_alert(sequence)

        self.assertEqual(alert, GravitationalWaveAlert())


class TestParseExternalCoincidence(TestCase):
    def test_none_without_a_coincidence(self):
        self.assertIsNone(parse_external_coincidence(EventSequenceFactory.create()))

    def test_coincidence_fields(self):
        coincidence = ExternalCoincidence.objects.create(details={
            'gcn_notice_id': 524680123, 'ivorn': 'ivo://nasa.gsfc.gcn/Fermi#GBM_Fin_Pos2026-11-01T03:12:46.00',
            'observatory': 'Fermi', 'search': 'GRB', 'time_difference': 1.7, 'time_coincidence_far': 1e-11,
            'time_sky_position_coincidence_far': 2e-12})
        sequence = EventSequenceFactory.create(external_coincidence=coincidence)

        alert = parse_external_coincidence(sequence)

        self.assertEqual(alert, ExternalCoincidenceAlert(
            gcn_notice_id=524680123, ivorn='ivo://nasa.gsfc.gcn/Fermi#GBM_Fin_Pos2026-11-01T03:12:46.00',
            observatory='Fermi', search='GRB', time_difference=1.7, time_coincidence_far=1e-11,
            time_sky_position_coincidence_far=2e-12))

    def test_empty_coincidence_details(self):
        sequence = EventSequenceFactory.create(external_coincidence=ExternalCoincidence.objects.create(details=None))

        self.assertEqual(parse_external_coincidence(sequence), ExternalCoincidenceAlert())

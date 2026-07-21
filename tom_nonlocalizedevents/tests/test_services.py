"""Tests for the services package (event_ingest + gracedb)."""
import importlib
from io import StringIO
from unittest import mock

import responses

from django.core.management import call_command
from django.test import TestCase

from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_nonlocalizedevents.services.event_ingest import ingest_igwn_event_from_data
from tom_nonlocalizedevents.services.gracedb import _build_gracedb_alert_data, ingest_event_from_gracedb


class TestServicesPackageImports(TestCase):
    def test_services_modules_and_handlers_import(self):
        """Import-wiring smoke test: every services module and the alert handler import cleanly."""
        for module_name in (
            'tom_nonlocalizedevents.services',
            'tom_nonlocalizedevents.services.event_ingest',
            'tom_nonlocalizedevents.services.gracedb',
            'tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler',
        ):
            importlib.import_module(module_name)

    def test_public_names_exported_from_package(self):
        """The package __init__ exports the two public entry points."""
        from tom_nonlocalizedevents import services
        self.assertTrue(callable(services.ingest_igwn_event_from_data))
        self.assertTrue(callable(services.ingest_event_from_gracedb))


class TestIngestIgwnEventFromData(TestCase):
    def test_malformed_alert_returns_none(self):
        nle, seq = ingest_igwn_event_from_data({'alert_type': 'INITIAL'})

        self.assertIsNone(nle)
        self.assertIsNone(seq)

    def test_retraction_sets_state_and_creates_no_sequence(self):
        nle, seq = ingest_igwn_event_from_data({'superevent_id': 'S250717a', 'alert_type': 'RETRACTION'})

        self.assertEqual(nle.state, NonLocalizedEvent.NonLocalizedEventState.RETRACTED)
        self.assertIsNone(seq)

    def test_alert_without_any_skymap_ingests_without_localization(self):
        """Regression: external_coincidence was read before assignment (NameError) when the
        alert carried no combined skymap."""
        alert = {
            'superevent_id': 'S250717b',
            'alert_type': 'INITIAL',
            'sequence_num': 1,
            'event': {'pipeline': 'gstlal', 'far': 1e-9},
        }

        nle, seq = ingest_igwn_event_from_data(alert, ingestor_source='test')

        self.assertEqual(nle.event_id, 'S250717b')
        self.assertIsNone(seq.localization)
        self.assertIsNone(seq.external_coincidence)
        self.assertEqual(seq.details, {'pipeline': 'gstlal', 'far': 1e-9})
        self.assertEqual(seq.ingestor_source, 'test')

    @mock.patch('tom_nonlocalizedevents.services.event_ingest.create_localization_for_skymap')
    def test_embedded_skymap_bytes_are_used_and_stripped_from_details(self, mock_create):
        """The live IGWN stream embeds skymap bytes; they must reach the localization
        pipeline and must never land in the details JSON."""
        mock_create.return_value = None
        alert = {
            'superevent_id': 'S250717c',
            'alert_type': 'PRELIMINARY',
            'sequence_num': 1,
            'event': {'pipeline': 'gstlal', 'skymap': b'FAKE-FITS-BYTES'},
        }

        nle, seq = ingest_igwn_event_from_data(alert)

        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs['skymap_bytes'], b'FAKE-FITS-BYTES')
        self.assertNotIn('skymap', seq.details)

    @responses.activate
    @mock.patch('tom_nonlocalizedevents.services.event_ingest.create_localization_for_skymap')
    def test_skymap_url_is_fetched_when_no_bytes_embedded(self, mock_create):
        responses.get('http://example.com/skymap.fits', body=b'FAKE-FITS-BYTES')
        mock_create.return_value = None
        alert = {
            'superevent_id': 'S250717d',
            'alert_type': 'PRELIMINARY',
            'sequence_num': 1,
            'urls': {'skymap': 'http://example.com/skymap.fits'},
            'event': {'pipeline': 'gstlal'},
        }

        ingest_igwn_event_from_data(alert)

        self.assertEqual(mock_create.call_args.kwargs['skymap_bytes'], b'FAKE-FITS-BYTES')


class TestBuildGracedbAlertData(TestCase):
    def test_versioned_filename_uses_embedded_version(self):
        data = _build_gracedb_alert_data('https://gracedb.ligo.org/api/', 'S1', 'bayestar.multiorder.fits,1', 5)

        self.assertEqual(data['sequence_num'], 2)  # version 1 is 0-indexed
        self.assertEqual(data['event'], {'pipeline': 'bayestar'})
        self.assertEqual(data['urls']['skymap'],
                         'https://gracedb.ligo.org/api/superevents/S1/files/bayestar.multiorder.fits,1')

    def test_unversioned_filename_falls_back_to_index(self):
        data = _build_gracedb_alert_data('https://gracedb.ligo.org/api/', 'S1', 'bayestar.multiorder.fits', 3)

        self.assertEqual(data['sequence_num'], 4)

    def test_combined_skymap_routed_to_combined_url(self):
        data = _build_gracedb_alert_data('https://gracedb.ligo.org/api/', 'S1', 'combined-ext.multiorder.fits,0', 0)

        self.assertIn('combined_skymap', data['urls'])
        self.assertNotIn('skymap', data['urls'])
        self.assertEqual(data['event'], {})


class TestIngestEventFromGracedb(TestCase):
    @responses.activate
    def test_ingests_each_skymap_file(self):
        responses.get(
            'https://gracedb.ligo.org/api/superevents/S250717e/files/',
            json={'bayestar.multiorder.fits,0': 'url', 'S250717e-1-Preliminary.xml': 'url'},
        )

        with mock.patch('tom_nonlocalizedevents.services.gracedb.ingest_igwn_event_from_data') as mock_ingest:
            mock_ingest.return_value = (mock.Mock(), mock.Mock())
            success_count, errors = ingest_event_from_gracedb('S250717e')

        self.assertEqual(success_count, 1)  # only the .fits file, not the .xml
        self.assertEqual(errors, [])
        self.assertEqual(mock_ingest.call_args.kwargs.get('ingestor_source'), 'gracedb')

    @responses.activate
    def test_no_skymap_files_reports_error(self):
        responses.get('https://gracedb.ligo.org/api/superevents/S250717f/files/', json={'foo.xml': 'url'})

        success_count, errors = ingest_event_from_gracedb('S250717f')

        self.assertEqual(success_count, 0)
        self.assertEqual(len(errors), 1)


class TestIngestLVKEventCommand(TestCase):
    @mock.patch('tom_nonlocalizedevents.management.commands.ingest_LVK_event.ingest_event_from_gracedb')
    def test_command_invokes_service_and_reports(self, mock_ingest):
        mock_ingest.return_value = (1, [])
        out = StringIO()

        call_command('ingest_LVK_event', 'S230518h', stdout=out)

        mock_ingest.assert_called_once_with('S230518h')
        self.assertIn('Successfully ingested 1 new sequence(s)', out.getvalue())

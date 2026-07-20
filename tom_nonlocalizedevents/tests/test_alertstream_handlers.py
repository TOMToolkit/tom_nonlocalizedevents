"""Tests for the alert-stream handlers."""
from types import SimpleNamespace

from django.test import TestCase, override_settings

from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from tom_nonlocalizedevents.models import NonLocalizedEvent

# a minimal IGWN alert with no skymap, so ingestion needs no network access
ALERT = {
    'superevent_id': 'S250720a',
    'alert_type': 'PRELIMINARY',
    'event': {'pipeline': 'gstlal', 'far': 1e-9},
}


class TestHandleIgwnMessage(TestCase):
    """The handler only unwraps the message and applies the test-alert gate;
    ingestion itself is the service layer's (separately tested) job."""

    def test_alert_packed_as_list(self):
        """hop-client <=0.6 delivered message.content as a single-element list (#77)."""
        message = SimpleNamespace(content=[dict(ALERT)])

        nle, seq = handle_igwn_message(message, metadata=None)

        self.assertEqual(nle.event_id, 'S250720a')
        self.assertEqual(seq.ingestor_source, 'hop')

    def test_alert_packed_as_dict(self):
        """hop-client >0.6 delivers the alert dict directly (#77)."""
        message = SimpleNamespace(content=dict(ALERT))

        nle, seq = handle_igwn_message(message, metadata=None)

        self.assertEqual(nle.event_id, 'S250720a')
        self.assertEqual(seq.sequence_id, 1)

    @override_settings(SAVE_TEST_ALERTS=False)
    def test_test_alerts_skipped_when_configured_off(self):
        message = SimpleNamespace(content=dict(ALERT, superevent_id='MS250720b'))

        nle, seq = handle_igwn_message(message, metadata=None)

        self.assertIsNone(nle)
        self.assertIsNone(seq)
        self.assertFalse(NonLocalizedEvent.objects.filter(event_id='MS250720b').exists())

    def test_test_alerts_saved_by_default(self):
        message = SimpleNamespace(content=dict(ALERT, superevent_id='MS250720c'))

        nle, _ = handle_igwn_message(message, metadata=None)

        self.assertEqual(nle.event_id, 'MS250720c')

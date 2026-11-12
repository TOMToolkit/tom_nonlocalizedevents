"""Tests of event_summary.header: the selected alert's attribute rows and the science formatting."""
from django.test import TestCase

from tom_nonlocalizedevents.event_summary.header import (HeaderAttribute, format_area, format_distance,
                                                         format_inverse_far, format_percent, header_attributes,
                                                         most_likely_classification)
from tom_nonlocalizedevents.models import ExternalCoincidence, NonLocalizedEvent
from tom_nonlocalizedevents.tests.factories import (EventLocalizationFactory, EventSequenceFactory,
                                                    NonLocalizedEventFactory)
from tom_nonlocalizedevents.tests.test_alerts import BURST_ALERT, CBC_ALERT


def labels(attributes):
    return [attribute.label for attribute in attributes]


def by_label(attributes):
    return {attribute.label: attribute.value for attribute in attributes}


class TestFormatting(TestCase):
    def test_inverse_far(self):
        self.assertEqual(format_inverse_far(None), '')
        self.assertEqual(format_inverse_far(0), '')
        self.assertEqual(format_inverse_far(1e-6), '12 d')
        self.assertEqual(format_inverse_far(1e-8), '3.2 yr')
        self.assertEqual(format_inverse_far(1e-10), '317 yr')
        self.assertEqual(format_inverse_far(1e-12), '32 kyr')
        self.assertEqual(format_inverse_far(1e-40), '317 Qyr')
        self.assertEqual(format_inverse_far(1e-45), '3e+37 yr')

    def test_distance(self):
        self.assertEqual(format_distance(None), '')
        self.assertEqual(format_distance(EventLocalizationFactory.build(distance_mean=0)), '')
        self.assertEqual(format_distance(EventLocalizationFactory.build(distance_mean=142.4, distance_std=38.2)),
                         '142 ± 38 Mpc')
        self.assertEqual(format_distance(EventLocalizationFactory.build(distance_mean=8.26, distance_std=1.04)),
                         '8.3 ± 1.0 Mpc')
        self.assertEqual(format_distance(EventLocalizationFactory.build(distance_mean=1810., distance_std=520.)),
                         '1.8 ± 0.5 Gpc')

    def test_area_and_percent(self):
        self.assertEqual(format_area(None), '–')
        self.assertEqual(format_area(980.4), '980')
        self.assertEqual(format_area(12345.6), '12,346')
        self.assertEqual(format_percent(0.914), '91%')
        self.assertEqual(format_percent(None), '')
        self.assertEqual(format_percent('x'), '')

    def test_most_likely_classification_from_a_details_dictionary(self):
        self.assertEqual(most_likely_classification(None), '')
        self.assertEqual(most_likely_classification({'group': 'CBC', 'classification': {'BBH': 0.7, 'BNS': 0.3}}),
                         'BBH')
        self.assertEqual(most_likely_classification({'group': 'CBC', 'search': 'SSM'}), 'SSM')
        self.assertEqual(most_likely_classification({'group': 'Burst'}), 'Burst')


class TestHeaderAttributes(TestCase):
    def test_cbc_alert_with_localization(self):
        localization = EventLocalizationFactory.create(distance_mean=142.4, distance_std=38.2, area_50=210.2,
                                                       area_90=980.7)
        sequence = EventSequenceFactory.create(nonlocalizedevent=localization.nonlocalizedevent,
                                               localization=localization, details=CBC_ALERT)

        attributes = header_attributes(sequence)

        self.assertEqual(labels(attributes), ['Event time', '1 / FAR', 'Classification', 'Properties', 'Distance',
                                              'Area 50% / 90%', 'Instruments', 'Pipeline / search'])
        self.assertEqual(by_label(attributes)['Event time'], '2026-07-25 16:01:06 UTC')
        self.assertEqual(by_label(attributes)['Classification'], 'BNS 100%, Terrestrial 0%, BBH 0%, NSBH 0%')
        self.assertEqual(by_label(attributes)['Properties'], 'HasNS 1.00 · HasMassGap 0.00 · HasRemnant 1.00')
        self.assertEqual(by_label(attributes)['Distance'], '142 ± 38 Mpc')
        self.assertEqual(by_label(attributes)['Area 50% / 90%'], '210 / 981 deg²')
        self.assertEqual(by_label(attributes)['Instruments'], 'H1 L1')
        self.assertEqual(by_label(attributes)['Pipeline / search'], 'gstlal / AllSky')
        self.assertEqual(attributes[1], HeaderAttribute('1 / FAR', '348 kyr', 'inverse false-alarm rate'))

    def test_burst_alert_without_localization(self):
        sequence = EventSequenceFactory.create(details=BURST_ALERT)

        attributes = by_label(header_attributes(sequence))

        self.assertEqual(attributes['Duration'], '12 ms')
        self.assertEqual(attributes['Central frequency'], '180 Hz')
        self.assertNotIn('Classification', attributes)
        self.assertNotIn('Distance', attributes)

    def test_external_coincidence_row(self):
        coincidence = ExternalCoincidence.objects.create(details={
            'observatory': 'Fermi', 'search': 'GRB', 'gcn_notice_id': 524680123, 'time_difference': 1.7,
            'time_coincidence_far': 1e-11, 'time_sky_position_coincidence_far': 2e-12})
        sequence = EventSequenceFactory.create(details=CBC_ALERT, external_coincidence=coincidence)

        attributes = by_label(header_attributes(sequence))

        self.assertEqual(attributes['External coincidence'],
                         'Fermi, GRB, notice 524680123, Δt +1.7 s, joint 1 / FAR 16 kyr')

    def test_graceDB_backfill_and_empty_details(self):
        self.assertEqual(labels(header_attributes(EventSequenceFactory.create(details={'pipeline': 'gstlal'}))),
                         ['Pipeline / search'])
        self.assertEqual(header_attributes(EventSequenceFactory.create(details=None)), [])

    def test_generic_event_type_lists_scalar_entries(self):
        event = NonLocalizedEventFactory.create(event_type=NonLocalizedEvent.NonLocalizedEventType.NEUTRINO)
        sequence = EventSequenceFactory.create(nonlocalizedevent=event,
                                               details={'signalness': 0.42, 'energy_tev': 150, 'contours': [1, 2]})

        self.assertEqual(header_attributes(sequence), [HeaderAttribute('signalness', '0.42'),
                                                       HeaderAttribute('energy tev', '150')])

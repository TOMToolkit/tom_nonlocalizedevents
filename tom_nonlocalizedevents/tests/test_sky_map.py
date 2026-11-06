"""Tests of the sky-map layers and their delivery to the page's JavaScript."""
from datetime import datetime, timezone
import pathlib
import re

from django.urls import reverse

from astroplan import moon_illumination
from astropy.coordinates import get_body
from astropy.time import Time

import tom_nonlocalizedevents
from tom_nonlocalizedevents.event_summary.context import SKY_MAP_DATA_ID
from tom_nonlocalizedevents.event_summary.sky_map import (FOV_MAX_DEG, FOV_MIN_DEG, SKY_MAP_LEVELS, field_of_view,
                                                          sky_map_layers, sun_and_moon)
from tom_nonlocalizedevents.models import ExternalCoincidence
from tom_nonlocalizedevents.tests.factories import (EventCandidateFactory, EventLocalizationFactory,
                                                    EventSequenceFactory, TargetFactory, build_synthetic_localization,
                                                    healpix_pixel_area, healpix_pixel_count)
from tom_nonlocalizedevents.tests.test_event_summary import TAB_CLICK_HEADERS, EventPageTestCase

EPOCH = datetime(2026, 11, 1, 3, 12, 44, tzinfo=timezone.utc)
JS_PATH = pathlib.Path(tom_nonlocalizedevents.__file__).parent / 'static/tom_nonlocalizedevents/js/event_sky_map.js'


class TestSunAndMoon(EventPageTestCase):
    def test_positions_and_radii_at_a_fixed_epoch(self):
        sun, moon, at = sun_and_moon(EPOCH)

        self.assertEqual(at, EPOCH)
        self.assertAlmostEqual(sun.ra, float(get_body('sun', Time(EPOCH)).ra.deg), places=6)
        self.assertAlmostEqual(moon.dec, float(get_body('moon', Time(EPOCH)).dec.deg), places=6)
        self.assertEqual(sun.exclusion_radius_deg, 15.0)
        illumination = float(moon_illumination(Time(EPOCH)))
        self.assertAlmostEqual(moon.illumination, illumination)
        self.assertAlmostEqual(moon.exclusion_radius_deg, 3.0 + 42.0 * illumination)

    def test_defaults_to_now(self):
        _, _, at = sun_and_moon()

        self.assertIsNotNone(at.tzinfo)


class TestSkyMapLayers(EventPageTestCase):
    def test_layers_for_an_alert_with_a_localization(self):
        densities = [1.0] * healpix_pixel_count(3)
        densities[5] = 50.0
        localization = build_synthetic_localization(self.event, densities, area_90=1000.0)
        sequence = EventSequenceFactory.create(nonlocalizedevent=self.event, sequence_id=9, localization=localization)
        active = EventCandidateFactory.create(nonlocalizedevent=self.event,
                                              target=TargetFactory.create(name='a-active'))
        retired = EventCandidateFactory.create(nonlocalizedevent=self.event, viable=False,
                                               target=TargetFactory.create(name='b-retired'))

        layers = sky_map_layers(self.event, sequence, at=EPOCH)

        moc_url = reverse('nonlocalizedevents:localization-moc', args=(localization.pk,))
        self.assertEqual([(moc.url, moc.fill) for moc in layers.mocs],
                         [(f'{moc_url}?prob=0.9', False), (f'{moc_url}?prob=0.5', True)])
        self.assertEqual(layers.mocs[0].label, 'localization 90%')
        self.assertIsNotNone(layers.center_ra)
        self.assertEqual(layers.fov_deg, field_of_view(localization))
        self.assertEqual([(marker.name, marker.viable) for marker in layers.candidates],
                         [('a-active', True), ('b-retired', False)])
        self.assertEqual(layers.candidates[0].url, reverse('targets:detail', args=(active.target.pk,)))
        self.assertEqual(layers.candidates[1].ra, retired.target.ra)
        self.assertEqual(layers.at, EPOCH)

    def test_combined_localization_adds_one_more_outline(self):
        combined = EventLocalizationFactory.create(nonlocalizedevent=self.event)
        coincidence = ExternalCoincidence.objects.create(details={'observatory': 'Fermi'}, localization=combined)
        sequence = EventSequenceFactory.create(nonlocalizedevent=self.event, sequence_id=9,
                                               external_coincidence=coincidence,
                                               localization=self.sequences[0].localization)

        layers = sky_map_layers(self.event, sequence, at=EPOCH)

        self.assertEqual(len(layers.mocs), len(SKY_MAP_LEVELS) + 1)
        self.assertEqual(layers.mocs[-1].label, 'combined 90%')
        self.assertFalse(layers.mocs[-1].fill)
        self.assertIn(str(combined.pk), layers.mocs[-1].url)

    def test_without_a_localization_the_map_is_all_sky_with_no_regions(self):
        sequence = EventSequenceFactory.create(nonlocalizedevent=self.event, sequence_id=9)

        layers = sky_map_layers(self.event, sequence, at=EPOCH)

        self.assertEqual(layers.mocs, [])
        self.assertIsNone(layers.center_ra)
        self.assertEqual(layers.fov_deg, FOV_MAX_DEG)
        self.assertEqual(sky_map_layers(self.event, None, at=EPOCH).mocs, [])

    def test_field_of_view_follows_the_90_percent_area_within_bounds(self):
        self.assertEqual(field_of_view(None), FOV_MAX_DEG)
        self.assertEqual(field_of_view(EventLocalizationFactory.build(area_90=None)), FOV_MAX_DEG)
        self.assertEqual(field_of_view(EventLocalizationFactory.build(area_90=1.0)), FOV_MIN_DEG)
        self.assertEqual(field_of_view(EventLocalizationFactory.build(area_90=30000.0)), FOV_MAX_DEG)
        self.assertAlmostEqual(field_of_view(EventLocalizationFactory.build(area_90=1000.0)), 3.0 * 2 * 17.841,
                               places=2)

    def test_layers_serialize_to_json(self):
        localization = build_synthetic_localization(self.event, [1 / healpix_pixel_area(3)] * healpix_pixel_count(3))
        sequence = EventSequenceFactory.create(nonlocalizedevent=self.event, sequence_id=9, localization=localization)

        data = sky_map_layers(self.event, sequence, at=EPOCH).model_dump(mode='json')

        self.assertEqual(data['at'], '2026-11-01T03:12:44Z')
        self.assertEqual(set(data), {'center_ra', 'center_dec', 'fov_deg', 'mocs', 'candidates', 'sun', 'moon', 'at'})


class TestSkyMapOnThePage(EventPageTestCase):
    def test_page_carries_the_viewer_and_the_data_element_without_inline_scripts(self):
        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertIn('id="event-sky-map"', content)
        self.assertIn('id="event-sky-map-viewer"', content)
        self.assertIn(f'<script id="{SKY_MAP_DATA_ID}" type="application/json">', content)
        # on the page itself the data element is not an out-of-band swap (a history restore swaps the body)
        self.assertIn('<div id="event-sky-map-data-holder">', content)
        self.assertIn('js/event_sky_map.js', content)
        for script in re.findall(r'<script[^>]*>', content):
            self.assertTrue('src=' in script or 'application/json' in script, script)

    def test_tab_click_replaces_the_data_element_out_of_band(self):
        response = self.client.get(self.url, {'sequence': '1'}, **TAB_CLICK_HEADERS)
        content = response.content.decode()

        self.assertIn('<div id="event-sky-map-data-holder" hx-swap-oob="true">', content)
        self.assertIn(f'<script id="{SKY_MAP_DATA_ID}" type="application/json">', content)
        self.assertNotIn('id="event-sky-map-viewer"', content)

    def test_javascript_reads_the_ids_the_templates_write(self):
        source = JS_PATH.read_text()

        self.assertIn(f"const DATA_ID = '{SKY_MAP_DATA_ID}';", source)
        self.assertIn("const CONTAINER_ID = 'event-sky-map';", source)
        self.assertIn("const DATA_SLOT_ID = 'event-sky-map-data-slot';", source)
        self.assertIn("const VIEWER_ID = 'event-sky-map-viewer';", source)
        self.assertIn('/3.8.2/aladin.js', source)

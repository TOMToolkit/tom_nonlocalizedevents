"""Tests of healpix_utils' deprecated SQLAlchemy names, its credible-region wrappers and its import structure."""
import ast
import importlib
import pathlib
import warnings

from django.test import TestCase, override_settings

from astropy_healpix import healpix_to_lonlat, level_to_nside

from tom_targets.models import Target

from tom_nonlocalizedevents import healpix_utils
from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.services import credible_regions
from tom_nonlocalizedevents.tests.factories import (EventCandidateFactory, NonLocalizedEventFactory, TargetFactory,
                                                    build_synthetic_localization, healpix_pixel_area,
                                                    healpix_pixel_count)

DEPRECATED_NAMES = ['sa_engine', 'Base', 'SaSkymap', 'SaSkymapTile', 'SaTarget', 'SaEventCandidate']


class TestDeprecatedSqlalchemyNames(TestCase):
    def test_each_name_is_importable_and_warns(self):
        for name in DEPRECATED_NAMES:
            with self.subTest(name=name), self.assertWarnsRegex(DeprecationWarning, f'{name} is deprecated'):
                self.assertIsNotNone(getattr(healpix_utils, name))

    def test_from_import_warns_too(self):
        with self.assertWarns(DeprecationWarning):
            from tom_nonlocalizedevents.healpix_utils import SaSkymapTile  # noqa: F401

    def test_importing_the_module_does_not_warn_or_build_the_engine(self):
        """Only a TOM that uses the names pays for the engine; the plugin itself must not trigger it."""
        healpix_utils._sqlalchemy_legacy.cache_clear()
        with warnings.catch_warnings():
            warnings.simplefilter('error', DeprecationWarning)
            importlib.reload(healpix_utils)
            healpix_utils.update_all_credible_region_percents_for_candidates  # a current name: no warning
        self.assertEqual(healpix_utils._sqlalchemy_legacy.cache_info().currsize, 0)

    def test_the_warning_is_logged_once_per_name(self):
        healpix_utils._deprecation_logged.discard('SaTarget')
        with self.assertLogs('tom_nonlocalizedevents.healpix_utils', level='WARNING') as logged:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                healpix_utils.SaTarget
                healpix_utils.SaTarget
        self.assertEqual(len(logged.records), 1)
        self.assertIn('SaTarget is deprecated', logged.output[0])

    def test_unknown_names_still_raise_attribute_error(self):
        with self.assertRaises(AttributeError):
            healpix_utils.no_such_name

    def test_the_mapped_classes_are_built_once_and_name_the_django_tables(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            first, second = healpix_utils.SaSkymapTile, healpix_utils.SaSkymapTile
            self.assertIs(first, second)
            # SaTarget's table is the model that owns the distance fields: TOM Toolkit 3 split targets into
            # tom_targets_basetarget, which broke the hardcoded 'tom_targets_target' silently
            self.assertEqual(healpix_utils.SaTarget.__tablename__,
                             Target._meta.get_field('distance').model._meta.db_table)
            self.assertEqual(healpix_utils.SaTarget.__tablename__, 'tom_targets_basetarget')
            self.assertEqual(healpix_utils.SaSkymap.__tablename__, EventLocalization._meta.db_table)
            self.assertEqual(healpix_utils.SaSkymapTile.__tablename__, SkymapTile._meta.db_table)
            self.assertEqual(healpix_utils.SaEventCandidate.__tablename__, EventCandidate._meta.db_table)
            self.assertEqual(str(healpix_utils.sa_engine.url.drivername), 'postgresql')


class TestCredibleRegionWrappers(TestCase):
    """The 1.0.0 entry points stay importable from healpix_utils and compute through the service."""

    @classmethod
    def setUpTestData(cls):
        cls.event = NonLocalizedEventFactory.create()
        # pixel 0 holds 0.6 of the probability, the rest is spread evenly
        count = healpix_pixel_count(3)
        probabilities = [0.6] + [0.4 / (count - 1)] * (count - 1)
        cls.localization = build_synthetic_localization(cls.event, [p / healpix_pixel_area(3) for p in probabilities])
        cls.inside = cls.candidate_at_pixel(0)
        cls.outside = cls.candidate_at_pixel(100)

    @classmethod
    def candidate_at_pixel(cls, ipix):
        longitude, latitude = healpix_to_lonlat(ipix, level_to_nside(3), order='nested')
        target = TargetFactory.create(ra=float(longitude.deg), dec=float(latitude.deg))
        return EventCandidateFactory.create(target=target, nonlocalizedevent=cls.event)

    def smallest_percent(self, candidate):
        return (CredibleRegion.objects.filter(candidate=candidate, localization=self.localization)
                .values_list('smallest_percent', flat=True).first())

    def test_update_all_records_the_smallest_level_through_the_service(self):
        healpix_utils.update_all_credible_region_percents_for_candidates(self.localization)

        self.assertEqual(self.smallest_percent(self.inside), 25)
        self.assertIsNone(self.smallest_percent(self.outside))

    def test_update_all_accepts_candidate_ids(self):
        healpix_utils.update_all_credible_region_percents_for_candidates(self.localization, [self.outside.pk])

        self.assertFalse(CredibleRegion.objects.filter(localization=self.localization).exists())

    def test_update_one_level_records_that_level_only(self):
        healpix_utils.update_credible_region_percent_for_candidates(self.localization, 0.9)

        self.assertEqual(self.smallest_percent(self.inside), 90)
        self.assertIsNone(self.smallest_percent(self.outside))

    def test_credible_region_probabilities_follow_the_settings(self):
        with override_settings(CREDIBLE_REGION_PROBABILITIES=[0.3, 0.7]):
            self.assertEqual(healpix_utils.CREDIBLE_REGION_PROBABILITIES, [0.7, 0.3])
            self.assertEqual(healpix_utils.CREDIBLE_REGION_PROBABILITIES,
                             credible_regions.credible_region_probabilities())


class TestImportStructure(TestCase):
    """The services package imports healpix_utils (event_ingest uses create_localization_for_skymap), so
    healpix_utils may import the credible-region service only inside function bodies, and the service may
    not import healpix_utils at all; otherwise importing either first fails."""

    @staticmethod
    def module_level_imports(module):
        tree = ast.parse(pathlib.Path(module.__file__).read_text())
        names = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or '')
        return names

    def test_healpix_utils_does_not_import_the_services_package_at_module_level(self):
        for name in self.module_level_imports(healpix_utils):
            self.assertNotIn('services', name)

    def test_the_service_never_imports_healpix_utils(self):
        source = pathlib.Path(credible_regions.__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn('healpix_utils', node.module or '')
            elif isinstance(node, ast.Import):
                self.assertFalse(any('healpix_utils' in alias.name for alias in node.names))

    def test_the_signal_handlers_import_the_service(self):
        from tom_nonlocalizedevents.signals import handlers

        self.assertIs(handlers.update_all_credible_region_percents_for_candidates,
                      credible_regions.update_all_credible_region_percents_for_candidates)

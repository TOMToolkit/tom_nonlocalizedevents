"""Tests of the candidate service (find a target, add and remove a candidate) and of the candidate management
command (add and remove).
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from tom_targets.models import Target, TargetName

from tom_nonlocalizedevents.models import CredibleRegion, EventCandidate
from tom_nonlocalizedevents.services.candidates import add_candidate, find_target, remove_candidate
from tom_nonlocalizedevents.tests.factories import NonLocalizedEventFactory, TargetFactory, build_synthetic_localization
from tom_nonlocalizedevents.tests.test_signals import densities_with_peak_at_pixel_0, target_at_pixel


class TestFindTarget(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.target = TargetFactory.create(name='AT2026abc')
        TargetName.objects.create(target=cls.target, name='ZTF26aaaaaaa')

    def test_by_name_alias_and_primary_key(self):
        self.assertEqual(find_target('AT2026abc'), self.target)
        self.assertEqual(find_target('ZTF26aaaaaaa'), self.target)
        self.assertEqual(find_target(str(self.target.pk)), self.target)

    def test_a_numeric_name_wins_over_a_primary_key(self):
        numeric_name = TargetFactory.create(name=str(self.target.pk))

        self.assertEqual(find_target(str(self.target.pk)), numeric_name)

    def test_names_are_matched_as_the_tom_matches_them(self):
        # the default TargetMatchManager ignores case, spaces, dashes, underscores and parentheses
        self.assertEqual(find_target('at2026abc'), self.target)
        self.assertEqual(find_target('AT 2026-abc'), self.target)
        self.assertEqual(find_target('ztf26AAAAAAA'), self.target)

    def test_no_match(self):
        with self.assertRaisesMessage(Target.DoesNotExist,
                                      'No target named no-such-target, and no target with that primary key.'):
            find_target('no-such-target')
        with self.assertRaises(Target.DoesNotExist):
            find_target('999999')

    def test_several_matches_are_reported_by_name(self):
        TargetFactory.create(name='M101')
        TargetFactory.create(name='m 101')

        with self.assertRaisesMessage(Target.MultipleObjectsReturned, 'Several targets match m101: M101, m 101.'):
            find_target('m101')


class TestAddCandidate(TestCase):
    def setUp(self):
        self.event = NonLocalizedEventFactory.create(event_id='S260901cc')
        self.localization = build_synthetic_localization(self.event, densities_with_peak_at_pixel_0(0.6))

    def test_creates_the_candidate_with_its_healpix_index_and_credible_regions(self):
        target = target_at_pixel(0)

        candidate, created = add_candidate(self.event, target, priority=3)

        self.assertTrue(created)
        self.assertEqual((candidate.nonlocalizedevent, candidate.target, candidate.priority), (self.event, target, 3))
        self.assertNotEqual(candidate.healpix, -1)
        # the post_save handler scored the candidate against the event's localization: at the peak, the 25% region
        region = CredibleRegion.objects.get(candidate=candidate, localization=self.localization)
        self.assertEqual(region.smallest_percent, 25)

    def test_adding_the_same_target_again_returns_the_existing_candidate_unchanged(self):
        target = target_at_pixel(0)
        first, _ = add_candidate(self.event, target, priority=3)
        first.viable = False
        first.save()

        second, created = add_candidate(self.event, target, priority=1)

        self.assertFalse(created)
        self.assertEqual(second, first)
        self.assertEqual((second.priority, second.viable), (3, False))
        self.assertEqual(EventCandidate.objects.filter(nonlocalizedevent=self.event).count(), 1)


class TestRemoveCandidate(TestCase):
    def test_deletes_the_candidate_and_its_credible_regions_but_not_the_target(self):
        event = NonLocalizedEventFactory.create(event_id='S260901ff')
        build_synthetic_localization(event, densities_with_peak_at_pixel_0(0.6))
        target = target_at_pixel(0)
        candidate, _ = add_candidate(event, target)
        self.assertTrue(CredibleRegion.objects.filter(candidate=candidate).exists())

        remove_candidate(candidate)

        self.assertFalse(EventCandidate.objects.filter(nonlocalizedevent=event, target=target).exists())
        self.assertFalse(CredibleRegion.objects.filter(candidate_id=candidate.pk).exists())
        self.assertTrue(Target.objects.filter(pk=target.pk).exists())


class TestCandidateCommandAdd(TestCase):
    def setUp(self):
        self.event = NonLocalizedEventFactory.create(event_id='S260901dd')
        self.localization = build_synthetic_localization(self.event, densities_with_peak_at_pixel_0(0.6))
        self.target = target_at_pixel(0)
        self.target.name = 'AT2026peak'
        self.target.save()

    def run_command(self, *arguments):
        out = StringIO()
        call_command('candidate', 'add', *arguments, stdout=out)
        return out.getvalue()

    def test_creates_the_candidate_and_reports_its_credible_regions(self):
        output = self.run_command('S260901dd', 'AT2026peak', '--priority', '2')

        candidate = EventCandidate.objects.get(nonlocalizedevent=self.event, target=self.target)
        self.assertEqual(candidate.priority, 2)
        self.assertIn(f'Created candidate {candidate.pk}: target AT2026peak for event S260901dd', output)
        self.assertIn(f'localization {self.localization.pk} ({self.localization.date}): smallest credible region 25%',
                      output)

    def test_a_target_far_from_the_localization_is_outside_every_region(self):
        far_away = target_at_pixel(100)

        output = self.run_command('S260901dd', str(far_away.pk))

        self.assertIn('outside every recorded credible region', output)

    def test_a_second_run_changes_nothing(self):
        self.run_command('S260901dd', 'AT2026peak', '--priority', '2')

        output = self.run_command('S260901dd', 'AT2026peak', '--priority', '5')

        self.assertIn('is already candidate', output)
        self.assertEqual(EventCandidate.objects.get(nonlocalizedevent=self.event, target=self.target).priority, 2)

    def test_an_event_without_localization_says_so(self):
        NonLocalizedEventFactory.create(event_id='S260901ee')

        output = self.run_command('S260901ee', 'AT2026peak')

        self.assertIn('The event has no localization yet', output)

    def test_unknown_event_and_unknown_target_are_command_errors(self):
        with self.assertRaisesRegex(CommandError, "No event with id 'S000000zz'"):
            call_command('candidate', 'add', 'S000000zz', 'AT2026peak')
        with self.assertRaisesRegex(CommandError, 'No target named nobody'):
            call_command('candidate', 'add', 'S260901dd', 'nobody')

    def test_a_name_matching_several_targets_is_a_command_error(self):
        TargetFactory.create(name='AT2026-peak')

        with self.assertRaisesRegex(CommandError, 'Several targets match at2026peak: AT2026-peak, AT2026peak'):
            call_command('candidate', 'add', 'S260901dd', 'at2026peak')


class TestCandidateCommandRemove(TestCase):
    def setUp(self):
        self.event = NonLocalizedEventFactory.create(event_id='S260901gg')
        self.target = TargetFactory.create(name='AT2026gone')
        self.candidate, _ = add_candidate(self.event, self.target)

    def test_removes_the_candidate_and_keeps_the_target(self):
        out = StringIO()

        call_command('candidate', 'remove', 'S260901gg', 'at2026gone', stdout=out)

        self.assertIn(f'Removed candidate {self.candidate.pk}: target AT2026gone is no longer a candidate of event '
                      'S260901gg', out.getvalue())
        self.assertFalse(EventCandidate.objects.filter(pk=self.candidate.pk).exists())
        self.assertTrue(Target.objects.filter(pk=self.target.pk).exists())

    def test_a_target_that_is_not_a_candidate_is_a_command_error(self):
        TargetFactory.create(name='AT2026never')

        with self.assertRaisesRegex(CommandError, 'Target AT2026never is not a candidate of event S260901gg'):
            call_command('candidate', 'remove', 'S260901gg', 'AT2026never')
        self.assertTrue(EventCandidate.objects.filter(pk=self.candidate.pk).exists())

    def test_unknown_event_and_unknown_target_are_command_errors(self):
        with self.assertRaisesRegex(CommandError, "No event with id 'S000000zz'"):
            call_command('candidate', 'remove', 'S000000zz', 'AT2026gone')
        with self.assertRaisesRegex(CommandError, 'No target named nobody'):
            call_command('candidate', 'remove', 'S260901gg', 'nobody')

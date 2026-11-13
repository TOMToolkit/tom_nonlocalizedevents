"""Tests of the EventCandidate API that cover the serializer repairs made with the MOC endpoint."""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from tom_nonlocalizedevents.models import EventCandidate
from tom_nonlocalizedevents.serializers import CredibleRegionSerializer
from tom_nonlocalizedevents.tests.factories import (CredibleRegionFactory, EventCandidateFactory,
                                                    EventLocalizationFactory, NonLocalizedEventFactory, TargetFactory)


class TestEventCandidateViewSet(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username='test_user')
        self.client.force_login(self.user)
        self.event = NonLocalizedEventFactory.create(event_id='S260901aa')
        self.localization = EventLocalizationFactory.create(nonlocalizedevent=self.event)
        self.candidate = EventCandidateFactory.create(nonlocalizedevent=self.event)
        self.credible_region = CredibleRegionFactory.create(localization=self.localization,
                                                            candidate=self.candidate, smallest_percent=90)

    def test_list_reports_credible_regions_by_localization(self):
        response = self.client.get(reverse('api:eventcandidate-list'))

        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['credible_regions'], {str(self.localization.pk): 90})

    def test_patch_without_an_updatable_field_is_a_validation_error(self):
        """Regression: the error message joined the key list with list.join and raised AttributeError instead."""
        response = self.client.patch(reverse('api:eventcandidate-detail', args=(self.candidate.pk,)), {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('viable, viability_reason, priority', str(response.json()))

    def test_patch_updates_viability(self):
        response = self.client.patch(reverse('api:eventcandidate-detail', args=(self.candidate.pk,)),
                                     {'viable': False, 'viability_reason': 'host galaxy too distant'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertFalse(self.candidate.viable)
        self.assertEqual(self.candidate.viability_reason, 'host galaxy too distant')

    def test_credible_region_serializer_fields(self):
        """Regression: the field list held an empty name, which fails on first instantiation."""
        self.assertEqual(CredibleRegionSerializer(self.credible_region).data,
                         {'smallest_percent': 90, 'localization': self.localization.pk})


class TestEventCandidateCreation(APITestCase):
    """POST creates through the candidate service, so the API, the command and the workflow step share one path."""

    def setUp(self):
        self.user = User.objects.create(username='test_user')
        self.client.force_login(self.user)
        self.event = NonLocalizedEventFactory.create(event_id='S260901aa')
        self.target = TargetFactory.create(name='AT2026api')
        self.url = reverse('api:eventcandidate-list')

    def test_post_with_a_target_primary_key(self):
        response = self.client.post(self.url, {'nonlocalizedevent': self.event.pk, 'target': self.target.pk,
                                               'priority': 2}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        candidate = EventCandidate.objects.get(nonlocalizedevent=self.event, target=self.target)
        self.assertEqual(candidate.priority, 2)
        self.assertNotEqual(candidate.healpix, -1)

    def test_a_repeated_post_is_a_validation_error_not_a_second_row(self):
        self.client.post(self.url, {'nonlocalizedevent': self.event.pk, 'target': self.target.pk}, format='json')

        response = self.client.post(self.url, {'nonlocalizedevent': self.event.pk, 'target': self.target.pk},
                                    format='json')

        self.assertEqual(response.status_code, 400)
        # DRF's validator for the model's unique constraint answers this one; the serializer's own check covers the
        # target_fields form of the request, which that validator cannot see
        self.assertIn('unique set', str(response.json()))
        self.assertEqual(EventCandidate.objects.filter(nonlocalizedevent=self.event).count(), 1)

    def test_post_with_target_fields_naming_an_existing_target(self):
        response = self.client.post(self.url, {'nonlocalizedevent': self.event.pk,
                                               'target_fields': {'name': 'AT2026api'}}, format='json')

        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(EventCandidate.objects.filter(nonlocalizedevent=self.event, target=self.target).exists())

    def test_post_without_a_target_is_a_validation_error(self):
        response = self.client.post(self.url, {'nonlocalizedevent': self.event.pk}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('target', str(response.json()))

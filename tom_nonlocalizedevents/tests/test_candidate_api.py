"""Tests of the EventCandidate API that cover the serializer repairs made with the MOC endpoint."""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from tom_nonlocalizedevents.serializers import CredibleRegionSerializer
from tom_nonlocalizedevents.tests.factories import (CredibleRegionFactory, EventCandidateFactory,
                                                    EventLocalizationFactory, NonLocalizedEventFactory)


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

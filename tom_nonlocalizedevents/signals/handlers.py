"""post_save handlers that keep CredibleRegion rows current as candidates and localizations arrive."""
from functools import partial
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from tom_nonlocalizedevents.models import EventCandidate, EventLocalization
from tom_nonlocalizedevents.services.credible_regions import update_all_credible_region_percents_for_candidates

logger = logging.getLogger(__name__)


@receiver(post_save, sender=EventCandidate)
def cb_post_save_event_candidate(sender, instance, created, **kwargs):
    # Anytime an EventCandidate is created, create and save its probability for different levels of credible region
    # for each localization we have for that event
    if created:
        logger.info(f"Just created and saved new Event Candidate with healpix {instance.healpix}")
        localizations = instance.nonlocalizedevent.localizations.all()
        for localization in localizations:
            update_all_credible_region_percents_for_candidates(localization, [instance.pk])


@receiver(post_save, sender=EventLocalization)
def cb_post_save_event_localization(sender, instance, created, **kwargs):
    # Anytime a new EventLocalization is created, update the smallest probability credible region for each
    # EventCandidate associated with that nonlocalizedevent for the new localization
    if created:
        transaction.on_commit(
            partial(update_all_credible_region_percents_for_candidates, eventlocalization=instance)
        )

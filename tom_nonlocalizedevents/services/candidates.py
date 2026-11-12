"""Candidates: the targets a TOM follows up for a non-localized event, stored as EventCandidate rows.

This module is the one place that adds or removes a candidate; the management command ``candidate`` (add and
remove), the REST endpoint and the Candidates workflow step all call it, so the rules live here. Creating the row
is all that is needed:
``EventCandidate.save()`` stores the target's HEALPix index, and the ``post_save`` handler in ``signals.handlers``
records the candidate's smallest credible region for each localization the event has.
"""
from __future__ import annotations

from tom_targets.models import Target

from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent


def find_target(name_or_pk: str) -> Target:
    """Return the one target that ``name_or_pk`` names or, when that is an integer no target is named by, the
    target with that primary key.

    Names are matched the way the TOM matches them: ``Target.matches.match_name()``, the manager a TOM
    replaces through the ``MATCH_MANAGERS`` setting. The default compares names and aliases with the case,
    spaces, dashes, underscores and parentheses removed, so ``m101`` finds ``M101``. The default match reads
    every target once per call; fine for a form or a command, not for a loop.

    Raises:
        Target.DoesNotExist: When no target matches.
        Target.MultipleObjectsReturned: When several targets match the name; the message lists their names.
    """
    matches = list(Target.matches.match_name(name_or_pk))
    if not matches and name_or_pk.isdigit():
        matches = list(Target.objects.filter(pk=int(name_or_pk)))
    if not matches:
        raise Target.DoesNotExist(f'No target named {name_or_pk}, and no target with that primary key.')
    if len(matches) > 1:
        names = ', '.join(sorted(target.name for target in matches))
        raise Target.MultipleObjectsReturned(f'Several targets match {name_or_pk}: {names}.')
    return matches[0]


def add_candidate(event: NonLocalizedEvent, target: Target, priority: int = 1) -> tuple[EventCandidate, bool]:
    """Make ``target`` a candidate of ``event``, or return the existing candidate unchanged.

    Returns:
        The candidate and whether this call created it. An existing candidate keeps its priority, viability and
        reason: adding a target a second time is not a way to edit it.
    """
    return EventCandidate.objects.get_or_create(nonlocalizedevent=event, target=target,
                                                defaults={'priority': priority})


def remove_candidate(candidate: EventCandidate) -> None:
    """Delete ``candidate`` and its CredibleRegion rows. The target stays.

    Removing differs from retiring: a retired candidate stays in the table under the Retired filter with its
    reason; a removed one leaves no record that the target was ever considered for the event.
    """
    candidate.delete()

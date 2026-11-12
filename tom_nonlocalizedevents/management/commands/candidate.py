from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from tom_targets.models import Target

from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent
from tom_nonlocalizedevents.services.candidates import add_candidate, find_target, remove_candidate


class Command(BaseCommand):
    """Add a target to the candidates of a non-localized event, or remove one from them."""
    help = ('Adds a target to the candidates of a non-localized event ("candidate add S230518h AT2023abc") or '
            'removes one from them ("candidate remove S230518h AT2023abc"). Adding reports the credible regions the '
            'candidate falls in; removing deletes the candidate and its credible regions and keeps the target. To '
            'keep a candidate on record, retire it on the Candidates page instead of removing it.')

    def add_arguments(self, parser: CommandParser) -> None:
        actions = parser.add_subparsers(dest='action', required=True, title='actions')
        add = actions.add_parser('add', help='Make the target a candidate of the event.')
        remove = actions.add_parser('remove', help='Remove the target from the candidates of the event.')
        for action in (add, remove):
            action.add_argument('event_id', type=str, help='The event id, for example S230518h.')
            action.add_argument('target', type=str,
                                help="The target's name, one of its aliases (matched as the TOM matches names), or "
                                     "its primary key.")
        add.add_argument('--priority', type=int, default=1,
                         help='The priority of the new candidate (default 1); an existing candidate keeps its own.')

    def handle(self, *args: Any, **options: Any) -> None:
        event_id: str = options['event_id']
        try:
            event = NonLocalizedEvent.objects.get(event_id=event_id)
        except NonLocalizedEvent.DoesNotExist:
            raise CommandError(f'No event with id {event_id!r}')
        try:
            target = find_target(options['target'])
        except (Target.DoesNotExist, Target.MultipleObjectsReturned) as error:
            raise CommandError(str(error))

        if options['action'] == 'add':
            self.add(event, target, options['priority'])
        else:
            self.remove(event, target)

    def add(self, event: NonLocalizedEvent, target: Target, priority: int) -> None:
        candidate, created = add_candidate(event, target, priority=priority)
        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Created candidate {candidate.pk}: target {target.name} for event {event.event_id}'))
        else:
            self.stdout.write(self.style.WARNING(
                f'Target {target.name} is already candidate {candidate.pk} of event {event.event_id}; nothing changed'))

        # the credible regions the post_save handler recorded for the candidate, one per localization of the event
        localizations = list(event.localizations.order_by('date'))
        if not localizations:
            self.stdout.write('The event has no localization yet; the credible regions are computed when one arrives.')
        smallest_percent_by_localization = {
            region.localization_id: region.smallest_percent for region in candidate.credibleregions.all()}
        for localization in localizations:
            percent = smallest_percent_by_localization.get(localization.pk)
            if percent is not None:
                where = f'smallest credible region {percent}%'
            else:
                where = 'outside every recorded credible region'
            self.stdout.write(f'  localization {localization.pk} ({localization.date}): {where}')

    def remove(self, event: NonLocalizedEvent, target: Target) -> None:
        try:
            candidate = EventCandidate.objects.get(nonlocalizedevent=event, target=target)
        except EventCandidate.DoesNotExist:
            raise CommandError(f'Target {target.name} is not a candidate of event {event.event_id}')
        candidate_pk = candidate.pk
        remove_candidate(candidate)
        self.stdout.write(self.style.SUCCESS(f'Removed candidate {candidate_pk}: target {target.name} is no longer a '
                                             f'candidate of event {event.event_id}'))

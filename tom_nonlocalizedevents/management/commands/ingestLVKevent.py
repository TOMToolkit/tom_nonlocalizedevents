import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from tom_nonlocalizedevents.services.gracedb import ingest_event_from_gracedb

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class Command(BaseCommand):
    """
    A Django management command to ingest a non-localized event from an external
    source (e.g., GraceDB) given its unique event identifier.
    """
    help = 'Ingests a non-localized event from an external source, e.g., GraceDB, given its EVENT_ID.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            'event_id',
            type=str,
            help='The unique identifier for the event to be ingested (e.g., S230518h).'
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """
        The main logic of the management command.

        This method orchestrates the fetching of data from GraceDB and calling
        the ingestion service for each available skymap.
        """
        event_id: str = options['event_id']
        self.stdout.write(self.style.SUCCESS(f'Starting ingestion process for event: {event_id}'))

        try:
            success_count, errors = ingest_event_from_gracedb(event_id)

            # Report successes first, as this is the primary expected outcome.
            if success_count > 0:
                self.stdout.write(self.style.SUCCESS(f"Successfully ingested {success_count} new sequence(s)."))
            elif not errors:
                # Only report "no new sequences" if there were also no errors.
                self.stdout.write(self.style.WARNING("No new sequences were ingested. The event may already be up to date."))

            # Always report any errors that occurred, regardless of successes.
            if errors:
                self.stderr.write(self.style.ERROR("Encountered the following errors:"))
                for error in errors:
                    self.stderr.write(f"  - {error}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"A critical error occurred: {e}"))
            logger.exception(f"Critical failure during ingestion for event {event_id}")

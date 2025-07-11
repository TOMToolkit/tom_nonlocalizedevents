import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class Command(BaseCommand):
    """
    Management command to ingest a non-localized event from an external
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
        event_id: str = options['event_id']
        self.stdout.write(self.style.SUCCESS(f'Starting ingestion process for event: {event_id}'))
        try:
            logger.info(f'Received request to ingest LVK event: {event_id}')
            # TODO: Implement logic to fetch event data from GraceDB using the event_id.
            # This will likely involve a new function, e.g., `gracedb_client.fetch_event(event_id)`.
            # The fetched data will then be transformed and passed to a service function.
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nIngestion process cancelled by user.'))
            logger.warning(f'KeyboardInterrupt received during ingestion for event: {event_id}')

        self.stdout.write(self.style.SUCCESS(f'Finished ingestion process for event: {event_id}'))

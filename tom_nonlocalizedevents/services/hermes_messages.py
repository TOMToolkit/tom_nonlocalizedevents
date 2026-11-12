"""The messages in the Hermes archive that mention a non-localized event: GCN circulars, the IGWN alerts, and the
messages astronomers post on Hermes's topics.

Hermes (https://hermes.lco.global) archives the messages of every topic it follows, and its public query endpoint
searches the archive's text across all of them: ``GET {HERMES_API_URL}/api/v0/query/?search_query=<event id>
&start=<ISO 8601 time>&limit=<n>``. The response is ``{"messages": [...], "next": ..., "prev": ...}``, newest message
first, where each message is ``{"metadata": {"topic", "timestamp"}, "annotations": {"con_text_uuid", "title",
"originator", "sender", "retracted", ...}}`` and ``timestamp`` is epoch milliseconds. An IGWN alert has no title; its
uuid is the ``hermes_message_id`` the plugin stores on the alert's EventSequence, which is how the card names it.
The same GCN circular can appear more than once, with different uuids and timestamps days apart (S250328ae on
2026-11-11: two circulars twice each, both copies from the same sender); why is not known. Titled messages of a
topic are therefore collapsed onto the earliest copy of their title.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.core.cache import cache

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
import requests

from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_nonlocalizedevents.services.alerts import parse_alert

logger = logging.getLogger(__name__)

# The query is not restricted to a topic: astronomers and TOMs post about an event on whichever topic they write
# to, and a search for an event id across every topic returned nothing but circulars and alerts for the events
# checked on 2026-11-11. The IGWN alerts (topic igwn.gwalert) repeat the event header's alert tabs, and readers
# expect to see them in the list all the same. The card shows each message's topic as Hermes names it.
HERMES_MESSAGES_LIMIT = 100  # archive messages asked for per event; the list shows at most this many
HERMES_MESSAGES_CACHE_SECONDS = 600  # a new message reaches the page within ten minutes
HERMES_TIMEOUT_SECONDS = 10


class HermesMessage(BaseModel):
    """One message as the Hermes archive lists it."""
    model_config = ConfigDict(extra='ignore')

    uuid: str
    topic: str
    title: str = ''
    originator: str = ''
    timestamp: datetime
    retracted: bool = False

    @field_validator('timestamp', mode='before')
    @classmethod
    def epoch_milliseconds_to_datetime(cls, value: Any) -> Any:
        """The archive gives the timestamp as epoch milliseconds."""
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return value

    @property
    def url(self) -> str:
        """The URL of the message's page on Hermes."""
        return urljoin(settings.HERMES_API_URL, f'/message/{self.uuid}')

    @classmethod
    def from_archive_message(cls, message: dict[str, Any]) -> HermesMessage:
        """Build a message from one entry of the archive's ``messages`` list.

        Raises:
            KeyError: When the entry lacks ``annotations``, ``metadata``, the topic or the uuid.
            ValidationError: When a field has a value the model cannot take.
        """
        annotations = message['annotations']
        return cls(uuid=annotations['con_text_uuid'], topic=message['metadata']['topic'],
                   title=annotations.get('title') or '', originator=annotations.get('originator') or '',
                   timestamp=message['metadata']['timestamp'], retracted=annotations.get('retracted', False))


def one_copy_per_title(messages: list[HermesMessage]) -> list[HermesMessage]:
    """Keep the earliest copy of each titled message of a topic.

    When Hermes receives the same message on different topics, just keep the first one.
    Messages without a title (the IGWN alerts) are all kept.
    """
    earliest: dict[tuple[str, str], HermesMessage] = {}
    kept: list[HermesMessage] = []
    for message in messages:
        if not message.title:
            kept.append(message)
            continue
        key = (message.topic, message.title)
        if key not in earliest or message.timestamp < earliest[key].timestamp:
            earliest[key] = message
    return kept + list(earliest.values())


def event_start_time(event: NonLocalizedEvent) -> datetime | None:
    """Return the time of the event's earliest alert, or None when no alert carries a time.

    This is used to limit HERMES archive queries.
    """
    times = [getattr(parse_alert(sequence), 'time', None) for sequence in event.sequences.all()]
    known_times = [time for time in times if time is not None]
    return min(known_times) if known_times else None


def hermes_messages_for_event(event: NonLocalizedEvent,
                              limit: int = HERMES_MESSAGES_LIMIT) -> tuple[list[HermesMessage], str | None]:
    """Return the archive messages that mention ``event``, newest first, and an error message or None.
    """
    cache_key = f'nonlocalizedevents:hermes_messages:{event.event_id}:{limit}'
    messages = cache.get(cache_key)
    if messages is not None:
        return messages, None

    query_url = urljoin(settings.HERMES_API_URL, '/api/v0/query/')
    query: dict[str, Any] = {'search_query': event.event_id, 'limit': limit}
    start_time = event_start_time(event)
    if start_time is not None:
        query['start'] = start_time.isoformat()  # Hermes parses the date and passes it to the archive
    try:
        response = requests.get(query_url, params=query, timeout=HERMES_TIMEOUT_SECONDS)
        response.raise_for_status()
        messages = [HermesMessage.from_archive_message(entry) for entry in response.json()['messages']]
    except requests.RequestException as error:
        logger.warning('Hermes messages for %s: the request failed: %s', event.event_id, error)
        return [], f'Hermes could not be reached or returned an error: {error}'
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        # ValueError covers a response body that is not JSON; the others, a JSON body of another shape
        logger.warning('Hermes messages for %s: the response was not understood: %s', event.event_id, error)
        return [], f'Hermes returned a response the plugin could not read: {error}'

    messages = sorted(one_copy_per_title(messages), key=lambda message: message.timestamp, reverse=True)
    cache.set(cache_key, messages, HERMES_MESSAGES_CACHE_SECONDS)
    return messages, None

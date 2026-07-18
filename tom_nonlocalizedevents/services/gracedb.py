"""GraceDB adapter for manual / back-fill event ingestion.

Translates GraceDB's file listing for a superevent into the standardized
alert dictionaries consumed by services.event_ingest.
"""
import logging
from typing import Any

import requests

from django.conf import settings
from django.db import transaction

from .event_ingest import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)

# how long to wait on GraceDB API responses before giving up (seconds)
REQUESTS_TIMEOUT = 60


class GraceDBClient:
    """A client for fetching event data from the GraceDB API.

    Abstracts the details of making HTTP requests to the GraceDB service. A
    persistent ``requests.Session`` is used for connection reuse. The base URL
    comes from the ``GRACEDB_API_URL`` setting, defaulting to the public
    GraceDB instance.
    """

    def __init__(self) -> None:
        self.base_url = getattr(settings, 'GRACEDB_API_URL', 'https://gracedb.ligo.org/api/')
        self.session = requests.Session()

    def _get(self, endpoint: str, as_json: bool = True) -> Any:
        """Perform a GET request against the GraceDB API, raising on HTTP errors."""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url, timeout=REQUESTS_TIMEOUT)
            response.raise_for_status()
            return response.json() if as_json else response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"Error communicating with GraceDB endpoint {url}: {e}")
            raise

    def get_event_files(self, event_id: str) -> list[str]:
        """Return the file names associated with the given superevent.

        Example endpoint: ``superevents/S230518h/files/``
        """
        data: dict[str, Any] = self._get(f"superevents/{event_id}/files/")
        return list(data.keys())


def ingest_event_from_gracedb(event_id: str) -> tuple[int, list[str]]:
    """Orchestrate the ingestion of a full event from GraceDB.

    Fetches the skymap file listing for ``event_id`` from GraceDB, transforms
    each skymap into the standard alert format, and passes them to the core
    ingestion service. This is the primary entry point for manual or
    back-fill ingestion from GraceDB.

    Args:
        event_id: The GraceDB identifier for the event (e.g. S230518h).

    Returns:
        A tuple of (number of successfully ingested sequences, list of error
        messages encountered).
    """
    success_count = 0
    errors: list[str] = []

    try:
        client = GraceDBClient()
        # Sorting the files ensures a deterministic processing order, which is
        # critical for generating stable sequence IDs for un-versioned files.
        files = sorted(client.get_event_files(event_id))
        skymap_files = [f for f in files if f.endswith('.fits') or '.fits,' in f]

        if not skymap_files:
            errors.append(f"No skymap files found for event {event_id} in GraceDB.")
            return 0, errors

        logger.info(f"Found {len(skymap_files)} skymap file(s) to process for {event_id}.")

        for i, filename in enumerate(skymap_files):
            logger.info(f"  - Processing file: {filename}")
            try:
                with transaction.atomic():
                    alert_data = _build_gracedb_alert_data(client.base_url, event_id, filename, sequence_index=i)
                    _, seq = ingest_igwn_event_from_data(alert_data, ingestor_source='gracedb')
                    if seq:
                        success_count += 1
            except Exception as e:
                errors.append(f"Failed to process file {filename}: {e}")
                logger.exception(f"Error during ingestion of {filename} for event {event_id}")

    except Exception as e:
        errors.append(f"An unexpected error occurred while fetching data for {event_id}: {e}")
        logger.exception(f"Failed to ingest event {event_id} from GraceDB.")

    return success_count, errors


def _build_gracedb_alert_data(base_gracedb_url: str, event_id: str, filename: str,
                              sequence_index: int) -> dict[str, Any]:
    """Construct the standardized alert dictionary from GraceDB file information.

    Translates a GraceDB skymap filename into the format expected by the core
    ingestion service. The file content is not fetched here; only the URL
    where it can be found is constructed. The explicit version in the
    filename (e.g. ``,1``) is preferred for the sequence number, falling back
    to the file's index in a sorted list so un-versioned files still get
    unique, stable sequence IDs.

    Args:
        base_gracedb_url: The base URL for the GraceDB API.
        event_id: The GraceDB identifier for the event.
        filename: The name of the skymap file in GraceDB.
        sequence_index: 0-based index of the file in a deterministically
            sorted list; fallback source of a unique sequence ID.

    Returns:
        A dictionary structured for ``ingest_igwn_event_from_data``.
    """
    parts = filename.split(',')
    if len(parts) > 1:
        # Prefer the explicit version from the filename. The version is 0-indexed, so add 1.
        base_filename, sequence_num = (parts[0], int(parts[1]) + 1)
    else:
        # Fallback for un-versioned files: use the provided index to ensure a unique, stable sequence number.
        base_filename, sequence_num = (parts[0], sequence_index + 1)

    pipeline = base_filename.split('.')[0]
    is_combined = 'combined' in base_filename
    skymap_url = f"{base_gracedb_url.rstrip('/')}/superevents/{event_id}/files/{filename}"

    alert_data: dict[str, Any] = {
        'superevent_id': event_id,
        'alert_type': 'UPDATE',  # GraceDB back-fill is treated as an update.
        'sequence_num': sequence_num,
        'urls': {},
        'event': {'pipeline': pipeline} if not is_combined else {},
        'external_coinc': {}
    }

    if is_combined:
        alert_data['urls']['combined_skymap'] = skymap_url
    else:
        alert_data['urls']['skymap'] = skymap_url

    return alert_data

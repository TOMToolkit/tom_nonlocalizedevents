import logging
from typing import Dict, Any, List, Tuple

import requests

from django.conf import settings
from django.db import transaction

from .base import ingest_igwn_event_from_data

logger = logging.getLogger(__name__)


class GraceDBClient:
    """
    A client for interacting with the GraceDB API to fetch event data.

    This client abstracts the details of making HTTP requests to the GraceDB
    service, providing a clean interface for fetching event files and content.
    It uses a persistent `requests.Session` for potential performance gains
    through connection reuse.
    """

    def __init__(self):
        # It's good practice to allow the API URL to be configurable,
        # falling back to a sensible default.
        self.base_url = getattr(settings, 'GRACEDB_API_URL', 'https://gracedb.ligo.org/api/')
        self.session = requests.Session()

    def _get(self, endpoint: str, as_json: bool = True) -> Any:
        """Helper method to perform a GET request."""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url)
            response.raise_for_status()  # Will raise an HTTPError for bad responses (4xx or 5xx)
            return response.json() if as_json else response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"Error communicating with GraceDB endpoint {url}: {e}")
            raise

    def get_event_files(self, event_id: str) -> List[str]:
        """
        Fetches the list of file names associated with a given event.
        Example endpoint: superevents/S230518h/files/
        """
        data: Dict[str, Any] = self._get(f"superevents/{event_id}/files/")
        return list(data.keys())


def ingest_event_from_gracedb(event_id: str) -> Tuple[int, List[str]]:
    """
    Orchestrates the ingestion of a full event from GraceDB.

    This service function encapsulates the entire process of fetching all
    relevant skymaps for a given event_id from GraceDB, transforming them
    into the standard alert format, and passing them to the core ingestion
    service. It is designed to be the primary entry point for manual or
    back-fill ingestion from GraceDB.

    :param event_id: The GraceDB identifier for the event (e.g., S230518h).
    :return: A tuple containing the number of successfully ingested sequences
             and a list of error messages encountered during the process.
    """
    success_count = 0
    errors = []

    try:
        client = GraceDBClient()
        # Sorting the files ensures a deterministic processing order, which is critical for
        # generating stable sequence IDs for un-versioned files.
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
                error_message = f"Failed to process file {filename}: {e}"
                errors.append(error_message)
                logger.exception(f"Error during ingestion of {filename} for event {event_id}")

    except Exception as e:
        error_message = f"An unexpected error occurred while fetching data for {event_id}: {e}"
        errors.append(error_message)
        logger.exception(f"Failed to ingest event {event_id} from GraceDB.")

    return success_count, errors


def _build_gracedb_alert_data(base_gracedb_url: str, event_id: str, filename: str, sequence_index: int) -> Dict[str, Any]:
    """
    Constructs the standardized alert dictionary from GraceDB file information.

    This function translates information derived from a GraceDB filename into the
    standard format expected by the base ingestion service. It does not fetch the
    file content itself; it only constructs the URL where the file can be found.

    It prioritizes the explicit version in the filename (e.g., `,1`) for the sequence
    number but falls back to the file's index in a sorted list to ensure uniqueness
    for un-versioned files.

    :param base_gracedb_url: The base URL for the GraceDB API.
    :param event_id: The GraceDB identifier for the event.
    :param filename: The name of the skymap file in GraceDB.
    :param sequence_index: The 0-based index of the file in a deterministically sorted list,
                           used as a fallback for generating a unique sequence ID.
    :return: A dictionary structured for the `ingest_igwn_event_from_data` service.
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

    alert_data = {
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

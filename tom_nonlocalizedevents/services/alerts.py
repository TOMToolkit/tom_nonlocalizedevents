"""Typed views of the alert payloads stored in ``EventSequence.details`` and ``ExternalCoincidence.details``.

Ingestion stores the ``event`` block of an IGWN alert as it arrived, and GraceDB back-fill stores less
(``{'pipeline': ...}``, sometimes nothing). Everything that displays an alert goes through ``parse_alert``,
so every field is optional here and a missing or malformed block still yields an object with empty values.
The field names follow the IGWN user guide (https://emfollow.docs.ligo.org/userguide/content.html).
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent

logger = logging.getLogger(__name__)


def _drop_none_values(value: Any) -> Any:
    """Alert dictionaries carry ``null`` for quantities a search does not compute; leave those out."""
    if isinstance(value, dict):
        return {key: entry for key, entry in value.items() if entry is not None}
    return value or {}


class GravitationalWaveAlert(BaseModel):
    """The ``event`` block of an IGWN gravitational-wave alert, every field optional."""
    model_config = ConfigDict(extra='ignore')

    time: datetime | None = None
    far: float | None = None
    significant: bool | None = None
    instruments: list[str] = Field(default_factory=list)
    group: str | None = None
    pipeline: str | None = None
    search: str | None = None
    properties: dict[str, float] = Field(default_factory=dict)
    classification: dict[str, float] = Field(default_factory=dict)
    duration: float | None = None
    central_frequency: float | None = None
    skymap_filename: str | None = None

    @field_validator('properties', 'classification', mode='before')
    @classmethod
    def _dictionaries_without_nulls(cls, value: Any) -> Any:
        return _drop_none_values(value)

    @field_validator('instruments', mode='before')
    @classmethod
    def _instruments_list(cls, value: Any) -> Any:
        return value or []

    @property
    def is_burst(self) -> bool:
        return self.group == 'Burst'

    @property
    def is_ssm(self) -> bool:
        return self.search == 'SSM'

    def classification_by_probability(self) -> list[tuple[str, float]]:
        """Return the classification entries, most probable first."""
        return sorted(self.classification.items(), key=lambda entry: entry[1], reverse=True)

    def most_likely_classification(self) -> str:
        """Return the most likely source class, SAGUARO-style: SSM searches report SSM, CBC alerts the maximum
        of the classification, everything else (bursts) its group."""
        if self.is_ssm:
            return 'SSM'
        if self.group == 'CBC':
            return self.classification_by_probability()[0][0] if self.classification else ''
        return self.group or ''


class ExternalCoincidenceAlert(BaseModel):
    """The ``external_coinc`` block of an IGWN alert, every field optional."""
    model_config = ConfigDict(extra='ignore')

    gcn_notice_id: int | None = None
    ivorn: str | None = None
    observatory: str | None = None
    search: str | None = None
    time_difference: float | None = None
    time_coincidence_far: float | None = None
    time_sky_position_coincidence_far: float | None = None


class GenericAlert(BaseModel):
    """The alert of an event type the plugin has no schema for: the stored dictionary as it is."""
    details: dict[str, Any] = Field(default_factory=dict)

    def scalar_items(self) -> list[tuple[str, Any]]:
        """Return the entries whose values display as text (no nested dictionaries or lists)."""
        return [(key, value) for key, value in self.details.items()
                if value is not None and not isinstance(value, (dict, list))]


def parse_alert(sequence: EventSequence) -> GravitationalWaveAlert | GenericAlert:
    """Return the sequence's alert as a typed object; gravitational-wave events get the IGWN schema.

    Malformed details are logged and treated as empty rather than raised: the event pages must render
    whatever ingestion stored.
    """
    details = sequence.details if isinstance(sequence.details, dict) else {}
    if sequence.nonlocalizedevent.event_type != NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE:
        return GenericAlert(details=details)
    try:
        return GravitationalWaveAlert.model_validate(details)
    except ValidationError as error:
        logger.warning(f'EventSequence {sequence.pk} has alert details the IGWN schema does not accept: {error}')
        return GravitationalWaveAlert()


def parse_external_coincidence(sequence: EventSequence) -> ExternalCoincidenceAlert | None:
    """Return the sequence's external coincidence block, or None when the sequence has none."""
    coincidence = sequence.external_coincidence
    if coincidence is None:
        return None
    details = coincidence.details if isinstance(coincidence.details, dict) else {}
    try:
        return ExternalCoincidenceAlert.model_validate(details)
    except ValidationError as error:
        logger.warning(f'ExternalCoincidence {coincidence.pk} has details the IGWN schema does not accept: {error}')
        return ExternalCoincidenceAlert()

[![pypi](https://img.shields.io/pypi/v/tom-nonlocalizedevents.svg)](https://pypi.python.org/pypi/tom-nonlocalizedevents)
[![run-tests](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml/badge.svg)](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/cbcf7ce565d8450f86fff863ef061ff9)](https://www.codacy.com/gh/TOMToolkit/tom_nonlocalizedevents/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=TOMToolkit/tom_nonlocalizedevents&amp;utm_campaign=Badge_Grade)
[![Coverage Status](https://coveralls.io/repos/github/TOMToolkit/tom_nonlocalizedevents/badge.svg?branch=main)](https://coveralls.io/github/TOMToolkit/tom_nonlocalizedevents?branch=main)

# tom_nonlocalizedevents

This reusable TOM Toolkit app supports electromagnetic (EM) follow-up observations of
non-localized events: gravitational-wave (GW) superevents, gamma-ray bursts, neutrino
events, and X-ray transients. GW events currently have the most support and are ingested from the IGWN alert stream or manually
from GraceDB, their skymaps are processed into healpix credible regions, and candidate targets can be matched against those regions.

## Requirements

- ``PostgreSQL`` is required to support the healpix MOC volume-map lookups
via healpix-alchemy.

- ``tom-alertstreams`` is required to listen to the IGWN alert stream.

## Installation

1. Install the package into your TOM environment:

    ```bash
    pip install tom-nonlocalizedevents
    ```

2. In your project's `settings.py`, add `tom_nonlocalizedevents` to `INSTALLED_APPS`.
   With TOM Toolkit 3's scaffold that looks like:

    ```python
    INSTALLED_APPS = TOMTOOKIT_INSTALLED_APPS + [
        'tom_nonlocalizedevents',
    ]
    ```

    (`TOMTOOKIT_INSTALLED_APPS` is the (misspelled) literal variable name in the TOM Toolkit scaffold —
    don't "correct" the spelling.)

    
3. Also in `settings.py`, add the settings this app reads:

    ```python
    # required: the model layer builds Hermes links from this URL
    HERMES_API_URL = 'https://hermes.lco.global'

    # optional, shown with their defaults:
    GRACEDB_API_URL = 'https://gracedb.ligo.org/api/'  # for manual GraceDB ingestion
    SAVE_TEST_ALERTS = True  # ingest test events (event ids beginning with 'M')?
    ```

4. Run `python manage.py migrate` to create the tom_nonlocalizedevents models.

5. Optionally, set environment variables to tune the SQLAlchemy engine used for the
   healpix lookups:

    | Env variable | Description | Default |
    | ------------ | ----------- | ------- |
    | `SA_DB_CONNECTION_URL` | Postgres connection URL for SQLAlchemy | built from the Django `default` database |
    | `POOL_SIZE` | Connections kept open in the pool ([docs](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.pool_size)) | 5 |
    | `MAX_OVERFLOW` | Connections allowed in pool "overflow" ([docs](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.max_overflow)) | 10 |
    | `CREDIBLE_REGION_PROBABILITIES` | JSON list of credible-region probabilities checked for each candidate | `'[0.25, 0.5, 0.75, 0.9, 0.95]'` |

## Features

- **The event list page** (`/nonlocalizedevents/`): lazy-loading tabs — All,
  Gravitational Wave (the landing tab), Gamma-ray Burst, Neutrino, X-ray Transient,
  Unknown — each with its own sortable, filterable htmx table. The GW tab carries the
  science columns and filters (1/FAR, most-likely classification, distance, HasNS,
  HasRemnant); the other event types currently show a generic table awaiting
  type-specific columns.
- **Event detail pages**, resolved per event type (type-specific templates with a
  generic fallback), showing the event's sequences, localization areas and distances,
  and external links (Hermes, GraceDB, Treasure Map).
- **Manual GraceDB ingestion**: the *Ingest event from GraceDB* button on the list page
  (login required), or the equivalent management command:

    ```bash
    python manage.py ingest_LVK_event S230518h
    ```

- **REST API** endpoints under `/api/`: `nonlocalizedevents/`, `eventlocalizations/`,
  and `eventcandidates/` (with bulk-create and PATCH support for candidates).

## Configuration to ingest GW events from the IGWN alert stream

To ingest GW events automatically, install and configure
[tom_alertstreams](https://github.com/TOMToolkit/tom-alertstreams) in your TOM
(`tom_alertstreams` is intentionally **not** a dependency of this app — it is only
needed for stream listening). Point the `igwn.gwalert` topic at this app's handler:

```python
ALERT_STREAMS = [
    {
        'ACTIVE': True,
        'NAME': 'tom_alertstreams.alertstreams.hopskotch.HopskotchAlertStream',
        'OPTIONS': {
            'URL': 'kafka://kafka.scimma.org/',
            'USERNAME': os.getenv('SCIMMA_AUTH_USERNAME'),
            'PASSWORD': os.getenv('SCIMMA_AUTH_PASSWORD'),
            'TOPIC_HANDLERS': {
                'igwn.gwalert':
                    'tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler.handle_igwn_message',
            },
        },
    },
]
```

Then run the stream listener: `python manage.py readstreams`.

## Development

### Extending for an event type

The per-event-type tabs and detail pages are extension points. To implement an event
type (e.g. gamma-ray bursts), add its type-specific columns, filters, and detail
content to its stub classes and template:

- `tables.py` — `GammaRayBurstEventTable`: add type-specific columns.
- `filters.py` — `GammaRayBurstEventFilterSet`: add type-specific filters.
- `templates/tom_nonlocalizedevents/detail/grb_detail.html` — extend the generic detail
  page with type-specific content.

### Running the tests

The test suite needs a reachable PostgreSQL. The repository's
[docker-compose.yml](docker-compose.yml) provides one whose credentials match the test
harness defaults:

```bash
docker compose up -d
python tom_nonlocalizedevents/tests/run_tests.py   # from the repository root
```

### Pointing a TOM at the docker-compose database

When developing a TOM (or developing `tom_nonlocalizedevents` in the context
of a TOM), this Django `DATABASES` configuration dictionary matches the
PostgreSQL database configured by the ``docker-compose.yml`` in this repo. Put it in `settings.py` or `local_settings.py`.

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'tom_nonlocalizedevents',  #  POSTGRES_DB in docker-compose.yml
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    },
}
```

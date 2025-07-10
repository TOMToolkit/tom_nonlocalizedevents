[![pypi](https://img.shields.io/pypi/v/tom-nonlocalizedevents.svg)](https://pypi.python.org/pypi/tom-nonlocalizedevents)
[![run-tests](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml/badge.svg)](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/cbcf7ce565d8450f86fff863ef061ff9)](https://www.codacy.com/gh/TOMToolkit/tom_nonlocalizedevents/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=TOMToolkit/tom_nonlocalizedevents&amp;utm_campaign=Badge_Grade)
[![Coverage Status](https://coveralls.io/repos/github/TOMToolkit/tom_nonlocalizedevents/badge.svg?branch=main)](https://coveralls.io/github/TOMToolkit/tom_nonlocalizedevents?branch=main)

# GW Superevent (or GRB, Neutrino) EM follow-up

This reusable TOM Toolkit app provides support for gravitational wave (GW) superevent
and other non-localized event electromagnetic (EM) follow up observations.  

## Requirements

This TOM plugin requires the use of a postgresql 14+ database backend, since it leverages some postgres specific stuff for MOC volume map lookups.

## Installation

1. Install the package into your TOM environment:
    ```bash
    pip install tom-nonlocalizedevents
   ```

2. In your project `settings.py`, add `tom_nonlocalizedevents` to your `INSTALLED_APPS` setting:

    ```python
    INSTALLED_APPS = [
        ...
        'tom_nonlocalizedevents',
    ]
    ```
    This will add `tom_nonlocalizedevents` urlpatterns to your project (TOM) urlpatterns and add a
    "Nonlocalized Events" item to your TOM's navbar, which takes you to the Nonlocalized Events index page.

    Also add the following to your settings if they are not already there, setting whatever default values you need for your setup. These point to your deployed TOM toolkit instance, and to the HERMES API:
    ```python
    TOM_API_URL = os.getenv('TOM_API_URL', 'http://127.0.0.1:8000')
    HERMES_API_URL = os.getenv('HERMES_API_URL', 'https://hermes-dev.lco.global')

    ```

3. Run ``python manage.py migrate`` to create the tom_nonlocalizedevents models.

4. Set environment variables below to configure different connections:

| Env variable | Description | Default |
| ------------ | ----------- | ------- |
| `SA_DB_CONNECTION_URL` | Location of your django postgres database used for sqlalchemy | by default, this uses Django `default` DB for the project |
| `POOL_SIZE` | The number of connections to keep open inside the connection pool. ([docs](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.pool_size)) | 5 |
| `MAX_OVERFLOW` | The number of connections to allow in connection pool “overflow”. ([docs](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.max_overflow)) | 10 |
| `CREDIBLE_REGION_PROBABILITIES` | JSON List of Credible Region probabilities to automatically check each candidate target for | `'[0.25, 0.5, 0.75, 0.9, 0.95]'` |
| `SAVE_TEST_ALERTS` | Boolean on if you want to save test nonlocalizedevents into your database (those with event_id beginning with 'M') | `true` |

See [Engine Configuration](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine) for
details of SQLAlchemy Engine Configuration.

## Running the tests

In order to run the tests, run the following in your virtualenv:

`python manage.py test`

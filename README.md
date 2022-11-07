[![pypi](https://img.shields.io/pypi/v/tom-superevents.svg)](https://pypi.python.org/pypi/tom-superevents)
[![run-tests](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml/badge.svg)](https://github.com/TOMToolkit/tom_nonlocalizedevents/actions/workflows/run-tests.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/cbcf7ce565d8450f86fff863ef061ff9)](https://www.codacy.com/gh/TOMToolkit/tom_nonlocalizedevents/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=TOMToolkit/tom_nonlocalizedevents&amp;utm_campaign=Badge_Grade)
[![Coverage Status](https://coveralls.io/repos/github/TOMToolkit/tom_nonlocalizedevents/badge.svg?branch=main)](https://coveralls.io/github/TOMToolkit/tom_nonlocalizedevents?branch=main)

# GW Superevent (or GRB, Neutrino) EM follow-up

This reusable TOM Toolkit app provides support for gravitational wave (GW) superevent
and other non-localized event electromagnetic (EM) follow up observations.  

## Requirements

This TOM plugin requires the use of a postgresql database backend, since it leverages some postgres specific stuff for MOC volume map lookups.

## Installation

1. Install the package into your TOM environment:
    ```bash
    pip install tom-nonlocalizedevents
   ```

2. In your project `settings.py`, add `tom_nonlocalizedevents` and `webpack_loader` to your `INSTALLED_APPS` setting:

    ```python
    INSTALLED_APPS = [
        'webpack_loader',
        ...
        'tom_nonlocalizedevents',
    ]
    ```
    
    Also include the following Django-Webpack-Loader settings in your settings.py:

    ```python
    VUE_FRONTEND_DIR_TOM_NONLOCAL = os.path.join(STATIC_ROOT, 'tom_nonlocalizedevents/vue')
    WEBPACK_LOADER = {
        'TOM_NONLOCALIZEDEVENTS': {
            'CACHE': not DEBUG,
            'BUNDLE_DIR_NAME': 'tom_nonlocalizedevents/vue/',  # must end with slash
            'STATS_FILE': os.path.join(VUE_FRONTEND_DIR_TOM_NONLOCAL, 'webpack-stats.json'),
            'POLL_INTERVAL': 0.1,
            'TIMEOUT': None,
            'IGNORE': [r'.+\.hot-update.js', r'.+\.map']
        }
    }
    ```

    If `WEBPACK_LOADER` is already defined in your settings, then integrate these values in to it.

3. Include the tom_nonlocalizedevent URLconf in your project `urls.py`:
   ```python
   urlpatterns = [
        ...
        path('nonlocalizedevents/', include('tom_nonlocalizedevents.urls')),
   ]
   ```

4. Run ``python manage.py migrate`` to create the tom_nonlocalizedevents models.

5. Set environment variables below to configure different connections:
| Env variable | Description | Default |
|--------------|-------------|---------|
| `SKIP_API_URL` | base URL to skip, used to get nonlocalized event details and candidates | `http://skip.dev.hop.scimma.org` |
| `TOM_API_URL` | base URL to your TOM, usd to call the TOM API from the vue components | `http://localhost:8000` |
| `SA_DB_CONNECTION_URL` | Location of your django postgres database used for sqlalchemy | uses Django `default` DB for the project |
| `CREDIBLE_REGION_PROBABILITIES` | JSON List of Credible Region probabilities to automatically check each candidate target for | `'[0.25, 0.5, 0.75, 0.9, 0.95]'` |

6. In your TOM project, make sure to run `python manage.py collectstatic` after installing this app, to collect its vue pages into your staticfiles dir.

7. If you want to automatically ingest GW events into your TOM, you should also install the `tom_alertstreams` app into your TOM and configure it to use the tom_nonlocalizedevents handler to ingest GW events: `tom_nonlocalizedevents.alertstream_handlers.gw_event_handler.handle_message`. There is also a handler to handle retractions via the `handle_retraction` method in that package. These are currently written to work with LVC GW messages.

## Development

When any changes are made to this library, the vue files will need to be build and bundled and committed into the repo so that they can be bundled and deployed with the django package. This means that after making any vue changes, you must run `npm run build` within the `tom_nonlocalizedevents_vue` directory once, which will install the built files into `tom_nonlocalizedevents/static/`, and then those built files will need to be committed into the repo. This allows django projects using this library to get those files when running `python manage.py collectstatic`.

## Running the tests

In order to run the tests, run the following in your virtualenv:

`python manage.py test`


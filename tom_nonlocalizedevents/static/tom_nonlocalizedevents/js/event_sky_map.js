/*
 * The event sky map: an Aladin Lite viewer drawing the layers that partials/event_sky_map_data.html
 * serialises (event_summary/sky_map.py: credible-region MOCs, candidates, sun and moon).
 *
 * Lifecycle: boot() runs for the initial page and for every htmx swap that brings a new #event-sky-map
 * container (a browser-back full-page restore does); it loads Aladin Lite 3.8.2 once per browser session,
 * creates the viewer and draws the layers. An alert-tab click replaces only the data element inside
 * #event-sky-map-data-slot (out of band); the observer on that slot redraws the layers on the existing
 * viewer, so the canvas is never re-created for a tab change. Nothing here is inline in a template.
 */
(function () {
    'use strict';

    const CONTAINER_ID = 'event-sky-map';
    const VIEWER_ID = 'event-sky-map-viewer';
    const DATA_SLOT_ID = 'event-sky-map-data-slot';
    const DATA_ID = 'event-sky-map-data';
    const ALADIN_URL = 'https://aladin.cds.unistra.fr/AladinLite/api/v3/3.8.2/aladin.js';
    const ALADIN_STORAGE_KEY = 'tom_nonlocalizedevents/aladin.js/3.8.2';
    const CANDIDATE_COLOR = '#4da3ff';
    const RETIRED_COLOR = '#8a8f94';
    const SUN_COLOR = '#ffd43b';
    const MOON_COLOR = '#cfd4da';

    const skyMap = {
        aladin: null,
        mocs: [],
        candidates: null,
        retired: null,
        bodies: null,
        exclusions: null,
        generation: 0,
    };

    // Aladin Lite is fetched once per browser session and kept in sessionStorage (the pattern of
    // tom_targets' aladin_skymap.html); a page that already loaded it (window.A) is reused.
    async function loadAladin() {
        if (window.A) {
            return;
        }
        let source = null;
        try {
            source = sessionStorage.getItem(ALADIN_STORAGE_KEY);
        } catch (error) {
            source = null;
        }
        if (!source) {
            const response = await fetch(ALADIN_URL);
            if (!response.ok) {
                throw new Error('Aladin Lite could not be fetched: HTTP ' + response.status);
            }
            source = await response.text();
            try {
                sessionStorage.setItem(ALADIN_STORAGE_KEY, source);
            } catch (error) {
                // storage full or disabled: the script still runs, it is just fetched again next time
            }
        }
        const script = document.createElement('script');
        script.text = source;
        document.head.appendChild(script);
    }

    function readLayers() {
        const element = document.getElementById(DATA_ID);
        return element ? JSON.parse(element.textContent) : null;
    }

    function bodyDescription(body) {
        let description = 'RA ' + body.ra.toFixed(3) + '<br>Dec ' + body.dec.toFixed(3) +
            '<br>avoid within ' + body.exclusion_radius_deg.toFixed(0) + '°';
        if (body.illumination !== null && body.illumination !== undefined) {
            description += '<br>illumination ' + body.illumination.toFixed(2);
        }
        return description;
    }

    function drawBodies(layers) {
        skyMap.bodies.removeAll();
        skyMap.exclusions.removeAll();
        [layers.sun, layers.moon].forEach(function (body) {
            skyMap.bodies.addSources([A.marker(body.ra, body.dec, {popupTitle: body.name, popupDesc: bodyDescription(body)})]);
            skyMap.exclusions.add(A.circle(body.ra, body.dec, body.exclusion_radius_deg,
                {color: body.name === 'Sun' ? SUN_COLOR : MOON_COLOR, lineWidth: 1}));
        });
    }

    function drawCandidates(layers) {
        skyMap.candidates.removeAll();
        skyMap.retired.removeAll();
        layers.candidates.forEach(function (candidate) {
            const marker = A.marker(candidate.ra, candidate.dec, {
                popupTitle: candidate.name,
                popupDesc: 'RA ' + candidate.ra.toFixed(5) + '<br>Dec ' + candidate.dec.toFixed(5) +
                    (candidate.viable ? '' : '<br>retired') + '<br><a href="' + candidate.url + '">target page</a>',
            });
            (candidate.viable ? skyMap.candidates : skyMap.retired).addSources([marker]);
        });
    }

    // Each MOC is fetched from the plugin's endpoint; a later update() (another tab click) outruns a slower
    // fetch, so results of an older generation are dropped instead of drawn over the newer layers.
    async function drawMocs(layers, generation) {
        skyMap.mocs.forEach(function (moc) { skyMap.aladin.removeOverlay(moc); });
        skyMap.mocs = [];
        for (const layer of layers.mocs) {
            let json;
            try {
                const response = await fetch(layer.url, {credentials: 'same-origin'});
                if (!response.ok) {
                    continue;
                }
                json = await response.json();
            } catch (error) {
                continue;
            }
            if (generation !== skyMap.generation) {
                return;
            }
            const moc = A.MOCFromJSON(json, {
                name: layer.label,
                color: layer.color,
                fillColor: layer.color,
                lineWidth: 2,
                opacity: layer.fill ? 0.3 : 0.9,
                fill: layer.fill,
                perimeter: !layer.fill,
                edge: false,
            });
            skyMap.aladin.addMOC(moc);
            skyMap.mocs.push(moc);
        }
    }

    function update(layers) {
        if (!skyMap.aladin || !layers) {
            return;
        }
        skyMap.generation += 1;
        if (layers.center_ra !== null && layers.center_dec !== null) {
            skyMap.aladin.gotoRaDec(layers.center_ra, layers.center_dec);
        }
        skyMap.aladin.setFoV(layers.fov_deg);
        drawBodies(layers);
        drawCandidates(layers);
        drawMocs(layers, skyMap.generation);
    }

    function observeDataSlot(container) {
        const slot = container.querySelector('#' + DATA_SLOT_ID);
        if (!slot) {
            return;
        }
        new MutationObserver(function () { update(readLayers()); }).observe(slot, {childList: true});
    }

    async function boot() {
        const container = document.getElementById(CONTAINER_ID);
        if (!container || container.dataset.booted) {
            return;
        }
        container.dataset.booted = 'true';
        try {
            await loadAladin();
            await A.init;
        } catch (error) {
            container.querySelector('#' + VIEWER_ID).textContent = 'The sky map is unavailable: ' + error.message;
            return;
        }
        skyMap.aladin = A.aladin('#' + VIEWER_ID, {
            survey: 'P/DSS2/color',
            projection: 'AIT',
            fov: 360,
            showReticle: false,
            showCooGrid: true,
            showCooGridControl: true,
            showFullscreenControl: true,
            showLayersControl: true,
        });
        skyMap.aladin.setCooGrid({color: 'grey', labelSize: 10});
        skyMap.candidates = A.catalog({name: 'Candidates', color: CANDIDATE_COLOR, sourceSize: 14});
        skyMap.retired = A.catalog({name: 'Retired candidates', color: RETIRED_COLOR, sourceSize: 12, shape: 'cross'});
        skyMap.bodies = A.catalog({name: 'Sun and moon', color: SUN_COLOR, sourceSize: 18});
        skyMap.exclusions = A.graphicOverlay({name: 'Avoidance', color: SUN_COLOR, lineWidth: 1});
        skyMap.aladin.addCatalog(skyMap.candidates);
        skyMap.aladin.addCatalog(skyMap.retired);
        skyMap.aladin.addCatalog(skyMap.bodies);
        skyMap.aladin.addOverlay(skyMap.exclusions);
        observeDataSlot(container);
        update(readLayers());
    }

    if (window.htmx) {
        window.htmx.onLoad(boot);
    } else {
        document.addEventListener('DOMContentLoaded', boot);
    }

    window.nonlocalizedevents = window.nonlocalizedevents || {};
    window.nonlocalizedevents.skyMap = {boot: boot, update: update};
})();

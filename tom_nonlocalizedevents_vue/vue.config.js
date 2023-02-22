const BundleTracker = require("webpack-bundle-tracker");


// webpack-stats.json will describe bundles built by this config and
// django-webpack-loader will be configured to read that file to identify
// and serve those bundles.

//
// declare a list of pages whcih will become the name of the bundles.
// The bundle names are used in the django template: {% render_bundle NAME %}
// Also, define the entry point JS for each bundle.
//
const pages = {
    'superevent_vue_app': {
        entry: './src/superevent.js'
    }
}

module.exports = {
    pages: pages,
    filenameHashing: false,
    runtimeCompiler: true,
    productionSourceMap: false,
    // this key is written in to the webpack-stats.json file during local development
    // (i.e. non-production) and the JS is served by the npm run serve server.
    // in production, the pages are found in the outputDir and served by django.
    // NB: in this context, staging means tom-demo-dev
    publicPath: process.env.NODE_ENV === 'production' || 'staging'
        ? ''
        : 'http://localhost:8080/',
    outputDir: '../tom_nonlocalizedevents/static/tom_nonlocalizedevents/vue/',

    chainWebpack: config => {

        config.optimization
            .splitChunks(false);

        Object.keys(pages).forEach(page => {
            config.plugins.delete(`html-${page}`);
            config.plugins.delete(`preload-${page}`);
            config.plugins.delete(`prefetch-${page}`);
        })

        // django-webpack-loader will read webpack-stats.json
        config
            .plugin('BundleTracker')
            .use(BundleTracker, [{
                filename: '../tom_nonlocalizedevents/static/tom_nonlocalizedevents/vue/webpack-stats.json',
                relativePath: true                     
            }]);

        // this allows reference paths to static file within Vue components like this:
        // for example, <img src="~__STATIC__/logo.png">
        config.resolve.alias
            .set('__STATIC__', 'static')

        // configure a non-production dev server for hot reloading of Vue components during development
        config.devServer
            .public('http://localhost:8080')
            .host('localhost')
            .port(8080)
            .hotOnly(true)
            .watchOptions({poll: 1000})
            .https(false)
            .headers({"Access-Control-Allow-Origin": ["*"]})

    }
};

// reference: https://gist.github.com/ilikerobots/30644a69428e8873622fb6963d0a22eb

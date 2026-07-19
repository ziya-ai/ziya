const path = require('path');

module.exports = {
  webpack: {
    configure: (webpackConfig) => {
      webpackConfig.module.rules.push({
        test: /\.m?js$/,
        resolve: {
          fullySpecified: false,
        },
      });

      // vexflow (music notation) is an OPTIONAL dependency — see
      // optionalDependencies in package.json.  If it isn't installed,
      // webpack's normal behavior is to fail the ENTIRE build the moment
      // any module does `import('vexflow')`, even though the only affected
      // feature is music notation rendering.  Alias it to a tiny stub in
      // that case so the build succeeds; the stub throws a clear,
      // actionable error at the point of use (caught by both the inline
      // codespan renderer and the fenced-block plugin's error panel).
      let vexflowInstalled = true;
      try {
        require.resolve('vexflow');
      } catch {
        vexflowInstalled = false;
      }
      if (!vexflowInstalled) {
        webpackConfig.resolve = webpackConfig.resolve || {};
        webpackConfig.resolve.alias = {
          ...(webpackConfig.resolve.alias || {}),
          vexflow: path.resolve(__dirname, 'src/utils/d3Plugins/vexflowStub.js'),
        };
        console.warn(
          '\n⚠️  vexflow not installed — music notation rendering will be ' +
          'disabled.\n   Run `npm install vexflow` to enable it.\n'
        );
      }

      // Profile build: emit source maps and keep React component names
      // readable in the browser profiler.  Activated by:
      //   npm run build:profile
      if (process.env.REACT_APP_PROFILE === 'true') {
        // 'source-map' produces full-fidelity maps with original file/line
        // info.  'hidden-source-map' is the CRA default (maps exist but
        // browsers can't find them without manual loading).
        webpackConfig.devtool = 'source-map';

        // Alias the production React scheduler to the profiling build so
        // component names and timings survive dead-code elimination.
        webpackConfig.resolve = webpackConfig.resolve || {};
        webpackConfig.resolve.alias = {
          ...(webpackConfig.resolve.alias || {}),
          'react-dom$': 'react-dom/profiling',
          'scheduler/tracing': 'scheduler/tracing-profiling',
        };
      }

      return webpackConfig;
    },
  },
};

module.exports = {
  webpack: {
    configure: (webpackConfig) => {
      webpackConfig.module.rules.push({
        test: /\.m?js$/,
        resolve: {
          fullySpecified: false,
        },
      });

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

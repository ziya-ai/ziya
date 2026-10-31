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

      // Production: drop console.log / debug / trace.  ~1,500 log calls
      // shipped (vegaLitePlugin alone has 320), several on per-flush and
      // per-save paths; once DevTools has been opened the console keeps
      // every logged object alive for the page's lifetime, and
      // console.trace captures a stack per call.  pure_funcs drops only the
      // call: arguments with side effects are still evaluated.  warn,
      // error and info are kept -- the window.debug* helpers print through
      // console.info for that reason.
      //
      // pure_funcs drops a call only when its VALUE is unused and only when
      // it is the literal console.log: `m => console.log(m)` (returned) and
      // `debug ? console.log : noop` (aliased) both survive.  Write those
      // with a block body; consoleStripBuild.test.ts scans for both shapes.
      // Groups are stripped too: their logs are gone, so they'd print empty.
      if (webpackConfig.mode === 'production') {
        const STRIP = ['console.log', 'console.debug', 'console.trace',
          'console.group', 'console.groupCollapsed', 'console.groupEnd'];
        for (const plugin of (webpackConfig.optimization && webpackConfig.optimization.minimizer) || []) {
          // terser-webpack-plugin v5 keeps terserOptions here.
          const terserOptions = plugin && plugin.options && plugin.options.minimizer
            && plugin.options.minimizer.options;
          if (!terserOptions || plugin.constructor.name !== 'TerserPlugin') continue;
          const compress = terserOptions.compress && typeof terserOptions.compress === 'object'
            ? terserOptions.compress : {};
          terserOptions.compress = {
            ...compress,
            pure_funcs: Array.from(new Set([...(compress.pure_funcs || []), ...STRIP])),
          };
        }
      }

      return webpackConfig;
    },
  },
  jest: {
    configure: (jestConfig) => {
      // D3Renderer does `await import('d3')`, which resolves to d3's ESM
      // entry; jest cannot parse it without a transform. Use the UMD bundle
      // under test instead.
      jestConfig.moduleNameMapper = {
        ...(jestConfig.moduleNameMapper || {}),
        '^d3$': '<rootDir>/node_modules/d3/dist/d3.min.js',
      };
      return jestConfig;
    },
  },
};

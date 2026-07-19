/**
 * Build-time stand-in for the optional `vexflow` dependency.
 * Aliased in via craco.config.js ONLY when vexflow is not installed
 * (see optionalDependencies in package.json), so the frontend build
 * still succeeds without it.
 *
 * Throws the moment any music-notation code path actually tries to use
 * it, with an actionable message.  Both call sites already catch this:
 *   - MusicInlineRenderer (inline `music:` codespans) catches and falls
 *     back to plain monospace text, matching MathRenderer's degradation.
 *   - musicPlugin.ts's render() (fenced ```music``` blocks) catches and
 *     shows the error in its existing error panel with this message.
 */
const MESSAGE = 'Music notation support requires the optional "vexflow" package. Run `npm install vexflow` in the frontend/ directory and rebuild to enable it.';

throw new Error(MESSAGE);

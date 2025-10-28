/**
 * Conditionally loads internal MCP formatters if available
 */

export async function loadInternalFormatters() {
  try {
    // Only load if the internal directory exists
    const internalFormatter = await import('../internal/mcpBuilderFormatter');
    console.log('✅ Internal MCP formatters loaded');
    return true;
  } catch (error) {
    console.log('ℹ️  Internal MCP formatters not available (this is normal for public deployments)');
    return false;
  }
}

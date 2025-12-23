/**
 * Utility for pretty-printing MCP tool outputs
 */

/**
 * Generic function to create tool display headers with parameters
 * Plugin interface allows internal formatter to override for Amazon-specific tools
 */
let enhanceToolHeaderPlugin: ((toolName: string, baseHeader: string, args: Record<string, any>) => string | null) | null = null;

export function registerToolHeaderEnhancer(enhancer: (toolName: string, baseHeader: string, args: Record<string, any>) => string | null) {
  enhanceToolHeaderPlugin = enhancer;
}

/**
 * Enhance tool display header with arguments for better visibility
 * Generic implementation that can be overridden by internal formatter
 */
export function enhanceToolDisplayHeader(
  toolName: string,
  baseHeader: string,
  args: Record<string, any>
): string {
  // Try internal formatter first if registered
  if (enhanceToolHeaderPlugin) {
    const internalResult = enhanceToolHeaderPlugin(toolName, baseHeader, args);
    if (internalResult) return internalResult;
  }

  // Generic fallback: extract the most meaningful parameter
  // Common parameter names that indicate what was searched/queried
  const meaningfulParams = [
    'query', 'searchQuery', 'command', 'url',
    'acronym', 'taskId', 'ticketId', 'pipelineName'
  ];

  for (const param of meaningfulParams) {
    if (args[param] && typeof args[param] === 'string') {
      // Truncate long values
      let value = args[param];
      if (value.length > 60) {
        value = value.substring(0, 60) + '...';
      }

      // Format the header with the parameter
      const paramLabel = param.replace(/([A-Z])/g, ' $1').trim();
      return `${baseHeader}: ${value}`;
    }
  }

  // No meaningful parameters found, return base header
  return baseHeader;
}

/**
 * Detect and parse rich text formats in tool outputs
 */
function detectAndParseRichContent(content: string): {
  hasRichContent: boolean;
  format: 'html' | 'markdown' | 'ansi' | 'none';
  parsed?: string;
} {
  if (!content || typeof content !== 'string') {
    return { hasRichContent: false, format: 'none' };
  }

  // Detect HTML with code blocks (like workspace search results)
  if (content.includes('<pre><code class="language-')) {
    const parsedMarkdown = parseHtmlCodeBlocksToMarkdown(content);
    if (parsedMarkdown) {
      return {
        hasRichContent: true,
        format: 'html',
        parsed: parsedMarkdown
      };
    }
  }

  // Detect general HTML content
  if (content.includes('<div') || content.includes('<p>') || content.includes('<table')) {
    return {
      hasRichContent: true,
      format: 'html',
      parsed: content
    };
  }

  // Detect markdown formatting
  const hasMarkdownIndicators = (
    content.includes('```') || // Code blocks
    /^#{1,6}\s/.test(content) || // Headers
    /^\*\s/.test(content) || // Unordered lists
    /^\d+\.\s/.test(content) || // Ordered lists
    content.includes('**') || // Bold
    content.includes('__') || // Bold alternative
    /\[.*\]\(.*\)/.test(content) // Links
  );

  if (hasMarkdownIndicators) {
    return {
      hasRichContent: true,
      format: 'markdown',
      parsed: content
    };
  }

  // Detect ANSI escape codes (colored terminal output)
  if (content.includes('\x1b[') || content.includes('\u001b[')) {
    return {
      hasRichContent: true,
      format: 'ansi',
      parsed: content
    };
  }

  return { hasRichContent: false, format: 'none' };
}

/**
 * Parse HTML code blocks to markdown
 */
function parseHtmlCodeBlocksToMarkdown(htmlContent: string): string | null {
  // Extract result blocks: "N. /path/to/file ... <pre><code class="language-X">...</code></pre>"
  const resultRegex = /(\d+)\.\s+([^\n]+)\n\s+(\d+\s+matching\s+lines?)\s*\n<pre><code\s+class="language-(\w+)">([^]*?)<\/code><\/pre>/g;

  const matches = [...htmlContent.matchAll(resultRegex)];
  if (matches.length === 0) return null;

  let markdown = '';
  matches.forEach((match, index) => {
    const [, fileNumber, filePath, matchCount, language, code] = match;
    markdown += `### ${fileNumber}. ${filePath}\n\n`;
    markdown += `*${matchCount}*\n\n`;
    markdown += `\`\`\`${language || 'text'}\n${code.trim()}\n\`\`\`\n\n`;

    if (index < matches.length - 1) {
      markdown += `---\n\n`;
    }
  });

  return markdown;
}

export interface FormattedOutput {
  content: string;
  type: 'json' | 'text' | 'table' | 'list' | 'error' | 'search_results' | 'html_content';
  showInput?: boolean;
  summary?: string;
  collapsed?: boolean;
  metadata?: Record<string, any>;
  hierarchicalResults?: Array<{
    title: string;
    content: string;
    language?: string;
    metadata?: Record<string, any>;
  }>;
}

/**
 * Convert plain text URLs to markdown links
 */
function convertUrlsToMarkdownLinks(content: string): string {
  // Match URLs that aren't already in markdown link format [text](url)
  // This regex looks for URLs not preceded by ]( to avoid double-converting
  const urlRegex = /(?<!\]\()https?:\/\/[^\s<>]+/g;

  return content.replace(urlRegex, (url) => {
    // Extract a readable display name from the URL
    let displayText = url;

    // Remove trailing punctuation that might not be part of the URL
    const trailingPunct = url.match(/[.,;:!?)]+$/);
    if (trailingPunct) {
      url = url.slice(0, -trailingPunct[0].length);
      displayText = url;
    }

    // For long URLs, show domain + truncated path
    if (url.length > 60) {
      try {
        const urlObj = new URL(url);
        const pathSnippet = urlObj.pathname.length > 25
          ? urlObj.pathname.substring(0, 25) + '...'
          : urlObj.pathname;
        displayText = `${urlObj.hostname}${pathSnippet}`;
      } catch {
        displayText = `${url.substring(0, 60)}...`;
      }
    }

    // Return markdown link format
    return `[${displayText}](${url})${trailingPunct ? trailingPunct[0] : ''}`;
  });
}

/**
 * Convert HTML details/summary tags to markdown-friendly format
 * This prevents HTML from breaking out of TOOL_BLOCK fences
 */
function convertDetailsToMarkdown(content: string): string {
  // Match <details><summary>...</summary>...</details> patterns
  const detailsRegex = /<details>\s*<summary>(.*?)<\/summary>\s*([\s\S]*?)<\/details>/g;

  let result = content.replace(detailsRegex, (match, summary, body) => {
    // Strip HTML tags from summary
    const cleanSummary = summary.replace(/<[^>]+>/g, '');

    // Strip HTML tags from body but preserve code blocks
    const cleanBody = body.replace(/<[^>]+>/g, '').trim();

    // Convert URLs in the body to clickable links
    const bodyWithLinks = convertUrlsToMarkdownLinks(cleanBody);

    // Format as expandable section with bold header
    return `<details>\n<summary><strong>${cleanSummary}</strong></summary>\n\n${bodyWithLinks}\n\n</details>`;
  });

  // Convert URLs that are outside details blocks
  result = convertUrlsToMarkdownLinks(result);

  return result;
}

export function formatMCPOutput(
  toolName: string,
  result: any,
  input?: any,
  options: {
    maxLength?: number;
    showInput?: boolean;
    compact?: boolean;
    defaultCollapsed?: boolean;
    fadeLastLine?: boolean;
  } = {}
): FormattedOutput {
  const { maxLength = 5000, showInput = false, compact = false, defaultCollapsed = true, fadeLastLine = false } = options;

  // Create a generic tool summary from input parameters
  const toolSummary = createToolSummary(toolName, input);

  // CRITICAL: If result is a string with HTML, convert details tags to markdown
  // This prevents HTML from breaking out of TOOL_BLOCK fences
  if (typeof result === 'string' && (result.includes('<details>') || result.includes('<summary>'))) {
    result = convertDetailsToMarkdown(result);
  }

  // EARLY CHECK: Detect rich content and render it appropriately
  if (typeof result === 'string') {
    const richContent = detectAndParseRichContent(result);

    if (richContent.hasRichContent && richContent.parsed) {
      const lines = richContent.parsed.split('\n');
      const shouldCollapse = richContent.parsed.length > 1000 || lines.length > 20;

      // Also convert URLs to links in rich content
      if (richContent.parsed) {
        richContent.parsed = convertUrlsToMarkdownLinks(richContent.parsed);
      }

      return {
        content: richContent.parsed,
        type: 'text', // Will be rendered as markdown through ToolBlock
        collapsed: shouldCollapse && defaultCollapsed,
        summary: shouldCollapse ? `${toolSummary ? `${toolSummary} - ` : ''}Rich content (${lines.length} lines)` : toolSummary
      };
    }
  }

  // Try FormatterRegistry (plugin system)
  if ((window as any).FormatterRegistry) {
    const formatter = (window as any).FormatterRegistry.getFormatter(toolName);
    if (formatter) {
      const formatterResult = formatter.format(toolName, result, { ...options, input });
      if (formatterResult) return formatterResult;
    }
  }

  // Handle double-encoded JSON strings (common in MCP responses)
  if (typeof result === 'string' && result.startsWith('{') && result.includes('\\n')) {
    try {
      result = JSON.parse(result);
    } catch (e) {
      // Not valid JSON, continue with string processing
    }
  }

  // Handle error responses
  if (result && typeof result === 'object' && result.error) {
    return {
      content: `❌ Error: ${result.error}\n${result.detail || ''}`,
      type: 'error',
      collapsed: false
    };
  }

  // Handle shell command outputs specially
  if (toolName === 'mcp_run_shell_command' && typeof result === 'string') {
    // For shell commands, the result should already include the command line
    // so we don't need to add it again
    const lines = result.split('\n');
    const shouldCollapse = lines.length > 5;

    let content = result;
    if (shouldCollapse && fadeLastLine && lines.length > 3) {
      const visibleLines = lines.slice(0, 3);
      const remainingLines = lines.slice(3);
      const lastVisibleLine = visibleLines[visibleLines.length - 1];
      const otherLines = visibleLines.slice(0, -1);

      content = otherLines.join('\n') + '\n' +
        `<span style="opacity: 0.6; font-style: italic;">${lastVisibleLine}</span>\n` +
        `<span style="opacity: 0.4; font-size: 0.9em;">... ${remainingLines.length} more lines ...</span>`;
    }

    return {
      content,
      type: 'text',
      showInput: false,
      collapsed: shouldCollapse && defaultCollapsed,
      summary: shouldCollapse ? `Output (${lines.length} lines, ${result.length} chars)` : undefined
    };
  }

  // Handle sequential thinking tool outputs specially
  if (toolName === 'mcp_sequentialthinking' || toolName === 'sequentialthinking') {
    return formatSequentialThinking(result, input, options);
  }

  // Handle time tool outputs specially (public builtin tool)
  if (toolName === 'mcp_get_current_time' || toolName === 'get_current_time') {
    let cleanResult = typeof result === 'string' ? result : String(result);

    // Clean up common formatting patterns
    cleanResult = cleanResult.replace(/^Input:\s*\{\}\s*\n*/, '');
    cleanResult = cleanResult.replace(/^Result:\s*\n*/, '');
    cleanResult = cleanResult.replace(/\n*Result:\s*\n*/, '\n');
    cleanResult = cleanResult.trim();

    // If it still contains "Result:" at the start, remove it
    if (cleanResult.startsWith('Result:')) {
      cleanResult = cleanResult.substring(7).trim();
    }

    return {
      content: cleanResult,
      type: 'text',
      showInput: false,
      collapsed: false
    };
  }

  // Handle wrapped response pattern (common in MCP responses)
  if (result.content && typeof result.content === 'object') {
    return formatMCPOutput(toolName, result.content, input, options);
  }

  // Generic list/array formatting
  if (Array.isArray(result)) {
    return formatGenericArray(result, toolName, { showInput, input, maxLength });
  }

  // Generic room/team/group formatting
  if (hasRoomOrGroupPattern(result)) {
    return formatRoomOrGroupData(result, { showInput, input, maxLength });
  }

  // Generic user/employee data formatting  
  if (hasUserDataPattern(result)) {
    return formatUserData(result, { showInput, input, maxLength });
  }

  // Generic HTML content pattern detection  
  if (hasHtmlContentPattern(result)) {
    return formatHtmlContent(result.content, { showInput, input, maxLength });
  }

  // Generic status wrapper pattern detection
  if (hasStatusWrapperPattern(result)) {
    return formatStatusWrapper(result, { showInput, input, maxLength });
  }

  // Generic multi-repository pattern detection
  if (hasRepositoryPattern(result)) {
    return formatRepositoryData(result, { showInput, input, maxLength });
  }

  // Generic team/user hierarchy pattern detection
  if (hasTeamHierarchyPattern(result)) {
    return formatTeamHierarchy(result, { showInput, input, maxLength });
  }

  // Generic time-series/metrics pattern detection
  if (hasMetricsPattern(result)) {
    return formatMetrics(result, { showInput, input, maxLength });
  }

  // Handle string results
  if (typeof result === 'string') {
    // Convert URLs to markdown links
    result = convertUrlsToMarkdownLinks(result);

    // Check if it's a large text output that should be collapsed
    const lines = result.split('\n');
    const shouldCollapse = result.length > 500 || lines.length > 5;

    let content = result;
    if (shouldCollapse && fadeLastLine && lines.length > 3) {
      const visibleLines = lines.slice(0, 3);
      const remainingLines = lines.slice(3);
      const lastVisibleLine = visibleLines[visibleLines.length - 1];
      const otherLines = visibleLines.slice(0, -1);

      content = otherLines.join('\n') + '\n' +
        `<span style="opacity: 0.6; font-style: italic;">${lastVisibleLine}</span>\n` +
        `<span style="opacity: 0.4; font-size: 0.9em;">... ${remainingLines.length} more lines ...</span>`;
    }

    // Check if it's JSON-like
    if ((result.startsWith('{') || result.startsWith('[')) && result.length > 2) {
      try {
        const parsed = JSON.parse(result);
        return formatObject(parsed, { maxLength, showInput, input, compact, defaultCollapsed, toolSummary, fadeLastLine });
      } catch (e) {
        // Not JSON, treat as plain text
        return {
          content,
          type: 'text',
          showInput: false,
          collapsed: shouldCollapse && defaultCollapsed,
          summary: shouldCollapse ? `Output (${result.length} chars, ${lines.length} lines)` : undefined
        };
      }
    }
    return {
      content: showInput ? `Input: ${formatInput(input)}\n\nResult:\n${content}` : content,
      type: 'text',
      showInput: false,
      collapsed: shouldCollapse && defaultCollapsed,
      summary: shouldCollapse ? `Output (${result.length} chars, ${lines.length} lines)` : undefined
    };
  }

  // Handle object/array results
  if (typeof result === 'object' && result !== null) {
    return formatObject(result, { maxLength, showInput: false, input, compact, defaultCollapsed, toolSummary, fadeLastLine });
  }

  // Handle primitive types
  const stringResult = String(result);
  return {
    content: stringResult,
    type: 'text',
    showInput: false,
    collapsed: false,
    summary: undefined
  };
}

// Generic pattern detection functions (public-safe)
function hasSearchResultsPattern(result: any): boolean {
  return result &&
    typeof result === 'object' &&
    result.content &&
    Array.isArray(result.content.results);
}

function hasRoomOrGroupPattern(result: any): boolean {
  return result && (
    (result.rooms && Array.isArray(result.rooms)) ||
    (result.data && result.data.groups && Array.isArray(result.data.groups)) ||
    (Array.isArray(result) && result[0] && (result[0].teamName || result[0].name || result[0].roomName))
  );
}

function hasUserDataPattern(result: any): boolean {
  return result && typeof result === 'object' &&
    (result.login || result.name || result.ownerLogin) &&
    (result.department_name || result.job_title || result.managerLogin);
}

function hasHtmlContentPattern(result: any): boolean {
  return result &&
    typeof result === 'object' &&
    result.content &&
    typeof result.content.content === 'string';
}

function hasStatusWrapperPattern(result: any): boolean {
  return result &&
    typeof result === 'object' &&
    result.status === 'success' &&
    result.data;
}

function hasRepositoryPattern(result: any): boolean {
  return result &&
    (result.gitRepositories ||
      (Array.isArray(result) && result[0]?.repositoryName) ||
      (result.repositories && Array.isArray(result.repositories)));
}

function hasTeamHierarchyPattern(result: any): boolean {
  return result &&
    ((Array.isArray(result.data) && result.data[0]?.members && result.data[0]?.owners) ||
      (Array.isArray(result) && result[0]?.members && result[0]?.owners) ||
      (result.teams && Array.isArray(result.teams)));
}

function hasMetricsPattern(result: any): boolean {
  return result &&
    typeof result === 'object' &&
    (result.totalCount || result.metrics || result.data?.totalCount);
}

// Generic formatting functions (public-safe)
function formatShellCommand(result: string, options: any): FormattedOutput {
  const { input, toolSummary, fadeLastLine = false, defaultCollapsed = true } = options;
  const commandExecuted = toolSummary?.replace(/^\$ /, '') || input?.command || '';

  // Extract command from result if not in input (fallback)
  let displayCommand = commandExecuted;
  if (!displayCommand && result.startsWith('$ ')) {
    const firstLine = result.split('\n')[0];
    displayCommand = firstLine.substring(2); // Remove '$ ' prefix
  }

  const lines = result.split('\n');
  const lineCount = lines.length;
  const shouldCollapse = lineCount > 5;

  let content = result;
  if (shouldCollapse && fadeLastLine && lines.length > 3) {
    const visibleLines = lines.slice(0, 3);
    const remainingLines = lines.slice(3);
    const lastVisibleLine = visibleLines[visibleLines.length - 1];
    const otherLines = visibleLines.slice(0, -1);

    content = otherLines.join('\n') + '\n' +
      `<span style="opacity: 0.6; font-style: italic;">${lastVisibleLine}</span>\n` +
      `<span style="opacity: 0.4; font-size: 0.9em;">... ${remainingLines.length} more lines ...</span>`;
  }

  // Create a more descriptive summary that includes the command
  let summaryText: string | undefined = undefined;
  if (shouldCollapse && displayCommand) {
    summaryText = `$ ${displayCommand} - Output (${lineCount} lines, ${result.length} chars)`;
  } else if (shouldCollapse) {
    summaryText = `Command output (${lineCount} lines)`;
  }

  return {
    content,
    type: 'text',
    showInput: false,
    collapsed: shouldCollapse && defaultCollapsed,
    summary: shouldCollapse && displayCommand
      ? `${toolSummary || `$ ${displayCommand}`} - Output (${lineCount} lines, ${result.length} chars)`
      : shouldCollapse ? `Command output (${lineCount} lines)` : undefined
  };
}

function formatSequentialThinking(result: any, input: any, options: any): FormattedOutput {
  const thinkingContent = result?.thought || input?.thought || result?.content || '';
  const thoughtNumber = Number(result?.thoughtNumber || input?.thoughtNumber || 1);
  const totalThoughts = Number(result?.totalThoughts || input?.totalThoughts || 1);
  const nextThoughtNeeded = result?.nextThoughtNeeded;

  const statusSuffix = nextThoughtNeeded ? '\n\n_Continuing..._' : '';
  const content = `🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${thinkingContent}${statusSuffix}`;

  return {
    content,
    type: 'text',
    collapsed: false,
    summary: `Thought ${thoughtNumber}/${totalThoughts}`
  };
}

function formatStatusWrapper(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;

  // Extract the actual data from status wrapper
  const data = result.data;

  // If data is an array with team/user-like objects
  if (Array.isArray(data) && data[0]?.name) {
    const items = data.map(item => formatGenericItem(item)).join('\n\n');
    return {
      content: showInput ? `Input: ${formatInput(input)}\n\nResults:\n\n${items}` : `Results:\n\n${items}`,
      type: 'list',
      showInput,
      collapsed: data.length > 5,
      summary: data.length > 5 ? `${data.length} items` : undefined
    };
  }

  // If data has groups (common pattern)
  if (data.groups && Array.isArray(data.groups)) {
    const groups = data.groups.map(group => formatGenericGroup(group)).join('\n\n');
    return {
      content: showInput ? `Input: ${formatInput(input)}\n\nGroups:\n\n${groups}` : `Groups:\n\n${groups}`,
      type: 'list',
      showInput,
      collapsed: data.groups.length > 5
    };
  }

  // Fallback to JSON for complex status wrappers
  return formatObject(data, { maxLength: maxLength, showInput, input, compact: false, defaultCollapsed: true });
}

function formatRepositoryData(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input } = options;

  const repos = result.gitRepositories || result.repositories || result;
  if (!Array.isArray(repos)) return formatObject(result, { maxLength: options.maxLength, showInput, input, compact: false, defaultCollapsed: true });

  const repoList = repos.map((repo: any) => {
    let info = `• **${repo.repositoryName || repo.name}**`;
    if (repo.repositoryPath || repo.path) {
      info += ` (${repo.repositoryPath || repo.path})`;
    }
    info += '\n';

    // Generic status formatting
    if (repo.gitStatus || repo.status) {
      const status = repo.gitStatus || repo.status;
      const statusPreview = typeof status === 'string' ?
        status.split('\n').slice(0, 2).join(' | ') : String(status);
      info += `  Status: ${statusPreview}\n`;
    }

    // Generic diff/changes formatting
    if (repo.gitDiff || repo.diff || repo.changes) {
      const diff = repo.gitDiff || repo.diff || repo.changes;
      if (typeof diff === 'string') {
        if (diff.includes('too large')) {
          info += `  Changes: Large changes (too large to display)\n`;
        } else if (diff.length > 100) {
          info += `  Changes: ${diff.split('\n').length} lines\n`;
        } else if (diff.trim()) {
          info += `  Changes: Present\n`;
        } else {
          info += `  Changes: None\n`;
        }
      }
    }

    return info;
  }).join('\n');

  return {
    content: showInput ? `Input: ${formatInput(input)}\n\nRepositories (${repos.length}):\n\n${repoList}` : `Repositories (${repos.length}):\n\n${repoList}`,
    type: 'list',
    showInput,
    collapsed: repos.length > 5,
    summary: repos.length > 5 ? `${repos.length} repositories` : undefined
  };
}

function formatTeamHierarchy(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input } = options;

  const teams = result.data || result.teams || result;
  if (!Array.isArray(teams)) return formatObject(result, { maxLength: options.maxLength, showInput, input, compact: false, defaultCollapsed: true });

  const teamList = teams.map((team: any) => {
    let info = `• **${team.teamName || team.name}**\n`;

    if (team.description) {
      info += `  Description: ${team.description}\n`;
    }

    // Generic member/owner formatting
    if (team.members) {
      const memberCount = typeof team.members === 'string' ?
        team.members.split(' ').length :
        Array.isArray(team.members) ? team.members.length : 0;
      info += `  Members: ${memberCount} member${memberCount === 1 ? '' : 's'}\n`;
    }

    if (team.owners) {
      const ownerCount = typeof team.owners === 'string' ?
        team.owners.split(' ').length :
        Array.isArray(team.owners) ? team.owners.length : 0;
      info += `  Owners: ${ownerCount} owner${ownerCount === 1 ? '' : 's'}\n`;
    }

    return info;
  }).join('\n');

  return {
    content: showInput ? `Input: ${formatInput(input)}\n\nTeams (${teams.length}):\n\n${teamList}` : `Teams (${teams.length}):\n\n${teamList}`,
    type: 'list',
    showInput,
    collapsed: teams.length > 5
  };
}

function formatMetrics(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input } = options;

  // Extract metrics from various common patterns
  const metrics = result.totalCount || result.metrics || result.data?.totalCount || result;

  if (typeof metrics === 'object' && metrics) {
    const metricsList = Object.entries(metrics)
      .filter(([key, value]) => typeof value === 'object' && value !== null)
      .map(([category, data]: [string, any]) => {
        const categoryName = category.replace(/_/g, ' ').toLowerCase()
          .replace(/\b\w/g, l => l.toUpperCase()); // Title case

        let categoryInfo = `• **${categoryName}**\n`;

        // Format numeric metrics generically
        Object.entries(data).forEach(([metricName, value]) => {
          if (typeof value === 'number') {
            const displayName = metricName.replace(/([A-Z])/g, ' $1')
              .replace(/^./, str => str.toUpperCase());
            categoryInfo += `  ${displayName}: ${value}\n`;
          }
        });

        return categoryInfo;
      }).join('\n');

    return {
      content: showInput ? `Input: ${formatInput(input)}\n\nMetrics Summary:\n\n${metricsList}` : `Metrics Summary:\n\n${metricsList}`,
      type: 'table',
      showInput,
      collapsed: Object.keys(metrics).length > 5
    };
  }

  return formatObject(result, { maxLength: options.maxLength, showInput, input, compact: false, defaultCollapsed: true });
}

function formatGenericArray(arr: any[], toolName: string, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;

  if (!arr.length) {
    // Extract query information from input to show what was searched
    let queryInfo = '';
    if (input) {
      // For workspace search
      if (input.searchQuery) {
        queryInfo = `"${input.searchQuery}"`;
        if (input.searchType) queryInfo += ` (${input.searchType})`;
        if (input.globPatterns?.length) queryInfo += `\n**Files:** ${input.globPatterns.join(', ')}`;
      }
      // For generic searches
      else if (input.query) {
        queryInfo = `"${input.query}"`;
      }
      // Fallback: show formatted input
      else if (showInput) {
        queryInfo = `**Search parameters:**\n${formatInput(input)}`;
      }
    }

    const message = queryInfo ? `${queryInfo}\n\nNo results found` : 'No results found';
    return { content: message, type: 'text', collapsed: false };
  }

  // Format based on common patterns
  const items = arr.map((item, index) => {
    if (typeof item === 'object' && item) {
      return formatGenericItem(item);
    }
    return `${index + 1}. ${item}`;
  }).join('\n\n');

  const content = showInput ? `Input: ${formatInput(input)}\n\nResults (${arr.length}):\n\n${items}` : `Results (${arr.length}):\n\n${items}`;

  return {
    content: content.length > maxLength ? content.substring(0, maxLength) + '\n...\n[Results truncated]' : content,
    type: 'list',
    showInput,
    collapsed: arr.length > 10,
    summary: arr.length > 10 ? `${arr.length} items` : undefined
  };
}

function formatRoomOrGroupData(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;

  // Extract rooms or groups
  const rooms = result.rooms || (result.data && result.data.groups) || (Array.isArray(result) ? result : []);

  if (!rooms.length) {
    return { content: 'No rooms or groups found', type: 'text', collapsed: false };
  }

  const roomList = rooms.map((room: any) => {
    let info = `• **${room.name || room.details?.label || room.teamName || 'Unnamed'}**\n`;

    if (room.description || room.details?.description) {
      info += `  Description: ${room.description || room.details.description}\n`;
    }

    // Add type-specific info
    if (room.enableSprints !== undefined) {
      info += `  Features: Sprints ${room.enableSprints ? '✓' : '✗'}, Kanban ${room.enableKanban ? '✓' : '✗'}\n`;
    }

    if (room.members && Array.isArray(room.members)) {
      info += `  Members: ${room.members.length} member${room.members.length === 1 ? '' : 's'}\n`;
    }

    if (room.owners && Array.isArray(room.owners)) {
      info += `  Owners: ${room.owners.length} owner${room.owners.length === 1 ? '' : 's'}\n`;
    }

    if (room.id || room.details?.ticketyId) {
      info += `  ID: ${room.id || room.details.ticketyId}\n`;
    }

    return info;
  }).join('\n');

  const title = result.rooms ? 'Taskei Rooms' : result.data?.groups ? 'Resolver Groups' : 'Teams';
  const content = showInput ? `Input: ${formatInput(input)}\n\n${title} (${rooms.length}):\n\n${roomList}` : `${title} (${rooms.length}):\n\n${roomList}`;

  return {
    content: content.length > maxLength ? content.substring(0, maxLength) + '\n...\n[Content truncated]' : content,
    type: 'list',
    showInput,
    collapsed: rooms.length > 10
  };
}

function formatUserData(result: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input } = options;

  // Handle both direct user data and wrapped responses
  const userData = result.content || result;

  let userInfo = '';

  if (userData.name || userData.ownerName) {
    userInfo += `**${userData.name || userData.ownerName}** (${userData.login || userData.ownerLogin})\n\n`;
  }

  // Add role/position info
  if (userData.job_title) {
    userInfo += `• **Position:** ${userData.job_title}\n`;
  }
  if (userData.department_name) {
    userInfo += `• **Department:** ${userData.department_name}\n`;
  }
  if (userData.manager || userData.managerName || userData.managerLogin) {
    const managerInfo = userData.manager ? userData.manager.login : (userData.managerName || userData.managerLogin);
    userInfo += `• **Manager:** ${userData.managerName || managerInfo} (${userData.managerLogin || managerInfo})\n`;
  }

  // Add location info
  if (userData.building) {
    userInfo += `• **Location:** ${userData.building}\n`;
  }
  if (userData.city && userData.country) {
    userInfo += `• **City:** ${userData.city}, ${userData.country}\n`;
  }

  // Add tenure info
  if (userData.total_tenure_formatted) {
    userInfo += `• **Tenure:** ${userData.total_tenure_formatted}\n`;
  }

  // Add contact info
  if (userData.email) {
    userInfo += `• **Email:** ${userData.email}\n`;
  }

  const content = showInput ? `Input: ${formatInput(input)}\n\nUser Information:\n\n${userInfo}` : `User Information:\n\n${userInfo}`;

  return {
    content,
    type: 'list',
    showInput,
    collapsed: false
  };
}

function formatGenericItem(item: any): string {
  let info = `• **${item.name || item.title || item.displayName || 'Unnamed'}**\n`;

  if (item.description) {
    info += `  Description: ${item.description}\n`;
  }

  // Format common fields generically
  ['id', 'type', 'status', 'owner', 'created', 'modified'].forEach(field => {
    if (item[field]) {
      const displayName = field.charAt(0).toUpperCase() + field.slice(1);
      let value = item[field];

      // Format timestamps
      if (typeof value === 'number' && value > 1000000000) {
        value = new Date(value * 1000).toLocaleDateString();
      }

      info += `  ${displayName}: ${value}\n`;
    }
  });

  return info;
}

function formatGenericGroup(group: any): string {
  const name = group.details?.label || group.name || 'Unnamed Group';
  const description = group.details?.description || group.description || '';

  let info = `• **${name}**\n`;
  if (description) {
    info += `  Description: ${description}\n`;
  }
  if (group.id || group.details?.ticketyId) {
    info += `  ID: ${group.id || group.details.ticketyId}\n`;
  }

  return info;
}

// Helper to extract query from search results content
function extractQueryFromSearchResults(content: any): string | null {
  // Try to parse search metadata from various formats
  if (typeof content === 'string') {
    // Look for "Query: X" pattern in search results
    const queryMatch = content.match(/Query:\s*"([^"]+)"/);
    if (queryMatch) return queryMatch[1];

    // Look for search results header with query
    const headerMatch = content.match(/Search results for "([^"]+)"/i);
    if (headerMatch) return headerMatch[1];
  }

  // Check if it's in the metadata
  if (content.query) return content.query;
  if (content.searchQuery) return content.searchQuery;

  return null;
}

function formatSearchResults(searchContent: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;
  const results = searchContent.results || [];

  // Extract query information for display (needed for both success and no-results cases)
  const query = input?.query || input?.searchQuery || extractQueryFromSearchResults(searchContent) || '';
  const domain = input?.domain || 'ALL';

  if (!results.length) {
    // Show query information BEFORE no results message
    let queryInfo = '';
    if (query) {
      queryInfo = `🔍 "${query}"${domain !== 'ALL' ? ` in **${domain}**` : ''}`;
    } else if (showInput && input) {
      queryInfo = `**Search parameters:**\n${formatInput(input)}`;
    }

    const message = queryInfo ? `${queryInfo}\n\nNo results found` : 'No results found';

    return {
      content: message,
      type: 'search_results',
      collapsed: false
    };
  }

  // Create a summary with query information
  let summaryPrefix = '';
  if (query) {
    summaryPrefix = `🔍 "${query}"${domain !== 'ALL' ? ` in ${domain}` : ''}\n\n`;
  }

  // Create results summary
  const summary = `Found ${results.length} result${results.length === 1 ? '' : 's'}${searchContent.totalResults ? ` (${searchContent.totalResults} total)` : ''}`;

  // Format results in a more readable way
  let formattedResults = '';

  // Handle different result formats
  if (results[0].filepath) {
    // Workspace search format
    formattedResults = results.map((result: any, index: number) => {
      const lineCount = result.lines ? result.lines.length : 0;
      return `${index + 1}. ${result.filepath}\n   ${lineCount} matching line${lineCount === 1 ? '' : 's'}`;
    }).join('\n\n');
  } else if (results[0].url) {
    // Internal search format  
    formattedResults = results.map((result: any, index: number) => {
      const domain = result.domain ? `[${result.domain}]` : '';
      const date = result.modificationDate ? new Date(result.modificationDate).toLocaleDateString() : '';
      const title = result.displayTitle || 'Untitled';
      const url = result.url || '';

      // Create markdown link for the title
      const titleLink = url ? `[${title}](${url})` : title;

      return `${index + 1}. ${titleLink} ${domain}\n   ${date ? `Modified: ${date}` : ''}`;
    }).join('\n\n');
  } else {
    // Generic object format
    formattedResults = JSON.stringify(results, null, 2);
  }

  const content = showInput
    ? `Input: ${formatInput(input)}\n\n${summaryPrefix}${summary}\n\n${formattedResults}`
    : `${summaryPrefix}${summary}\n\n${formattedResults}`;

  return {
    content: content.length > maxLength ? content.substring(0, maxLength) + '\n...\n[Results truncated]' : content,
    type: 'search_results',
    showInput,
    summary,
    collapsed: true,
    metadata: { resultCount: results.length, totalResults: searchContent.totalResults }
  };
}

function formatHtmlContent(contentObj: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;
  const htmlContent = contentObj.content || '';

  // Extract meaningful info from HTML
  const summary = `HTML content (${htmlContent.length} chars)`;

  // For HTML content, provide a much shorter preview
  const preview = htmlContent.substring(0, 200) + (htmlContent.length > 200 ? '...' : '');

  const content = showInput
    ? `Input: ${formatInput(input)}\n\nHTML Content Preview:\n${preview}\n\n[Full content available in collapsed view]`
    : `HTML Content Preview:\n${preview}\n\n[Full content available in collapsed view]`;

  return {
    content: htmlContent.length > maxLength ? htmlContent.substring(0, maxLength) + '\n...\n[Content truncated]' : htmlContent,
    type: 'html_content',
    showInput,
    summary,
    collapsed: true
  };
}

function formatObject(obj: any, options: { maxLength: number; showInput: boolean; input?: any; compact: boolean; defaultCollapsed: boolean; toolSummary?: string; fadeLastLine?: boolean }): FormattedOutput {
  const { maxLength, showInput, input, compact, defaultCollapsed, toolSummary, fadeLastLine = false } = options;

  try {
    const jsonString = JSON.stringify(obj, null, compact ? 0 : 2);
    const lines = jsonString.split('\n');
    const shouldCollapse = jsonString.length > 1000 || lines.length > 5;

    let content = jsonString;
    if (shouldCollapse && fadeLastLine && lines.length > 3) {
      const visibleLines = lines.slice(0, 3);
      const remainingLines = lines.slice(3);
      const lastVisibleLine = visibleLines[visibleLines.length - 1];
      const otherLines = visibleLines.slice(0, -1);

      content = otherLines.join('\n') + '\n' +
        `<span style="opacity: 0.6; font-style: italic;">${lastVisibleLine}</span>\n` +
        `<span style="opacity: 0.4; font-size: 0.9em;">... ${remainingLines.length} more lines ...</span>`;
    }

    // Truncate if too long
    const truncated = content.length > maxLength
      ? content.substring(0, maxLength) + '\n...\n[Output truncated]'
      : content;

    const finalContent = showInput
      ? `Input: ${formatInput(input)}\n\nResult:\n${truncated}`
      : truncated;

    return {
      content: finalContent,
      type: 'json',
      showInput,
      collapsed: shouldCollapse && defaultCollapsed,
      summary: shouldCollapse ? `${toolSummary ? `${toolSummary} - ` : ''}JSON object (${lines.length} lines, ${Object.keys(obj).length} properties)` : toolSummary
    };
  } catch (e) {
    return { content: String(obj), type: 'text', showInput, collapsed: false };
  }
}

function formatInput(input: any): string {
  return input ? JSON.stringify(input, null, 2) : 'None';
}

// Generic tool summary generator
function createToolSummary(toolName: string, input: any): string {
  if (!input || typeof input !== 'object') return '';

  const cleanToolName = toolName.replace(/^mcp_/, '');

  // Tool-specific parameter extraction
  const toolSummaries: Record<string, (input: any) => string> = {
    'run_shell_command': (input) => input.command ? `$ ${input.command}` : '',
    'WorkspaceSearch': (input) => {
      const query = input.searchQuery || '';
      const type = input.searchType || 'contentLiteral';
      return query ? `"${query}" (${type})` : '';
    },
    'InternalSearch': (input) => {
      const query = input.query || '';
      const domain = input.domain || 'ALL';
      const domainDisplay = domain !== 'ALL' ? ` in ${domain}` : '';
      return query ? `"${query}"${domainDisplay}` :
        domain !== 'ALL' ? `Search in ${domain}` : '';
    },
    'ReadInternalWebsites': (input) => {
      const inputs = input.inputs || [];
      if (inputs.length === 1) return `${inputs[0]}`;
      if (inputs.length > 1) return `${inputs.length} URLs`;
      return '';
    },
    'InternalCodeSearch': (input) => {
      const query = input.query || '';
      const searchType = input.searchType || 'code';
      return query ? `"${query}" (${searchType})` : '';
    },
    'TicketingReadActions': (input) => {
      const action = input.action || '';
      if (action === 'search-tickets' && input.input?.fullText) {
        return `${action}: "${input.input.fullText}"`;
      }
      if (action === 'get-ticket' && input.input?.ticketId) {
        return `${action}: ${input.input.ticketId}`;
      }
      return action;
    },
    'TaskeiListTasks': (input) => {
      const filters: string[] = [];
      if (input.name?.value) filters.push(`name: "${input.name.value}"`);
      if (input.assignee) filters.push(`assignee: ${input.assignee}`);
      if (input.status) filters.push(`status: ${input.status}`);
      return filters.length ? filters.join(', ') : 'list tasks';
    }
  };

  // Try tool-specific summary first
  const specificSummary = toolSummaries[cleanToolName];
  if (specificSummary) {
    const summary = specificSummary(input);
    if (summary) return summary;
  }

  // Generic fallback: extract most likely meaningful parameters
  const meaningfulParams = [
    'query', 'searchQuery', 'command', 'action', 'name', 'id', 'taskId', 'ticketId',
    'pipelineName', 'environmentName', 'deploymentId', 'resourceName', 'username',
    'teamName', 'groupName', 'roomId', 'conversationId'
  ];

  // Find the first meaningful parameter with a value
  for (const param of meaningfulParams) {
    if (input[param] && typeof input[param] === 'string') {
      return `${param}: ${input[param]}`;
    }
  }

  // If no meaningful params found, try to create a summary from available keys
  const keys = Object.keys(input).slice(0, 3);
  if (keys.length > 0) {
    return keys.join(', ');
  }

  return '';
}

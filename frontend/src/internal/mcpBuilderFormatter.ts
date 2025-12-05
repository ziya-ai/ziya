/**
 * Amazon Builder MCP specific formatting enhancements
 * This file contains Amazon-internal knowledge and should NOT be committed to public branches
 */

import { FormattedOutput, registerInternalFormatter, registerToolHeaderEnhancer } from '../utils/mcpFormatter';

// Amazon-specific tool knowledge
const AMAZON_TOOL_MAPPINGS = {
  'mcp_SearchAcronymCentral': 'acronym',
  'mcp_TicketingReadActions': 'ticketing',
  'mcp_WorkspaceSearch': 'workspace_search',
  'WorkspaceSearch': 'workspace_search',
  'mcp_TaskeiGetRooms': 'taskei_rooms',
  'mcp_TaskeiListTasks': 'taskei_tasks',
  'mcp_OncallReadActions': 'oncall',
  'mcp_ApolloReadActions': 'apollo',
  'mcp_GetSasRisks': 'sas_risks',
  'mcp_WorkspaceGitDetails': 'workspace_git',
  'mcp_GetPipelineHealth': 'pipeline_health',
  'mcp_InternalCodeSearch': 'code_search',
  'mcp_ReadInternalWebsites': 'website_content',
  'mcp_InternalSearch': 'internal_search',
  'mcp_InternalCodeSearch': 'code_search'
};

const AMAZON_FIELD_LABELS = {
  'ownerLogin': 'Owner',
  'managerLogin': 'Manager',
  'teamName': 'Team',
  'resolverGroup': 'Resolver Group',
  'environmentName': 'Environment',
  'pipelineName': 'Pipeline'
};

/**
 * Enhance display headers for Amazon-internal tools
 */
function enhanceAmazonToolHeader(toolName: string, baseHeader: string, args: Record<string, any>): string | null {
  const toolType = AMAZON_TOOL_MAPPINGS[toolName as keyof typeof AMAZON_TOOL_MAPPINGS];

  if (!toolType) return null; // Let generic formatter handle it

  switch (toolType) {
    case 'internal_search':
      if (args.query) {
        const domain = args.domain ? ` (${args.domain})` : '';
        return `Internal Search: ${args.query}${domain}`;
      }
      break;

    case 'code_search':
      if (args.query) {
        const searchType = args.searchType ? ` [${args.searchType}]` : '';
        return `Code Search: ${args.query}${searchType}`;
      }
      break;

    case 'acronym':
      if (args.acronym) {
        return `Acronym: ${args.acronym}`;
      }
      break;

    case 'workspace_search':
      if (args.searchQuery) {
        const type = args.searchType ? ` [${args.searchType}]` : '';
        return `Workspace Search: ${args.searchQuery}${type}`;
      }
      break;

    case 'website_content':
      if (args.inputs) {
        const url = typeof args.inputs === 'string' ? args.inputs : JSON.stringify(args.inputs);
        const truncated = url.length > 50 ? url.substring(0, 50) + '...' : url;
        return `Read: ${truncated}`;
      }
      break;
  }

  return null; // Let generic formatter handle it
}

function formatBuilderMcpOutput(toolName: string, result: any, options: any): FormattedOutput | null {
  const toolType = AMAZON_TOOL_MAPPINGS[toolName as keyof typeof AMAZON_TOOL_MAPPINGS];

  if (!toolType) {
    return null; // Let generic formatter handle it
  }

  switch (toolType) {
    case 'acronym':
      return formatAmazonAcronym(result, options);
    case 'ticketing':
      return formatAmazonTicketing(result, options);
    case 'taskei_rooms':
      return formatAmazonTaskeiRooms(result, options);
    case 'oncall':
      return formatAmazonOncall(result, options);
    case 'apollo':
      return formatAmazonApollo(result, options);
    case 'sas_risks':
      return formatAmazonSasRisks(result, options);
    case 'workspace_git':
      return formatAmazonWorkspaceGit(result, options);
    case 'code_search':
      return formatAmazonCodeSearch(result, options);
    case 'workspace_search':
      return formatAmazonWorkspaceSearch(result, options);
    case 'internal_search':
      return formatAmazonInternalSearch(result, options);
    default:
      return null;
  }
}

/**
 * Format file size in human-readable format
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Truncate snippet to max lines
 */
function truncateSnippet(snippet: string, maxLines: number): { text: string; truncated: boolean } {
  const lines = snippet.split('\n');
  if (lines.length <= maxLines) {
    return { text: snippet, truncated: false };
  }
  
  return {
    text: lines.slice(0, maxLines).join('\n'),
    truncated: true,
  };
}

/**
 * Add line numbers to snippet
 */
function addLineNumbers(snippet: string, startLine: number): string {
  const lines = snippet.split('\n');
  const maxLineNum = startLine + lines.length - 1;
  const padding = maxLineNum.toString().length;
  
  return lines
    .map((line, idx) => {
      const lineNum = (startLine + idx).toString().padStart(padding, ' ');
      return `${lineNum} | ${line}`;
    })
    .join('\n');
}

/**
 * Highlight matching terms in snippet (bold markdown)
 */
function highlightCodeMatches(snippet: string, matchingParts: string[] = []): string {
  if (matchingParts.length === 0) return snippet;
  
  let highlighted = snippet;
  matchingParts.forEach(part => {
    // Use word boundaries to avoid partial matches in code
    const regex = new RegExp(`\\b(${part})\\b`, 'gi');
    highlighted = highlighted.replace(regex, '**$1**');
  });
  
  return highlighted;
}

function formatAmazonCodeSearch(result: any, _options: any): FormattedOutput | null {
  const { input } = _options;

  // Extract search parameters
  let effectiveInput = input || {};

  // Handle tool_input wrapper
  if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'string') {
    try {
      effectiveInput = JSON.parse(effectiveInput.tool_input);
    } catch (e) {
      console.error('📋 CODE_SEARCH: Failed to parse tool_input:', e);
      effectiveInput = input || {};
    }
  } else if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'object') {
    effectiveInput = effectiveInput.tool_input;
  }

  const searchQuery = effectiveInput.query || '';
  const searchType = effectiveInput.searchType || 'code';

  // Handle both direct result and wrapped content
  const searchData = result.content || result;
  const results = searchData.results || [];
  const totalHits = searchData.totalHits || {};
  const paginationInfo = searchData.paginationInfo || '';

  if (!results.length) {
    const queryDisplay = searchQuery || '(empty query)';
    const searchSummary = `No code matches found for **"${queryDisplay}"** [${searchType}]`;

    return {
      content: searchSummary,
      type: 'search_results',
      collapsed: false,
      summary: searchSummary,
      // Don't include hierarchicalResults for empty results
      // to keep the output clean
    };
  }

  // Create detailed summary
  const totalCount = totalHits.count || 0;
  const relation = totalHits.relation || '';
  const queryInfo = searchQuery ? `🔍 "${searchQuery}" [${searchType}]` : '';
  const resultSummary = `${results.length} result${results.length === 1 ? '' : 's'}${totalCount ? ` of ${totalCount.toLocaleString()} ${relation}` : ''}`;
  const summary = queryInfo ? `${queryInfo} - ${resultSummary}` : resultSummary;

  // Create hierarchical results for two-level collapsible rendering
  const hierarchicalResults = results.map((result: any, index: number) => {
    const repo = result.repository?.name || 'Unknown';
    const filePath = result.filePath || '';
    const matches = result.matches || 0;
    const fileSize = formatFileSize(result.fileSizeBytes || 0);
    const branches = result.branches?.join(', ') || 'mainline';
    const matchingParts = result.matchingQueryParts || [];

    // Build title with metadata
    const title = `${index + 1}. ${repo}/${filePath} (${matches} match${matches === 1 ? '' : 'es'}, ${fileSize})`;

    // Handle truncated files
    if (result.snippetTruncationReason) {
      const reason = result.snippetTruncationReason;
      return {
        title,
        content: `⚠️ **${reason}** - No snippets available\n\n**Branch:** ${branches}\n\nView on [code.amazon.com](https://code.amazon.com/packages/${repo}/blobs/${branches}/--/${filePath})`,
        language: 'text',
        metadata: {
          repo,
          filePath,
          truncated: true,
          reason
        }
      };
    }

    // Build content from snippets
    const snippets = result.snippets || [];
    if (snippets.length === 0) {
      return {
        title,
        content: '*No snippets available*',
        language: 'text',
        metadata: { repo, filePath, matches }
      };
    }

    // Format snippets with line numbers and highlighting
    const snippetContent = snippets.map((snippet: any, snippetIdx: number) => {
      let snippetText = snippet.snippet || '';
      const startLine = snippet.startLineNumber || 1;

      // Truncate long snippets
      const { text, truncated } = truncateSnippet(snippetText, 20);
      snippetText = text;

      // Highlight matches
      snippetText = highlightCodeMatches(snippetText, matchingParts);

      // Add line numbers
      snippetText = addLineNumbers(snippetText, startLine);

      let header = snippets.length > 1 ? `**Snippet ${snippetIdx + 1}** (Line ${startLine}):\n\n` : '';
      let footer = truncated ? '\n\n*... (truncated, showing first 20 lines)*' : '';

      return `${header}${snippetText}${footer}`;
    }).join('\n\n---\n\n');

    // Detect language from file extension
    const ext = filePath.split('.').pop()?.toLowerCase() || '';
    const langMap: { [key: string]: string } = {
      'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
      'py': 'python', 'rb': 'ruby', 'php': 'php', 'java': 'java', 'go': 'go',
      'rs': 'rust', 'cpp': 'cpp', 'c': 'c', 'cs': 'csharp', 'css': 'css',
      'html': 'html', 'xml': 'xml', 'json': 'json', 'md': 'markdown',
      'sh': 'bash', 'yml': 'yaml', 'yaml': 'yaml', 'sql': 'sql'
    };
    const language = langMap[ext] || 'text';

    return {
      title,
      content: snippetContent,
      language,
      metadata: {
        repo,
        filePath,
        matches,
        branches,
        fileSize,
        snippetCount: snippets.length
      }
    };
  });

  const shouldCollapse = results.length > 3;

  // Add pagination info if available
  let fullSummary = summary;
  if (paginationInfo) {
    fullSummary += `\n\n*${paginationInfo}*`;
  }

  // For code search, when collapsed, show only summary + first few titles
  // The full hierarchicalResults should be rendered separately by the UI
  const contentForToolBlock = shouldCollapse
    ? `${summary}\n\n**Preview (showing ${Math.min(3, results.length)} of ${results.length}):**\n` +
      hierarchicalResults.slice(0, 3).map(r => `• ${r.title}`).join('\n') +
      (results.length > 3 ? `\n\n*Expand for ${results.length - 3} more results*` : '')
    : fullSummary;

  return {
    content: contentForToolBlock,
    type: 'search_results',
    collapsed: shouldCollapse,
    summary: summary,
    hierarchicalResults: shouldCollapse ? hierarchicalResults : undefined
  };
}

function formatAmazonWorkspaceSearch(result: any, _options: any): FormattedOutput | null {
  const { input } = _options;

  // Extract search parameters - handle both direct input and nested in result
  // CRITICAL: input.tool_input can be a JSON string that needs parsing!
  let effectiveInput = input || {};

  // If tool_input exists and is a string, parse it
  if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'string') {
    try {
      effectiveInput = JSON.parse(effectiveInput.tool_input);
      console.log('📋 WORKSPACE_SEARCH: Parsed tool_input from JSON string');
    } catch (e) {
      console.error('📋 WORKSPACE_SEARCH: Failed to parse tool_input:', e);
      effectiveInput = input || {};
    }
  } else if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'object') {
    // tool_input is already an object
    effectiveInput = effectiveInput.tool_input;
  }

  const searchQuery = effectiveInput.searchQuery !== undefined ? effectiveInput.searchQuery : '';
  const searchType = effectiveInput.searchType || effectiveInput.type || 'contentLiteral';
  const contextLines = effectiveInput.contextLines || 0;
  const globPatterns = effectiveInput.globPatterns;

  // Handle both direct result and wrapped content
  const searchData = result.content || result;
  const results = searchData.results || [];
  const totalCount = searchData.totalCount || 0;
  const hasMore = searchData.hasMore || false;

  if (!results.length) {
    // Include query, search type, and context info in a compact format
    const contextInfo = contextLines > 0 ? ` with ${contextLines} context lines` : '';

    // Always include search query information, even if empty
    const queryDisplay = searchQuery || '(empty query)';
    const searchSummary = `No matches found for **"${queryDisplay}"** (${searchType})${contextInfo}`;

    // Add helpful context if glob patterns were used
    const globInfo = globPatterns
      ? ` in patterns: ${Array.isArray(globPatterns) ? JSON.stringify(globPatterns) : globPatterns}`
      : '';

    const fullSummary = searchSummary + globInfo;

    return {
      content: fullSummary,
      type: 'search_results',
      collapsed: false,
      summary: fullSummary
    };
  }

  // Create detailed summary with query information
  const queryInfo = searchQuery ? `Query: **"${searchQuery}"** (${searchType})` : '';
  const resultSummary = `${results.length}${hasMore ? '+' : ''} result${results.length === 1 ? '' : 's'}${totalCount && totalCount !== results.length ? ` of ${totalCount} total` : ''}`;
  const summary = queryInfo ? `${queryInfo} - ${resultSummary}` : resultSummary;

  // Create hierarchical results for two-level collapsible rendering
  const hierarchicalResults = results.map((result: any, index: number) => {
    const matchCount = result.lines?.length || 0;
    const title = `${index + 1}. ${result.filepath} (${matchCount} matching line${matchCount === 1 ? '' : 's'})`;

    let codeContent = '';
    if (result.lines && result.lines.length > 0) {
      // Extract file extension for syntax highlighting
      const ext = result.filepath.split('.').pop()?.toLowerCase() || '';
      const langMap: { [key: string]: string } = {
        'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
        'py': 'python', 'rb': 'ruby', 'php': 'php', 'java': 'java', 'go': 'go',
        'rs': 'rust', 'cpp': 'cpp', 'c': 'c', 'cs': 'csharp', 'css': 'css',
        'html': 'html', 'xml': 'xml', 'md': 'markdown', 'sh': 'bash', 'yml': 'yaml', 'yaml': 'yaml'
      };
      const language = langMap[ext] || 'text';

      codeContent = result.lines.join('\n');

      return {
        title,
        content: codeContent,
        language,
        metadata: {
          filepath: result.filepath,
          matchCount
        }
      };
    } else {
      return {
        title,
        content: 'No content available',
        language: 'text',
        metadata: {
          filepath: result.filepath,
          matchCount: 0
        }
      };
    }
  });

  const shouldCollapse = results.length > 5;

  // Create a more descriptive summary for workspace search
  let summaryText = summary;
  if (searchQuery && shouldCollapse) {
    summaryText = `Search "${searchQuery}" - ${summary}`;
  } else if (shouldCollapse) {
    summaryText = summary;
  }

  return {
    content: summary,
    type: 'search_results',
    collapsed: shouldCollapse,
    summary: summaryText,
    hierarchicalResults
  };
}

function formatAmazonAcronym(result: any, _options: any): FormattedOutput | null {
  if (!result.results?.[0]) {
    return { content: 'No acronym definitions found', type: 'text', collapsed: false };
  }

  const acronym = result.results[0];
  const definitions = Object.entries(acronym.defsUrls || {})
    .map(([def, url]) => `• **${def}**${url ? ` - [Link](${url})` : ''}`)
    .join('\n');

  return {
    content: `**${acronym.acronymName}** Definitions:\n\n${definitions}`,
    type: 'list',
    collapsed: false,
    metadata: { totalResults: result.totalResults }
  };
}

function formatAmazonTicketing(result: any, _options: any): FormattedOutput {
  if (result.status === 'success' && result.data?.groups) {
    const groups = result.data.groups.map((group: any) =>
      `• **${group.details.label}**\n  - ${group.details.description}\n  - ID: ${group.name}`
    ).join('\n\n');

    return {
      content: `Resolver Groups:\n\n${groups}`,
      type: 'list',
      collapsed: result.data.groups.length > 5
    };
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonTaskeiRooms(result: any, _options: any): FormattedOutput {
  if (result.rooms) {
    const rooms = result.rooms.map((room: any) =>
      `• **${room.name}**\n  - ${room.description}\n  - Sprints: ${room.enableSprints ? '✓' : '✗'} | Kanban: ${room.enableKanban ? '✓' : '✗'}`
    ).join('\n\n');

    return {
      content: `Taskei Rooms (${result.rooms.length}):\n\n${rooms}`,
      type: 'list',
      collapsed: result.rooms.length > 10
    };
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonOncall(result: any, _options: any): FormattedOutput {
  if (result.status === 'success' && result.data) {
    const teams = result.data.map((team: any) =>
      `• **${team.teamName}**\n  - ${team.description}\n  - Members: ${team.members.split(' ').length}\n  - Owners: ${team.owners.split(' ').length}`
    ).join('\n\n');

    return {
      content: `On-call Teams:\n\n${teams}`,
      type: 'list',
      collapsed: result.data.length > 5
    };
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonApollo(result: any, _options: any): FormattedOutput {
  if (result.content && result.content.status === 'success' && result.content.data) {
    const data = result.content.data;

    if (data.EnvironmentStageNames) {
      const stages = data.EnvironmentStageNames.map((stage: any) =>
        `• **${stage.Alias || 'No Alias'}**\n  - Environment: ${stage.EnvironmentStageIdentifier.EnvironmentName}\n  - Stage: ${stage.EnvironmentStageIdentifier.Stage}\n  - Owner: ${stage.Owner || 'No owner'}\n  - Modified: ${new Date(stage.DateModified * 1000).toLocaleDateString()}`
      ).join('\n\n');

      return {
        content: `Apollo Environment Stages:\n\n${stages}`,
        type: 'list',
        collapsed: data.EnvironmentStageNames.length > 10
      };
    }
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonSasRisks(result: any, _options: any): FormattedOutput {
  if (result.content && result.content.status === 'success' && result.content.data) {
    const data = result.content.data;

    if (data.ownerLogin) {
      const summary = `**Risk Summary for ${data.ownerName} (${data.ownerLogin})**\nManager: ${data.managerName} (${data.managerLogin})\n\n`;

      const riskTypes = Object.entries(data.totalCount).map(([type, counts]: [string, any]) =>
        `• **${type.replace(/_/g, ' ')}**: ${counts.totalRiskCount} total risks, ${counts.blockingRiskCount} blocking`
      ).join('\n');

      return {
        content: `${summary}${riskTypes}`,
        type: 'list',
        collapsed: false
      };
    }
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonWorkspaceGit(result: any, _options: any): FormattedOutput {
  if (result.gitRepositories) {
    const repos = result.gitRepositories.map((repo: any) => {
      let repoInfo = `• **${repo.repositoryName}** (${repo.repositoryPath})\n`;

      if (repo.gitStatus) {
        const statusLines = repo.gitStatus.split('\n').slice(0, 3);
        repoInfo += `  Status: ${statusLines.join(' | ')}\n`;
      }

      if (repo.gitDiff) {
        if (repo.gitDiff === "Git diff is too large for summarizing details") {
          repoInfo += `  Diff: Large changes present\n`;
        } else if (repo.gitDiff.length > 200) {
          repoInfo += `  Diff: ${repo.gitDiff.split('\n').length} lines of changes\n`;
        } else if (repo.gitDiff.trim()) {
          repoInfo += `  Diff: Changes present\n`;
        } else {
          repoInfo += `  Diff: No changes\n`;
        }
      }

      return repoInfo;
    }).join('\n');

    return {
      content: `Git Status for ${result.gitRepositories.length} repositories:\n\n${repos}`,
      type: 'list',
      collapsed: result.gitRepositories.length > 5
    };
  }

  return { content: JSON.stringify(result, null, 2), type: 'json', collapsed: true };
}

function formatAmazonInternalSearch(result: any, _options: any): FormattedOutput | null {
  const { input } = _options;

  // Extract search parameters
  let effectiveInput = input || {};

  // Handle tool_input wrapper (can be string or object)
  if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'string') {
    try {
      effectiveInput = JSON.parse(effectiveInput.tool_input);
      console.log('📋 INTERNAL_SEARCH: Parsed tool_input from JSON string');
    } catch (e) {
      console.error('📋 INTERNAL_SEARCH: Failed to parse tool_input:', e);
      effectiveInput = input || {};
    }
  } else if (effectiveInput.tool_input && typeof effectiveInput.tool_input === 'object') {
    effectiveInput = effectiveInput.tool_input;
  }

  const searchQuery = effectiveInput.query || '';
  const domain = effectiveInput.domain || 'ALL';
  const sortBy = effectiveInput.sortBy || 'SCORE';

  // Handle both direct result and wrapped content
  const searchData = result.content || result;
  const results = searchData.results || [];
  const totalResults = searchData.totalResults || 0;

  if (!results.length) {
    const queryDisplay = searchQuery || '(empty query)';
    const domainDisplay = domain !== 'ALL' ? ` in **${domain}**` : '';
    const searchSummary = `No results found for **"${queryDisplay}"**${domainDisplay}`;

    return {
      content: searchSummary,
      type: 'search_results',
      collapsed: false,
      summary: searchSummary
    };
  }

  // Create detailed summary with query information
  const queryInfo = searchQuery ? `🔍 "${searchQuery}"${domain !== 'ALL' ? ` in **${domain}**` : ''}` : '';
  const resultSummary = `${results.length} result${results.length === 1 ? '' : 's'}${totalResults && totalResults !== results.length ? ` of ${totalResults} total` : ''}`;
  const summary = queryInfo ? `${queryInfo} - ${resultSummary}` : resultSummary;

  // Create hierarchical results for two-level collapsible rendering
  const hierarchicalResults = results.map((result: any, index: number) => {
    const title = result.displayTitle || result.title || 'Untitled';
    const url = result.url || '';
    const domain = result.domain || '';
    const modDate = result.modificationDate ? new Date(result.modificationDate).toLocaleDateString() : '';
    const score = result.score ? ` (score: ${result.score})` : '';

    // Build title with metadata
    const titleWithMeta = `${index + 1}. ${title}${domain ? ` [${domain}]` : ''}${score}`;

    // Build content with description and metadata
    let contentLines = [];

    if (result.description) {
      contentLines.push(result.description.trim());
    }

    if (url) {
      contentLines.push(`**URL:** [${url}](${url})`);
    }

    if (modDate) {
      contentLines.push(`**Modified:** ${modDate}`);
    }

    if (result.author) {
      contentLines.push(`**Author:** ${result.author}`);
    }

    const content = contentLines.join('\n\n');

    return {
      title: titleWithMeta,
      content,
      language: 'markdown',
      metadata: {
        url,
        domain,
        modDate,
        score: result.score
      }
    };
  });

  const shouldCollapse = results.length > 5;

  return {
    content: summary,
    type: 'search_results',
    collapsed: shouldCollapse,
    summary: summary,
    hierarchicalResults
  };
}

registerInternalFormatter(formatBuilderMcpOutput);
registerToolHeaderEnhancer(enhanceAmazonToolHeader);

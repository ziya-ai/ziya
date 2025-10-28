/**
 * Utility for pretty-printing MCP tool outputs
 */

export interface FormattedOutput {
  content: string;
  type: 'json' | 'text' | 'table' | 'list' | 'error' | 'search_results' | 'html_content';
  showInput?: boolean;
  summary?: string;
  collapsed?: boolean;
  metadata?: Record<string, any>;
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
  } = {}
): FormattedOutput {
  const { maxLength = 5000, showInput = false, compact = false, defaultCollapsed = true } = options;
  
  // Create a generic tool summary from input parameters
  const toolSummary = createToolSummary(toolName, input);
  
  // Try internal formatter first (if available)
  const internalResult = tryInternalFormatter?.(toolName, result, options);
  if (internalResult) {
    return internalResult;
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
    return formatShellCommand(result, { ...options, input, toolSummary });
  }
  
  // Handle workspace search outputs specially
  if (toolName === 'mcp_WorkspaceSearch' && result && typeof result === 'object') {
    return formatWorkspaceSearch(result, { ...options, input, toolSummary });
  }
  
  // Handle sequential thinking tool outputs specially
  if (toolName === 'mcp_sequentialthinking') {
    return formatSequentialThinking(result, input, options);
  }
  
  // Generic search results pattern detection
  if (hasSearchResultsPattern(result)) {
    return formatSearchResults(result.content, { showInput, input, maxLength });
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
    // Check if it's a large text output that should be collapsed
    const shouldCollapse = result.length > 500 || result.split('\n').length > 20;
    
    // Check if it's JSON-like
    if ((result.startsWith('{') || result.startsWith('[')) && result.length > 2) {
      try {
        const parsed = JSON.parse(result);
        return formatObject(parsed, { maxLength, showInput, input, compact, defaultCollapsed, toolSummary });
      } catch (e) {
        // Not JSON, treat as plain text
        return {
          content: showInput ? `Input: ${formatInput(input)}\n\nResult:\n${result}` : result,
          type: 'text',
          showInput,
          collapsed: shouldCollapse && defaultCollapsed,
          summary: shouldCollapse ? `${toolSummary || 'Text output'} (${result.length} chars, ${result.split('\n').length} lines)` : toolSummary
        };
      }
    }
    return {
      content: showInput ? `Input: ${formatInput(input)}\n\nResult:\n${result}` : result,
      type: 'text',
      showInput,
      collapsed: shouldCollapse && defaultCollapsed,
      summary: shouldCollapse ? `${toolSummary || 'Text output'} (${result.length} chars, ${result.split('\n').length} lines)` : toolSummary
    };
  }
  
  // Handle object/array results
  if (typeof result === 'object' && result !== null) {
    return formatObject(result, { maxLength, showInput, input, compact, defaultCollapsed, toolSummary });
  }
  
  // Handle primitive types
  const stringResult = String(result);
  return {
    content: showInput ? `Input: ${formatInput(input)}\n\nResult: ${stringResult}` : stringResult,
    type: 'text',
    showInput,
    collapsed: false,
    summary: toolSummary
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
  const { input, toolSummary } = options;
  const commandExecuted = toolSummary?.replace(/^\$ /, '') || input?.command || '';
  
  // Extract command from result if not in input (fallback)
  let displayCommand = commandExecuted;
  if (!displayCommand && result.startsWith('$ ')) {
    const firstLine = result.split('\n')[0];
    displayCommand = firstLine.substring(2); // Remove '$ ' prefix
  }
  
  const lineCount = result.split('\n').length;
  const shouldCollapse = lineCount > 10;
  
  return {
    content: result,
    type: 'text',
    showInput: false,
    collapsed: shouldCollapse,
    summary: shouldCollapse && displayCommand 
      ? `${toolSummary || `$ ${displayCommand}`} - Output (${lineCount} lines, ${result.length} chars)`
      : shouldCollapse ? `Command output (${lineCount} lines)` : undefined
  };
}

function formatWorkspaceSearch(result: any, options: any): FormattedOutput {
  const { input } = options;
  const searchQuery = input?.searchQuery || '';
  const searchType = input?.searchType || 'contentLiteral';
  const contextLines = input?.contextLines || 0;
  
  // Handle both direct result and wrapped content
  const searchData = result.content || result;
  const results = searchData.results || [];
  const totalCount = searchData.totalCount || 0;
  const hasMore = searchData.hasMore || false;
  
  if (!results.length) {
    const searchSummary = searchQuery ? `No matches found for "${searchQuery}"` : 'No results found';
    return {
      content: searchSummary,
      type: 'search_results',
      collapsed: false,
      summary: searchSummary
    };
  }
  
  // Create detailed summary with query information
  const queryInfo = searchQuery ? `Query: "${searchQuery}" (${searchType})` : '';
  const resultSummary = `${results.length}${hasMore ? '+' : ''} result${results.length === 1 ? '' : 's'}${totalCount && totalCount !== results.length ? ` of ${totalCount} total` : ''}`;
  const summary = queryInfo ? `${queryInfo} - ${resultSummary}` : resultSummary;
  
  // Format results with actual matching lines
  const formattedResults = results.map((result: any, index: number) => {
    let fileInfo = `${index + 1}. ${result.filepath}\n   ${result.lines.length} matching line${result.lines.length === 1 ? '' : 's'}`;
    
    if (result.lines && result.lines.length > 0) {
      // Extract file extension for syntax highlighting
      const ext = result.filepath.split('.').pop()?.toLowerCase() || '';
      const langMap: {[key: string]: string} = {
        'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
        'py': 'python', 'rb': 'ruby', 'php': 'php', 'java': 'java', 'go': 'go',
        'rs': 'rust', 'cpp': 'cpp', 'c': 'c', 'cs': 'csharp', 'css': 'css',
        'html': 'html', 'xml': 'xml', 'md': 'markdown', 'sh': 'bash', 'yml': 'yaml', 'yaml': 'yaml'
      };
      const language = langMap[ext] || 'text';
      
      const codeContent = result.lines.map((line: string) => line.replace(/^\d+[:\-*+\s]*/, '')).join('\n');
      // Use HTML pre/code tags with language class for syntax highlighting within tool blocks
      fileInfo += `\n<pre><code class="language-${language}">${codeContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
    }
    
    return fileInfo;
  }).join('\n\n');
  
  const shouldCollapse = results.length > 5;
  
  return {
    content: `${summary}\n\n${formattedResults}`,
    type: 'search_results',
    collapsed: shouldCollapse,
    summary: summary
  };
}

function formatSequentialThinking(result: any, input: any, options: any): FormattedOutput {
  const thinkingContent = result?.thought || input?.thought || result?.content || '';
  const thoughtNumber = result?.thoughtNumber || input?.thoughtNumber || 1;
  const totalThoughts = result?.totalThoughts || input?.totalThoughts || 1;
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
    return { content: 'No results found', type: 'text', collapsed: false };
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

// Plugin interface for internal formatters
let tryInternalFormatter: ((toolName: string, result: any, options: any) => FormattedOutput | null) | null = null;

export function registerInternalFormatter(formatter: (toolName: string, result: any, options: any) => FormattedOutput | null) {
  tryInternalFormatter = formatter;
}

function formatSearchResults(searchContent: any, options: { showInput: boolean; input?: any; maxLength: number }): FormattedOutput {
  const { showInput, input, maxLength } = options;
  const results = searchContent.results || [];
  
  if (!results.length) {
    return {
      content: 'No results found',
      type: 'search_results',
      collapsed: false
    };
  }
  
  // Create a summary
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
      // Handle the nested body error structure
      let bodyInfo = '';
      if (result.body) {
        try {
          const bodyObj = JSON.parse(result.body);
          if (bodyObj.error) {
            bodyInfo = ` (${bodyObj.error})`;
          }
        } catch (e) {
          // Body isn't JSON, use as-is if short
          bodyInfo = result.body.length < 50 ? ` - ${result.body}` : '';
        }
      }
      const domain = result.domain ? `[${result.domain}]` : '';
      const date = result.modificationDate ? new Date(result.modificationDate).toLocaleDateString() : '';
      return `${index + 1}. ${result.displayTitle || 'Untitled'} ${domain}${bodyInfo}\n   ${result.url}\n   ${date ? `Modified: ${date}` : ''}`;
    }).join('\n\n');
  } else {
    // Generic object format
    formattedResults = JSON.stringify(results, null, 2);
  }
  
  const content = showInput 
    ? `Input: ${formatInput(input)}\n\n${summary}\n\n${formattedResults}`
    : `${summary}\n\n${formattedResults}`;
    
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

function formatObject(obj: any, options: { maxLength: number; showInput: boolean; input?: any; compact: boolean; defaultCollapsed: boolean; toolSummary?: string }): FormattedOutput {
  const { maxLength, showInput, input, compact, defaultCollapsed, toolSummary } = options;
  
  try {
    const jsonString = JSON.stringify(obj, null, compact ? 0 : 2);
    const shouldCollapse = jsonString.length > 1000 || jsonString.split('\n').length > 30;
    
    // Truncate if too long
    const truncated = jsonString.length > maxLength 
      ? jsonString.substring(0, maxLength) + '\n...\n[Output truncated]'
      : jsonString;
    
    const content = showInput 
      ? `Input: ${formatInput(input)}\n\nResult:\n${truncated}`
      : truncated;
      
    return { 
      content, 
      type: 'json', 
      showInput,
      collapsed: shouldCollapse && defaultCollapsed,
      summary: shouldCollapse ? `${toolSummary ? `${toolSummary} - ` : ''}JSON object (${jsonString.length} chars, ${Object.keys(obj).length} properties)` : toolSummary
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
      return query ? `"${query}" in ${domain}` : '';
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
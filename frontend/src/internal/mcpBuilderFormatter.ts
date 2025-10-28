/**
 * Amazon Builder MCP specific formatting enhancements
 * This file contains Amazon-internal knowledge and should NOT be committed to public branches
 */

import { FormattedOutput, registerInternalFormatter } from '../utils/mcpFormatter';

// Amazon-specific tool knowledge
const AMAZON_TOOL_MAPPINGS = {
  'mcp_SearchAcronymCentral': 'acronym',
  'mcp_TicketingReadActions': 'ticketing', 
  'mcp_TaskeiGetRooms': 'taskei_rooms',
  'mcp_TaskeiListTasks': 'taskei_tasks',
  'mcp_OncallReadActions': 'oncall',
  'mcp_ApolloReadActions': 'apollo',
  'mcp_GetSasRisks': 'sas_risks',
  'mcp_WorkspaceGitDetails': 'workspace_git',
  'mcp_GetPipelineHealth': 'pipeline_health',
  'mcp_InternalCodeSearch': 'code_search',
  'mcp_ReadInternalWebsites': 'website_content'
};

const AMAZON_FIELD_LABELS = {
  'ownerLogin': 'Owner',
  'managerLogin': 'Manager', 
  'teamName': 'Team',
  'resolverGroup': 'Resolver Group',
  'environmentName': 'Environment',
  'pipelineName': 'Pipeline'
};

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
      // Let generic formatter handle code search since it follows standard search pattern
      return null;
    default:
      return null;
  }
}

function formatAmazonAcronym(result: any, options: any): FormattedOutput {
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

function formatAmazonTicketing(result: any, options: any): FormattedOutput {
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

function formatAmazonTaskeiRooms(result: any, options: any): FormattedOutput {
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

function formatAmazonOncall(result: any, options: any): FormattedOutput {
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

function formatAmazonApollo(result: any, options: any): FormattedOutput {
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

function formatAmazonSasRisks(result: any, options: any): FormattedOutput {
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

function formatAmazonWorkspaceGit(result: any, options: any): FormattedOutput {
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

// Register the internal formatter

// Register the internal formatter
registerInternalFormatter(formatBuilderMcpOutput);

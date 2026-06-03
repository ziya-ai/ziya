/**
 * Command API client — shared slash command dispatch.
 *
 * Both CLI and web frontend route slash commands through
 * POST /api/v1/commands.  This module provides the web-side client.
 */

export interface CommandRequest {
  command: string;
  args: string;
  conversation_id?: string;
  context_summary?: string;
}

export interface CommandResponse {
  type: string;
  message: string;
  data: Record<string, any>;
}

function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

/**
 * Dispatch a slash command to the backend.
 */
export async function dispatchCommand(req: CommandRequest): Promise<CommandResponse> {
  const res = await fetch('/api/v1/commands', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...projectHeaders() },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Command failed: ${res.status} ${text}`);
  }
  return res.json();
}

/**
 * Check if a message text is a slash command.
 * Returns the parsed command and args, or null if not a command.
 */
export function parseSlashCommand(text: string): { command: string; args: string } | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('/')) return null;

  // Only recognize known commands to avoid false positives
  const KNOWN_COMMANDS = ['goal'];

  const parts = trimmed.slice(1).split(/\s+(.+)/s);
  const command = parts[0]?.toLowerCase();
  const args = parts[1] || '';

  if (!command || !KNOWN_COMMANDS.includes(command)) return null;

  return { command, args };
}

/**
 * Project-scoped cross-tab synchronization via BroadcastChannel.
 *
 * Same-project tabs share:
 *   - Conversation list changes (created, deleted, updated)
 *   - Live streaming chunks and processing state
 *   - Folder changes
 *
 * Different-project tabs are completely isolated (different channel names).
 *
 * Each tab has a unique tabId so it can ignore its own broadcasts.
 */

const TAB_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export type SyncMessageType =
  | 'conversations-changed'
  | 'conversation-created'
  | 'conversation-deleted'
  | 'streaming-chunk'
  | 'streaming-state'
  | 'streaming-ended'
  | 'folders-changed';

export interface SyncMessage {
  type: SyncMessageType;
  _sender: string;
  [key: string]: any;
}

type SyncHandler = (msg: SyncMessage) => void;

class ProjectSyncChannel {
  private channel: BroadcastChannel | null = null;
  private projectId: string | null = null;
  private listeners: Map<SyncMessageType, Set<SyncHandler>> = new Map();

  /** Join the sync channel for a project. Leaves any previous channel. */
  join(projectId: string): void {
    if (this.projectId === projectId && this.channel) return;
    this.leave();
    this.projectId = projectId;
    this.channel = new BroadcastChannel(`ziya-project-${projectId}`);
    this.channel.onmessage = (event: MessageEvent<SyncMessage>) => {
      const msg = event.data;
      // Ignore our own messages
      if (msg._sender === TAB_ID) return;
      this.dispatch(msg);
    };
    console.log(`📡 ProjectSync: Joined channel for project ${projectId} (tab ${TAB_ID})`);
  }

  /** Leave the current channel. */
  leave(): void {
    if (this.channel) {
      this.channel.close();
      this.channel = null;
      console.log(`📡 ProjectSync: Left channel for project ${this.projectId}`);
    }
    this.projectId = null;
  }

  /** Post a message to all other tabs on the same project. */
  post(type: SyncMessageType, payload: Record<string, any> = {}): void {
    if (!this.channel) return;
    this.channel.postMessage({ type, ...payload, _sender: TAB_ID });
  }

  /** Subscribe to a message type. */
  on(type: SyncMessageType, handler: SyncHandler): void {
    let handlers = this.listeners.get(type);
    if (!handlers) {
      handlers = new Set();
      this.listeners.set(type, handlers);
    }
    handlers.add(handler);
  }

  /** Unsubscribe from a message type. */
  off(type: SyncMessageType, handler: SyncHandler): void {
    this.listeners.get(type)?.delete(handler);
  }

  /** Get this tab's unique ID. */
  get tabId(): string {
    return TAB_ID;
  }

  /** Get the current project ID this channel is joined to. */
  get currentProjectId(): string | null {
    return this.projectId;
  }

  private dispatch(msg: SyncMessage): void {
    const handlers = this.listeners.get(msg.type);
    if (!handlers || handlers.size === 0) return;
    for (const handler of handlers) {
      try {
        handler(msg);
      } catch (err) {
        console.error(`📡 ProjectSync: Error in handler for ${msg.type}:`, err);
      }
    }
  }
}

/** Singleton — all code in this tab shares one channel instance. */
export const projectSync = new ProjectSyncChannel();

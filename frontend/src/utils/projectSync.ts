/**
 * Project-scoped cross-tab synchronization via BroadcastChannel.
 *
 * Same-project tabs share:
 *   - Conversation list changes (created, deleted, updated)
 *   - Live streaming chunks and processing state
 *   - Folder changes
 *
 * Different-project tabs are completely isolated (different channel names),
 * and so are tabs on the same origin talking to DIFFERENT Ziya installations:
 * the channel name carries the server instance id.  Without that, a tab on a
 * reused/tunnelled port that happened to resolve the same project id would
 * deliver 'conversation-deleted' straight into another installation's tab.
 *
 * Each tab has a unique tabId so it can ignore its own broadcasts.
 */

import { scopedName, whenInstanceKnown } from './instanceIdentity';

const TAB_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export type SyncMessageType =
  | 'conversations-changed'
  | 'conversation-created'
  | 'conversation-deleted'
  | 'streaming-chunk'
  | 'streaming-state'
  | 'streaming-ended'
  // "Is any tab streaming this conversation?"  A tab that owns the turn
  // replies by re-posting 'streaming-state'.  Used by ChatTurnReattachWatcher
  // to avoid reattaching to a turn this browser is already mirroring.
  | 'streaming-probe'
  | 'folders-changed'
  | 'delegate-status-changed';

export interface SyncMessage {
  type: SyncMessageType;
  _sender: string;
  [key: string]: any;
}

type SyncHandler = (msg: SyncMessage) => void;

class ProjectSyncChannel {
  private channel: BroadcastChannel | null = null;
  private projectId: string | null = null;
  // Increments on every join/leave so a join still awaiting the instance id
  // when the project changes does not open a channel for the old project.
  private joinEpoch = 0;
  private listeners: Map<SyncMessageType, Set<SyncHandler>> = new Map();

  /**
   * Join the sync channel for a project. Leaves any previous channel.
   * Opens asynchronously once the server instance id is known; posts made
   * before then are dropped, exactly as they are today with no channel.
   */
  join(projectId: string): void {
    if (this.projectId === projectId && this.channel) return;
    this.leave();
    this.projectId = projectId;
    const epoch = ++this.joinEpoch;
    whenInstanceKnown().then(instanceId => {
      if (epoch !== this.joinEpoch) return; // superseded by a later join/leave
      const name = scopedName(`ziya-project-${projectId}`, instanceId);
      this.channel = new BroadcastChannel(name);
      this.channel.onmessage = (event: MessageEvent<SyncMessage>) => {
        const msg = event.data;
        // Ignore our own messages
        if (msg._sender === TAB_ID) return;
        this.dispatch(msg);
      };
      console.log(`📡 ProjectSync: Joined channel ${name} (tab ${TAB_ID})`);
    });
  }

  /** Leave the current channel. */
  leave(): void {
    this.joinEpoch++;
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

import { Conversation, ConversationFolder, SearchResult, MessageMatch, SearchOptions } from './types';
import { v4 as uuidv4 } from 'uuid';
import { purgeExpiredConversations } from './retentionPurge';
import { message } from 'antd';
import { performEmergencyRecovery } from './emergencyRecovery';

declare global {
    interface Navigator {
        locks?: {
            request(name: string, callback: (lock: any) => Promise<any>): Promise<any>;
        };
    }
    interface IDBDatabase { }
}

// Get database name from localStorage or use default
const DB_BASE_NAME = (() => {
    const storedName = localStorage.getItem('ZIYA_DB_NAME');
    if (storedName) {
        console.log('Using custom database name:', storedName);
    }
    return storedName || 'ZiyaDB';
})();
let currentDbName = DB_BASE_NAME;
let currentVersion = 3; // Increment version to force upgrade
const STORE_NAME = 'conversations';
const BACKUP_STORE_NAME = 'conversationsBackup';

interface DatabaseHealth {
    isHealthy: boolean;
    errors: string[];
    canRecover: boolean;
}

interface DB {
    db: IDBDatabase | null;
    init(): Promise<void>;
    saveConversations(conversations: Conversation[]): Promise<void>;
    saveConversation(conversation: Conversation): Promise<void>;
    getConversation(id: string): Promise<Conversation | null>;
    deleteConversation(id: string): Promise<void>;
    getConversations(): Promise<Conversation[]>;
    getConversationShells(): Promise<Conversation[]>;
    exportConversations(): Promise<string>;
    importConversations(data: string, importRootFolderId?: string): Promise<void>;
    getFolders(): Promise<ConversationFolder[]>;
    saveFolder(folder: ConversationFolder): Promise<void>;
    deleteFolder(id: string): Promise<void>;
    moveConversationToFolder(conversationId: string, folderId: string | null): Promise<boolean>;
    repairDatabase(): Promise<void>;
    forceReset(): Promise<void>;
    checkDatabaseHealth(): Promise<DatabaseHealth>;
    searchConversations(query: string, options?: SearchOptions): Promise<SearchResult[]>;
}

class ConversationDB implements DB {
    private saveInProgress = false;
    private lastBackupTime = 0;  // BUGFIX: Throttle localStorage backups
    private lastKnownVersion: number = 0;
    private connectionAttempts = 0;
    /** True after the one-time migration from bulk 'current' key to per-record storage. */
    private migrated = false;
    private _pendingMigrationData: Conversation[] | null = null;
    private initializing = false;
    private initPromise: Promise<void> | null = null;

    db: IDBDatabase | null = null;

    async init(): Promise<void> {
        if (this.initPromise) return this.initPromise;

        this.initializing = true;
        if (navigator.locks) {
            this.initPromise = navigator.locks.request('ziya-db-init', async _lock => {
                return this._initWithLock();
            });
            return this.initPromise;
        }
        this.initPromise = this._initWithLock();
        return this.initPromise;
    }

    private async _initWithLock(): Promise<void> {
        try {
            console.debug('Initializing database...');

            if (this.db) {
                this.db.close();
                this.db = null;
            }

            // First check existing version
            console.debug('Checking existing database version');
            const checkRequest = indexedDB.open(currentDbName);

            return new Promise((resolve, reject) => {
                checkRequest.onsuccess = () => {
                    const existingVersion = checkRequest.result.version;

                    // Check for Safari migration issue - missing conversations store
                    const db = checkRequest.result;
                    const hasFolders = db.objectStoreNames.contains('folders');
                    const hasConversations = db.objectStoreNames.contains('conversations');
                    const hasNoStores = db.objectStoreNames.length === 0;

                    // Close the check connection before proceeding
                    checkRequest.result.close();

                    if (hasFolders && !hasConversations) {
                        console.warn('Safari migration issue detected: folders store exists but conversations store is missing');
                        // Auto-trigger emergency recovery
                        performEmergencyRecovery().then(() => {
                            console.log('Recovery completed, forcing page reload');
                            // Force reload to reinitialize everything
                            setTimeout(() => {
                                window.location.reload();
                            }, 100);
                        }).catch(err => console.error('Auto-recovery failed:', err));
                        return;
                    }

                    // Detect corrupted DB with zero object stores — force version bump
                    if (hasNoStores) {
                        console.warn('Corrupted database detected: no object stores exist. Forcing version bump to recreate schema.');
                        currentVersion = existingVersion + 1;
                    }

                    // Use existing version if it's higher
                    if (existingVersion > currentVersion) {
                        currentVersion = existingVersion;
                    }

                    // Log the version we're using
                    console.debug('Database version check:', {
                        existingVersion,
                        usingVersion: currentVersion
                    });

                    // Now open with correct version
                    const dbRequest = indexedDB.open(currentDbName, currentVersion);
                    let upgradeCompleted = false;

                    dbRequest.onerror = () => {
                        console.error('Database initialization error:', dbRequest.error);
                        this.initPromise = null;
                        reject(dbRequest.error);
                    };

                    dbRequest.onblocked = () => {
                        console.warn('Database opening blocked by another tab. Will retry...');
                        const retryInit = () => {
                            if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
                                this.initPromise = null;
                                this.initializing = false;
                                console.warn('Database still blocked, retrying init in 2s...');
                                setTimeout(() => {
                                    this._initWithLock().then(resolve).catch(reject);
                                }, 2000);
                            }
                        };
                        // Give the other tab 5s to complete its upgrade, then retry
                        setTimeout(retryInit, 5000);
                    };

                    dbRequest.onsuccess = () => {
                        this.db = dbRequest.result;

                        this.db.onversionchange = () => {
                            console.warn('Database version change detected, closing connection to unblock other tabs');
                            if (this.db) {
                                this.db.close();
                                this.db = null;
                                this.initPromise = null;
                            }
                        };

                        this.db.onclose = () => {
                            console.debug('Database connection closed');
                            if (this.initializing && !upgradeCompleted && !this.saveInProgress) {
                                console.warn('Database closed during initialization');
                                this.initPromise = null;
                            }
                            this.db = null;
                            this.initPromise = null;
                        };

                        this.initializing = false;
                        console.debug('Database initialized successfully:', {
                            name: this.db.name,
                            version: this.db.version,
                            stores: Array.from(this.db.objectStoreNames)
                        });

                        // Retention purge is intentionally NOT run here.
                        // It called getConversations() (full getAll on every
                        // record including message bodies) which held the
                        // ziya-db-read Web Lock and starved the sidebar's
                        // getConversationShells() call that runs right after
                        // init resolves.  ChatContext schedules the purge
                        // after shells are loaded and the UI is interactive.
                        // Migrate from bulk 'current' key to per-record storage.
                        // Must complete before callers read/write.
                        this._migrateBulkToPerRecord().then(() => {
                            resolve();
                        }).catch(err => {
                            console.warn('Migration from bulk storage failed (non-fatal):', err);
                            resolve(); // Don't block init — old format still works
                        });

                    };

                    dbRequest.onupgradeneeded = (event: IDBVersionChangeEvent) => {
                        console.debug('Upgrading database schema from version', event.oldVersion, 'to', event.newVersion);
                        upgradeCompleted = false;
                        const db = (event.target as IDBOpenDBRequest).result;

                        // Create stores based on what's missing, not just version
                        if (!db.objectStoreNames.contains(STORE_NAME)) {
                            console.debug(`Creating ${STORE_NAME} store`);
                            db.createObjectStore(STORE_NAME);
                        }

                        if (!db.objectStoreNames.contains('folders')) {
                            console.debug('Creating folders store');
                            db.createObjectStore('folders', { keyPath: 'id' });
                        }

                        // Create backup store if needed
                        if (!db.objectStoreNames.contains(BACKUP_STORE_NAME)) {
                            console.debug(`Creating ${BACKUP_STORE_NAME} store`);
                            db.createObjectStore(BACKUP_STORE_NAME);
                        }

                        // Add a transaction complete handler
                        const transaction = (event.target as IDBOpenDBRequest).transaction;
                        if (transaction) {
                            transaction.oncomplete = () => {
                                console.debug('Database upgrade transaction completed successfully');
                                upgradeCompleted = true;
                            }
                        }
                    };

                    checkRequest.onerror = () => {
                        console.error('Version check failed:', checkRequest.error);
                        reject(checkRequest.error);
                    };
                }
            });
        } catch (error) {
            this.initPromise = null;
            console.error('Database initialization failed:', error);
            this.initializing = false;
            throw error;
        }
    }

    /**
     * One-time migration: split the single 'current' array record into
     * individual per-conversation records keyed by conversation ID.
     *
     * Old format: store has one record  { key: 'current', value: Conversation[] }
     * New format: store has N records   { key: conv.id,   value: Conversation }
     *
     * After migration the 'current' key is deleted.  All subsequent
     * reads/writes use per-ID keys.
     */
    private async _migrateBulkToPerRecord(): Promise<void> {
        if (this.migrated) return;
        if (!this.db) return;

        const tx = this.db.transaction([STORE_NAME], 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        return new Promise<void>((resolve, reject) => {
            const getReq = store.get('current');

            getReq.onerror = () => {
                // No 'current' key — already migrated or fresh DB
                this.migrated = true;
                resolve();
            };

            getReq.onsuccess = () => {
                const bulk = getReq.result;
                if (!Array.isArray(bulk) || bulk.length === 0) {
                    // Nothing to migrate (fresh DB, or already migrated)
                    this.migrated = true;
                    resolve();
                    return;
                }

                console.log(`🔄 MIGRATION: Splitting ${bulk.length} conversations from bulk 'current' key to per-record storage`);

                let written = 0;
                for (const conv of bulk) {
                    if (!conv?.id) continue;
                    store.put(conv, conv.id);
                    written++;
                }

                // Delete the old bulk record
                store.delete('current');

                console.log(`✅ MIGRATION: Wrote ${written} per-record entries, deleted 'current' key`);
                this.migrated = true;
            };

            tx.oncomplete = () => resolve();
            tx.onerror = () => {
                console.error('Migration transaction failed:', tx.error);
                // Mark as migrated anyway to prevent retry loops — the old
                // format still works, we'll try again on next restart.
                this.migrated = true;
                reject(tx.error);
            };
        });
    }

    private validateConversations(conversations: Conversation[]): boolean {
        // Minimal validation - only check structural integrity
        // We should NEVER silently discard user data based on content
        return conversations.every(conv =>
            typeof conv === 'object' &&
            conv !== null &&
            typeof conv.id === 'string' &&
            conv.id.length > 0 &&
            Array.isArray(conv.messages)
        );
    }

    private mergeConversations(local: Conversation[], remote: Conversation[]): Conversation[] {
        const merged = new Map<string, Conversation>();

        // Don't filter out empty conversations - they're valid while waiting for first message
        const localFiltered = local;
        const remoteFiltered = remote;

        // Protect active conversations first
        const activeConvs = localFiltered.filter(conv =>
            conv.messages &&
            conv.messages.length > 0 &&
            conv.isActive !== false
        );

        console.debug('Merging conversations:', {
            localCount: localFiltered.length,
            remoteCount: remoteFiltered.length,
            activeCount: activeConvs.length,
            localIds: localFiltered.map(c => c.id),
            remoteIds: remoteFiltered.map(c => c.id)
        });

        // Add active conversations first
        activeConvs.forEach(conv => {
            merged.set(conv.id, {
                ...conv,
                _version: Date.now(),
                isActive: true
            });
        });

        // Add remaining local conversations
        localFiltered.forEach(conv => {
            if (!merged.has(conv.id)) {
                merged.set(conv.id, {
                    ...conv,
                    _version: conv._version || Date.now(),
                    isActive: conv.isActive !== false
                });
            }
        });

        // Merge remote conversations
        remoteFiltered.forEach(conv => {
            const existingConv = merged.get(conv.id);
            if (existingConv) {
                if (existingConv.isActive === false ||
                    (existingConv._version && conv._version && existingConv._version > conv._version)) {
                    return;
                }
            }
            merged.set(conv.id, {
                ...conv,
                _version: Math.max(conv._version || 0, existingConv?._version || 0, Date.now()),
                isActive: existingConv?.isActive !== false
            });
        });

        return Array.from(merged.values()).sort((a, b) => {
            if (a.isActive && !b.isActive) return -1;
            if (!a.isActive && b.isActive) return 1;
            return (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0);
        });
    }

    async saveConversations(conversations: Conversation[]): Promise<void> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try {
                await this.init();
            } catch (error) {
                console.error('Failed to initialize database:', error);
                try {
                    const recovered = await this.handleMissingStore();
                    if (!recovered) {
                        console.error('Failed to recover database');
                    }
                } catch (e) {
                    console.error('Failed to handle missing store:', e);
                }
                throw new Error('Database initialization failed');
            }
            if (!this.db) throw new Error('Database not initialized');
        }

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async _lock => {
                // FAST PATH: small batch with no shells — skip dedup/getAll overhead
                const hasShells = conversations.some(c => (c as any)._isShell);
                const allHaveIds = conversations.every(c => c?.id);
                if (allHaveIds && !hasShells && conversations.length <= 10) {
                    const tx = this.db!.transaction([STORE_NAME], 'readwrite');
                    const store = tx.objectStore(STORE_NAME);
                    return new Promise<void>((resolve, reject) => {
                        tx.oncomplete = () => resolve();
                        tx.onerror = () => reject(tx.error);
                        tx.onabort = () => reject(new Error('Transaction aborted'));
                        for (const conv of conversations) {
                            // Defensive: hasShells above should guarantee this never
                            // fires, but if a future change ever lets a shell reach
                            // the fast path we want to know loudly rather than
                            // silently blank message content.
                            if ((conv as any)._isShell) {
                                console.error(
                                    `🛡️ FAST_PATH_GUARD: Refusing to write shell for ${conv.id?.substring?.(0, 8)} via fast path. This indicates a regression.`,
                                    new Error('shell-in-fast-path stack')
                                );
                                continue;
                            }
                            const stripped = { ...conv } as any;
                            delete stripped._isShell;
                            delete stripped._fullMessageCount;
                            // Per-record tombstone guard (mirrors the slow path in
                            // _saveConversationsWithLock).  Without this the fast
                            // path could overwrite a populated IDB record with an
                            // empty-messages version and bump _version, stranding
                            // the user with a conversation that shows no messages
                            // on next reload (observed for conv 317c500e).  Uses
                            // the same threshold as the slow path: only preserve
                            // when existing.length > caller.length AND existing
                            // had more than 2 messages (guards against resurrecting
                            // deleted short conversations).
                            const guardReq = store.get(conv.id);
                            guardReq.onsuccess = () => {
                                const existing = guardReq.result;
                                const existingLen = Array.isArray(existing?.messages) ? existing.messages.length : 0;
                                const callerLen = Array.isArray(stripped.messages) ? stripped.messages.length : 0;
                                if (existingLen > callerLen && existingLen > 2) {
                                    console.warn(
                                        `🛡️ FAST_PATH_TOMBSTONE: Preserving ${existingLen} messages ` +
                                        `for ${conv.id.substring(0, 8)} (caller had ${callerLen}). ` +
                                        `Title: "${(conv.title || '').substring(0, 60)}"`,
                                        new Error('fast-path tombstone stack')
                                    );
                                    store.put({ ...stripped, messages: existing.messages, _version: conv._version || Date.now() }, conv.id);
                                } else {
                                    store.put({ ...stripped, _version: conv._version || Date.now() }, conv.id);
                                }
                            };
                            guardReq.onerror = () => {
                                // Can't read existing — write anyway (match slow path behavior)
                                store.put({ ...stripped, _version: conv._version || Date.now() }, conv.id);
                            };
                        }
                    });
                }
                try {
                    return await this._saveConversationsWithLock(conversations);
                } catch (error) {
                    // If database connection error, try to recover once
                    if (error instanceof Error &&
                        (error.message.includes('closing') || error.message.includes('InvalidStateError'))) {
                        console.warn('Database connection error, attempting recovery');
                        await this.init();
                        return await this._saveConversationsWithLock(conversations);
                    }

                    // If this is a missing store error, attempt recovery
                    if (error instanceof Error &&
                        error.name === 'NotFoundError' &&
                        error.message.includes('object stores was not found')) {
                        console.warn('Missing store during save, attempting recovery');
                        const recovered = await this.handleMissingStore();
                        if (!recovered) {
                            console.error('Failed to recover database during save');
                            // Try emergency recovery as a last resort
                            await this.forceReset();
                            await this.init();

                            // Force reload to reinitialize everything
                            setTimeout(() => {
                                window.location.reload();
                            }, 100);
                        }
                        return this._saveConversationsWithLock(conversations);
                    }
                    throw error;
                }
            });
        }
        return this._saveConversationsWithLock(conversations);
    }

    private async _saveConversationsWithLock(conversations: Conversation[]): Promise<void> {
        console.debug('Starting _saveConversationsWithLock with', conversations.length, 'conversations');

        // CRITICAL: Deduplicate conversations before saving
        const deduped = new Map<string, Conversation>();
        const shellMetadataUpdates = new Map<string, Conversation>();
        conversations.forEach(conv => {
            // CRITICAL: Strip shell markers before persisting — they are
            // transient metadata that must never reach IndexedDB.
            // Also reject shell conversations outright if they would
            // downgrade the message count (data loss prevention).
            if ((conv as any)._isShell) {
                const fullCount = (conv as any)._fullMessageCount || 0;
                // Shells ALWAYS have stripped message content (content: '').
                // Writing them to IDB blanks the real content even when
                // messages.length matches _fullMessageCount (short chats) or
                // when _fullMessageCount got cleared upstream.  Block
                // unconditionally and route through the metadata-only merge
                // path so folderId/version/lastAccessedAt are still persisted.
                // Capture a stack so we can identify which caller fed a shell
                // into queueSave — shells should never reach persistence paths
                // and the caller is the real bug to fix.
                console.warn(
                    `🛡️ SAVE_GUARD: Blocking shell write for ${conv.id.substring(0, 8)} (messages=${conv.messages.length}, fullCount=${fullCount}). Metadata will be merged separately.`,
                    new Error('shell-write-caller stack')
                );
                shellMetadataUpdates.set(conv.id, conv);
                return; // Skip this conversation entirely — IDB keeps the full record
            }

            const existing = deduped.get(conv.id);
            if (!existing) {
                deduped.set(conv.id, conv);
            } else if ((conv as any)._isShell && !(existing as any)._isShell) {
                // Never let a shell overwrite a non-shell entry in the same batch
                console.warn(`🛡️ DEDUP_GUARD: Keeping non-shell version of ${conv.id.substring(0, 8)} (shell had ${conv.messages?.length || 0}, non-shell has ${existing.messages?.length || 0})`);
            } else {
                // Keep the one with more messages or newer version
                const existingMsgCount = existing.messages?.length || 0;
                const currentMsgCount = conv.messages?.length || 0;
                const existingVersion = existing._version || 0;
                const currentVersion = conv._version || 0;

                if (currentMsgCount > existingMsgCount ||
                    (currentMsgCount === existingMsgCount && currentVersion > existingVersion)) {
                    console.warn('🔄 Replacing duplicate conversation:', conv.id.substring(0, 8),
                        `(${existingMsgCount} -> ${currentMsgCount} messages)`);
                    deduped.set(conv.id, conv);
                } else {
                    console.warn('⚠️ Skipping duplicate conversation:', conv.id.substring(0, 8),
                        `(keeping ${existingMsgCount} messages, discarding ${currentMsgCount})`);
                }
            }
        });

        const uniqueConversations = Array.from(deduped.values());
        if (uniqueConversations.length !== conversations.length) {
            console.warn(`🔧 Deduplicated: ${conversations.length} -> ${uniqueConversations.length} conversations`);
        }

        // Check if database is available and not closing
        if (!this.db || this.initializing) {
            console.warn('Database not ready, attempting to initialize');
            await this.init();
            if (!this.db) throw new Error('Database initialization failed');
        }

        if (this.saveInProgress) {
            console.warn('Save already in progress, skipping');
            return;
        }

        this.saveInProgress = true;
        let saveCompleted = false;

        // Check if the store exists before attempting to save
        if (!this.db.objectStoreNames.contains(STORE_NAME)) {
            console.error('Cannot save - conversations store not found');
            this.saveInProgress = false;
            throw new Error('Conversations store not found');
        }

        try {
            // Merge metadata from blocked shell writes onto existing IDB records.
            // This ensures folderId, _version, lastAccessedAt etc. are persisted
            // even when the SAVE_GUARD blocks the full conversation write to
            // protect against message data loss.
            if (shellMetadataUpdates.size > 0) {
                const metaTx = this.db!.transaction([STORE_NAME], 'readwrite');
                const metaStore = metaTx.objectStore(STORE_NAME);
                for (const [id, shellConv] of shellMetadataUpdates) {
                    const getReq = metaStore.get(id);
                    getReq.onsuccess = () => {
                        const existing = getReq.result;
                        if (existing) {
                            existing.folderId = shellConv.folderId;
                            existing._version = shellConv._version || existing._version;
                            existing.lastAccessedAt = shellConv.lastAccessedAt || existing.lastAccessedAt;
                            existing.groupId = shellConv.groupId !== undefined ? shellConv.groupId : existing.groupId;
                            existing.isGlobal = shellConv.isGlobal !== undefined ? shellConv.isGlobal : existing.isGlobal;
                            metaStore.put(existing, id);
                            console.log(`🔧 SAVE_GUARD: Merged metadata for ${id.substring(0, 8)} (folderId: ${shellConv.folderId})`);
                        }
                    };
                }
                await new Promise<void>((resolve, reject) => {
                    metaTx.oncomplete = () => resolve();
                    metaTx.onerror = () => reject(metaTx.error);
                });
            }

            const tx = this.db!.transaction([STORE_NAME], 'readwrite');
            console.debug('Transaction created successfully');
            const store = tx.objectStore(STORE_NAME);

            return new Promise<void>((resolve, reject) => {
                console.debug('📝 Conversations being saved:', uniqueConversations.length);

                const conversationsToSave = uniqueConversations.map(conv => ({
                    ...conv,
                    _version: conv._version || Date.now(),
                    messages: conv.messages.map(msg => ({
                        ...msg,
                        _timestamp: msg._timestamp || Date.now()
                    })),
                    lastAccessedAt: conv.lastAccessedAt || Date.now(),
                    isActive: conv.isActive !== false
                }));

                // Write each conversation as its own record with per-record guard
                for (const conv of conversationsToSave) {
                    const guardReq = store.get(conv.id);
                    guardReq.onsuccess = () => {
                        const existing = guardReq.result;
                        // Per-record message-count guard
                        if (existing?.id && existing.messages?.length > conv.messages?.length
                            && existing.messages.length > 2) {
                            console.warn(
                                `🛡️ IDB_WRITE_GUARD: Preserving ${existing.messages.length} messages ` +
                                `for ${conv.id.substring(0, 8)} (caller had ${conv.messages?.length || 0})`
                            );
                            store.put({ ...conv, messages: existing.messages }, conv.id);
                        } else {
                            store.put(conv, conv.id);
                        }
                    };
                    guardReq.onerror = () => {
                        // Can't read existing — write anyway
                        store.put(conv, conv.id);
                    };
                }

                // Also clean up legacy 'current' key if it still exists
                // (belt-and-suspenders alongside migration)
                try { store.delete('current'); } catch { /* ignore */ }

                tx.oncomplete = () => {
                    saveCompleted = true;
                    console.debug('Save completed:', conversationsToSave.length, 'conversations');
                    resolve();
                };

                tx.onerror = () => {
                    console.error('Transaction error:', tx.error);
                    // Provide more specific error information
                    const errorMsg = tx.error?.message || 'Unknown transaction error';
                    reject(new Error(`Transaction failed: ${errorMsg}`));
                };

                tx.onabort = () => {
                    console.error('Transaction aborted');
                    reject(new Error('Transaction was aborted'));
                };
            });
        } finally {
            this.saveInProgress = false;
        }
    }

    // Add a method to restore from backup if needed
    async restoreFromBackup(): Promise<Conversation[]> {
        // Server-side recovery: INIT_SYNC in ChatContext fetches from
        // /api/v1/projects/{pid}/chats and merges into IndexedDB.
        // This method is a last-resort fallback that returns empty —
        // the real recovery happens in ChatContext's initialization flow.
        console.warn('restoreFromBackup called — server sync in ChatContext handles recovery');
        try {
            // Clean up legacy localStorage backup if it exists
            localStorage.removeItem('ZIYA_CONVERSATION_BACKUP');
        } catch (e) {
            // Ignore — localStorage may not be available
        }

        // Return empty array if no backup or error
        return [];
    }

    /**
     * Read a single conversation by ID.  O(1) with per-record storage.
     */
    async getConversation(id: string): Promise<Conversation | null> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try { await this.init(); } catch { return null; }
            if (!this.db) return null;
        }

        const readFn = async (): Promise<Conversation | null> => {
            const tx = this.db!.transaction([STORE_NAME], 'readonly');
            const store = tx.objectStore(STORE_NAME);
            return new Promise((resolve) => {
                const request = store.get(id);
                request.onsuccess = () => {
                    const result = request.result;
                    // Validate it's actually a conversation object (not the
                    // legacy 'current' array or some other artifact)
                    if (result && result.id && Array.isArray(result.messages)) {
                        resolve(result);
                    } else {
                        resolve(null);
                    }
                };
                request.onerror = () => resolve(null);
            });
        };

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-read', () => readFn());
        }
        return readFn();
    }

    /**
     * Write a single conversation.  Includes a per-record message-count
     * guard to prevent regressions.
     */
    async saveConversation(conversation: Conversation): Promise<void> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try { await this.init(); } catch { throw new Error('Database not initialized'); }
            if (!this.db) throw new Error('Database not initialized');
        }

        const writeFn = async (): Promise<void> => {
            const tx = this.db!.transaction([STORE_NAME], 'readwrite');
            const store = tx.objectStore(STORE_NAME);

        // Strip transient shell markers — they must never reach IndexedDB.
        // saveConversations (bulk path) already blocks shell writes;
        // this single-record path needs the same protection.
        const toWrite = { ...conversation } as any;
        delete toWrite._isShell;
        delete toWrite._fullMessageCount;
        conversation = toWrite;

            // Per-record guard: check existing message count before overwriting
            const getReq = store.get(conversation.id);
            getReq.onsuccess = () => {
                const existing = getReq.result;
                if (existing?.messages?.length > conversation.messages?.length
                    && existing.messages.length > 2) {
                    console.warn(
                        `🛡️ SAVE_GUARD: Preserving ${existing.messages.length} messages ` +
                        `for ${conversation.id.substring(0, 8)} (caller had ${conversation.messages?.length || 0})`
                    );
                    conversation = { ...conversation, messages: existing.messages };
                }
                store.put(conversation, conversation.id);
            };
            getReq.onerror = () => {
                // Can't read existing — write anyway
                store.put(conversation, conversation.id);
            };

            return new Promise<void>((resolve, reject) => {
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
        };

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', () => writeFn());
        }
        return writeFn();
    }

    /**
     * Delete a single conversation by ID.
     */
    async deleteConversation(id: string): Promise<void> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try { await this.init(); } catch { return; }
            if (!this.db) return;
        }

        const tx = this.db.transaction([STORE_NAME], 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        store.delete(id);

        return new Promise<void>((resolve, reject) => {
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    }

    async getConversations(): Promise<Conversation[]> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try {
                await this.init();
            } catch (error) {
                console.warn('Failed to initialize database, returning empty conversations array');
                console.error('Failed to initialize database:', error);
                return this.restoreFromBackup();
            }
            if (!this.db) throw new Error('Database not initialized');
        }

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-read', async _lock => {
                return this._getConversationsWithLock();
            });
        }
        return this._getConversationsWithLock();
    }

    private async _getConversationsWithLock(): Promise<Conversation[]> {
        if (!this.db) {
            console.warn('Database not initialized in _getConversationsWithLock');
            return [];
        }

        // Check if the store exists
        if (!this.db.objectStoreNames.contains(STORE_NAME)) {
            console.warn(`${STORE_NAME} store not found, attempting recovery`);
            const recovered = await this.handleMissingStore();
            if (recovered) {
                return this.getConversations(); // Try again with recovered database
            }
            return this.restoreFromBackup(); // Try to restore from backup
        }

        let tx;
        try {
            tx = this.db.transaction([STORE_NAME], 'readonly');
        } catch (error) {
            console.error('Error creating transaction:', error);
            const recovered = await this.handleMissingStore();
            if (recovered) {
                return this.getConversations(); // Try again with recovered database
            }
            return this.restoreFromBackup(); // Try to restore from backup
        }

        const store = tx.objectStore(STORE_NAME);

        return new Promise<Conversation[]>((resolve, reject) => {
            const request = store.getAll();

            request.onsuccess = () => {
                const allRecords = request.result || [];
                // getAll() returns every record in the store.  Filter to
                // valid conversation objects — skip legacy 'current' array
                // (if migration hasn't run yet) and any other artifacts.
                const conversations: Conversation[] = [];
                for (const record of allRecords) {
                    if (!record || typeof record !== 'object') continue;
                    if (Array.isArray(record)) continue;
                    if (record.id && typeof record.id === 'string' && Array.isArray(record.messages)) {
                        conversations.push(record);
                    }
                }

                if (conversations.length > 0) {
                    resolve(conversations);
                } else {
                    this.restoreFromBackup().then(backup =>
                        resolve(backup.length > 0 ? backup : [])
                    ).catch(() => resolve([]));
                }
            };

            request.onerror = () => {
                reject(request.error);
            };
        });
    }

    /**
     * Load conversations without message content — only metadata needed for
     * the sidebar, folder tree, and routing.  Messages for the active
     * conversation are loaded on-demand by the full getConversations() path
     * or by the server sync that fetches individual chats.
     */
    async getConversationShells(): Promise<Conversation[]> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try { await this.init(); } catch { return []; }
            if (!this.db) return [];
        }
        const readFn = async (): Promise<Conversation[]> => {
            const tx = this.db!.transaction([STORE_NAME], 'readonly');
            const store = tx.objectStore(STORE_NAME);
            const shells: Conversation[] = [];

            return new Promise<Conversation[]>((resolve) => {
                const cursorReq = store.openCursor();

                cursorReq.onsuccess = () => {
                    const cursor = cursorReq.result;
                    if (!cursor) {
                        // Cursor exhausted — return collected shells
                        resolve(shells);
                        return;
                    }

                    const conv = cursor.value;

                    // Skip non-conversation records (legacy bulk array, artifacts)
                    if (!conv?.id || typeof conv.id !== 'string' || !Array.isArray(conv.messages)) {
                        cursor.continue();
                        return;
                    }

                    // Build shell: drop message content entirely.  V8's String.slice
                    // returns a SlicedString that retains the parent string, so even
                    // "truncated" previews hold the full original content alive.
                    // The sidebar only reads title/id/timestamps from shells, never
                    // content, so dropping it is safe and essential to keep shells
                    // actually lightweight.
                    const stripMessage = (m: any) => m ? ({
                        id: m.id,
                        role: m.role,
                        content: '',
                        _timestamp: m._timestamp,
                    }) : m;
                    const firstMsg = conv.messages.length > 0 ? stripMessage(conv.messages[0]) : null;
                    const lastMsg = conv.messages.length > 1 ? stripMessage(conv.messages[conv.messages.length - 1]) : null;
                    shells.push({
                        ...conv,
                        messages: firstMsg ? (lastMsg ? [firstMsg, lastMsg] : [firstMsg]) : [],
                        _isShell: true,
                        _fullMessageCount: conv.messages?.length || 0,
                    } as Conversation);

                    cursor.continue();
                };

                cursorReq.onerror = () => resolve([]);
            });
        };

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-read', () => readFn());
        }
        return readFn();
    }

    private async handleMissingStore(): Promise<boolean> {
        console.warn('Handling missing store issue (seen in Safari migration)');

        // Close any existing connection
        if (this.db) {
            this.db.close();
            this.db = null;
        }

        // Force version increment to trigger schema recreation
        currentVersion++;
        this.initPromise = null;
        localStorage.removeItem('ZIYA_DB_NAME'); // Clear any custom DB name

        try {
            // Reinitialize with new version
            await this.init();

            // Check if we have the required stores now
            if (this.db &&
                (this.db as IDBDatabase).objectStoreNames.contains(STORE_NAME) &&
                (this.db as IDBDatabase).objectStoreNames.contains('folders')) {

                console.log('Database recovery successful - schema restored with version', (this.db as any).version);
                return true;
            }

            console.warn('Recovery attempt failed - stores still missing');
            return false;
        } catch (error) {
            console.error('Error during store recovery:', error);
            return false;
        }
    }

    async exportConversations(): Promise<string> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-read', async _lock => {
                return this._exportConversations();
            });
        }
        return this._exportConversations();
    }
    private async _exportConversations(): Promise<string> {
        if (!this.db) {
            throw new Error('Database not initialized');
        }
        try {
            const tx = this.db.transaction([STORE_NAME], 'readonly');
            const store = tx.objectStore(STORE_NAME);
            const request = store.getAll();
            return new Promise((resolve, reject) => {
                request.onsuccess = () => {
                    const allRecords = request.result || [];
                    // Filter to valid conversation objects
                    const activeConversations = allRecords.filter(record =>
                        record &&
                        !Array.isArray(record) &&
                        record.id &&
                        typeof record.id === 'string' &&
                        Array.isArray(record.messages) &&
                        record.isActive !== false
                    );

                    // Also export folders to maintain hierarchy
                    this.getFolders().then(folders => {
                        const exportData = {
                            version: '1.0',
                            exportDate: new Date().toISOString(),
                            conversations: activeConversations,
                            folders: folders
                        };
                        resolve(JSON.stringify(exportData, null, 2));
                    }).catch(error => {
                        console.warn('Failed to export folders, exporting conversations only:', error);
                        // Fallback to conversations only for backward compatibility
                        resolve(JSON.stringify(activeConversations, null, 2));
                    });
                };
                request.onerror = () => reject(request.error);
            });
        } catch (error) {
            throw new Error(`Export failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    }

    async importConversations(data: string, importRootFolderId?: string): Promise<void> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async _lock => {
                return this._importConversations(data, importRootFolderId);
            });
        }
        return this._importConversations(data, importRootFolderId);
    }

    private validateImportedConversation(conv: any): boolean {
        return !!(
            conv &&
            typeof conv === 'object' &&
            conv.id &&
            typeof conv.id === 'string' &&
            conv.id.length > 0 &&
            conv.title &&
            typeof conv.title === 'string' &&
            conv.messages &&
            Array.isArray(conv.messages)
        );
    }

    private async _importConversations(data: string, importRootFolderId?: string): Promise<void> {
        if (!this.db) {
            throw new Error('Database not initialized');
        }
        try {
            // Parse and validate the imported data
            const parsedData = JSON.parse(data);
            let importedConversations: Conversation[] = [];
            let importedFolders: ConversationFolder[] = [];

            // Handle both old format (array of conversations) and new format (object with conversations and folders)
            if (Array.isArray(parsedData)) {
                // Old format - just conversations
                importedConversations = parsedData;
                console.log('Importing legacy format:', data.length, 'conversations');

                // Validate all conversations before importing
                importedConversations = importedConversations.filter(c => {
                    const valid = this.validateImportedConversation(c);
                    if (!valid) console.warn('⚠️ Skipping invalid conversation:', c.id?.substring(0, 8) || 'no-id');
                    return valid;
                });
            } else if (parsedData && typeof parsedData === 'object') {
                // New format - object with conversations and folders
                if (parsedData.conversations && Array.isArray(parsedData.conversations)) {
                    importedConversations = parsedData.conversations;
                }
                if (parsedData.folders && Array.isArray(parsedData.folders)) {
                    importedFolders = parsedData.folders;
                }
                console.log('Importing new format with folders:', {
                    conversations: importedConversations.length,
                    folders: importedFolders.length
                });

                // Validate all conversations
                const invalidCount = importedConversations.length;
                importedConversations = importedConversations.filter(c => {
                    const valid = this.validateImportedConversation(c);
                    if (!valid) console.warn('⚠️ Skipping invalid conversation:', c.id?.substring(0, 8) || 'no-id');
                    return valid;
                });

                if (importedConversations.length < invalidCount) {
                    console.warn(`⚠️ IMPORT: Filtered out ${invalidCount - importedConversations.length} invalid conversations`);
                }
            } else {
                throw new Error('Invalid import format - expected array or object with conversations');
            }

            // Get existing conversations to prevent duplicates
            const existingConversations = await this.getConversations();
            const existingIds = new Set(existingConversations.map(c => c.id));

            // Only import conversations that don't already exist
            const newConversations = importedConversations.filter(c => !existingIds.has(c.id));
            const duplicateCount = importedConversations.length - newConversations.length;

            if (duplicateCount > 0) {
                console.warn(`⚠️ IMPORT: Skipping ${duplicateCount} duplicate conversations`);
            }

            if (newConversations.length === 0) {
                console.log('ℹ️ IMPORT: No new conversations to import');
                return;
            }

            // Ensure all imported conversations are marked as active with explicit versions
            // Build folder ID remapping when importing under a root folder.
            // Every imported folder gets a fresh UUID so re-importing the same
            // file twice doesn't collide.  Parent references are rewritten to
            // preserve the original hierarchy underneath importRootFolderId.
            const folderIdMap = new Map<string, string>();
            if (importRootFolderId && importedFolders.length > 0) {
                for (const folder of importedFolders) {
                    folderIdMap.set(folder.id, uuidv4());
                }
            }

            const processedConversations = newConversations.map(conv => ({
                ...conv,
                isActive: true,
                _version: conv._version || Date.now(),
                // Always stamp lastAccessedAt to now. The original value reflects
                // when the conversation was last used before export — which may be
                // months old. The server's retention policy uses lastActiveAt
                // (derived from lastAccessedAt) to decide expiry, so an old
                // timestamp causes the server to delete the conversation on its
                // first sync. Stamping now correctly records "user just imported this".
                lastAccessedAt: Date.now(),
                folderId: importRootFolderId
                    ? (conv.folderId && folderIdMap.has(conv.folderId)
                        ? folderIdMap.get(conv.folderId)
                        : importRootFolderId)
                    : conv.folderId
            }));

            // Final validation
            const validConversations = processedConversations.filter(conv =>
                this.validateConversations([conv])
            );

            if (validConversations.length === 0) {
                console.warn('⚠️ IMPORT: No valid conversations after filtering');
                return;
            }

            console.log(`📥 IMPORT: Validated ${validConversations.length} conversations for import`);

            // Import folders first (if any)
            if (importedFolders.length > 0 && importRootFolderId && folderIdMap.size > 0) {
                // Remap folder IDs and reparent under the import root folder,
                // preserving the original hierarchy as sub-folders.
                console.log(`📁 IMPORT: Remapping ${importedFolders.length} folders under import root`);

                for (const folder of importedFolders) {
                    const newId = folderIdMap.get(folder.id);
                    if (!newId) continue;

                    const newParentId = folder.parentId && folderIdMap.has(folder.parentId)
                        ? folderIdMap.get(folder.parentId)!
                        : importRootFolderId;

                    try {
                        await this.saveFolder({
                            ...folder,
                            id: newId,
                            parentId: newParentId,
                            createdAt: folder.createdAt || Date.now(),
                            updatedAt: Date.now()
                        });
                    } catch (error) {
                        console.warn(`Failed to import folder ${folder.name}:`, error);
                    }
                }
            } else if (importedFolders.length > 0) {
                // No import root — legacy behaviour: import folders as-is, skip duplicates
                const existingFolders = await this.getFolders();
                const existingFolderIds = new Set(existingFolders.map(f => f.id));
                const newFolders = importedFolders.filter(f => !existingFolderIds.has(f.id));
                console.log(`📁 IMPORT: Adding ${newFolders.length} new folders (legacy mode)`);
                for (const folder of newFolders) {
                    try {
                        await this.saveFolder({
                            ...folder,
                            createdAt: folder.createdAt || Date.now(),
                            updatedAt: Date.now()
                        });
                    } catch (error) {
                        console.warn(`Failed to import folder ${folder.name}:`, error);
                    }
                }
            }

            // Write imported conversations as individual records
            const tx = this.db.transaction([STORE_NAME], 'readwrite');
            const store = tx.objectStore(STORE_NAME);

            for (const conv of validConversations) {
                store.put(conv, conv.id);
            }

            return new Promise((resolve, reject) => {
                tx.oncomplete = () => {
                    console.log(`✅ IMPORT COMPLETE: Wrote ${validConversations.length} conversations`);
                    resolve();
                };

                tx.onerror = () => {
                    console.error('❌ Import transaction failed:', tx.error);
                    reject(tx.error);
                };
            });
        } catch (error) {
            console.error('Import error:', error);
            throw new Error(error instanceof Error ? error.message : 'Failed to import conversations');
        }
    }

    async getFolders(): Promise<ConversationFolder[]> {
        if (!this.db) {
            try {
                await this.init();
            } catch (error) {
                console.error('Failed to initialize database:', error);
                return [];
            }
        }

        try {
            // Check if the 'folders' object store exists
            if (!this.db!.objectStoreNames.contains('folders')) {
                console.warn("Folders object store doesn't exist yet. Will be created on next database upgrade.");
                return [];
            } else {
                return new Promise((resolve, reject) => {
                    const tx = this.db!.transaction('folders', 'readonly');
                    const store = tx.objectStore('folders');
                    const request = store.getAll();
                    request.onsuccess = () => resolve(request.result || []);
                    request.onerror = () => reject(request.error);
                });
            }
        } catch (error) {
            console.error('Error getting folders:', error);
            return [];
        }
    }

    async saveFolder(folder: ConversationFolder): Promise<void> {
        if (!this.db) await this.init();

        // Check if the 'folders' object store exists
        if (!this.db!.objectStoreNames.contains('folders')) {
            console.warn("Folders object store doesn't exist yet. Cannot save folder until database is upgraded.");
            return;
        }

        return new Promise<void>((resolve, reject) => {
            const tx = this.db!.transaction('folders', 'readwrite');
            const store = tx.objectStore('folders');
            const request = store.put(folder);

            request.onsuccess = () => {
                // signal other tabs that folder data has changed
                try {
                    localStorage.setItem('ziyaDbLastFolderUpdate', Date.now().toString());
                } catch (e) { console.warn("Could not signal folder update via localStorage", e); }
                resolve();
            };
            request.onerror = () => reject(request.error);
            tx.oncomplete = () => resolve();
        });
    }

    async deleteFolder(id: string): Promise<void> {
        await this.init();

        try {
            // Delete the folder
            const tx = this.db!.transaction('folders', 'readwrite');
            const store = tx.objectStore('folders');
            await store.delete(id);
            const request = store.delete(id);

            await new Promise<void>((resolve, reject) => { // Wrap in promise for async/await
                request.onsuccess = () => {
                    // Signal other tabs that folder data has changed
                    try {
                        localStorage.setItem('ziyaDbLastFolderUpdate', Date.now().toString());
                    } catch (e) { console.warn("Could not signal folder update via localStorage", e); }
                    resolve();
                };
                request.onerror = () => reject(request.error);
            });

            console.log(`Folder ${id} deleted from database`);
        } catch (error) {
            console.error(`Error deleting folder ${id}:`, error);
            throw error;
        }
    }

    async moveConversationToFolder(conversationId: string, folderId: string | null): Promise<boolean> {
        await this.init();
        const tx = this.db!.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        return new Promise((resolve, reject) => {
            const getRequest = store.get(conversationId);

            getRequest.onsuccess = () => {
                const conv = getRequest.result;
                if (!conv?.id) {
                    resolve(false);
                    return;
                }

                // Log the conversation being moved
                console.log('Moving conversation to folder:', { conversationId, folderId });

                const putRequest = store.put(
                    { ...conv, folderId, lastAccessedAt: Date.now(), _version: Date.now() },
                    conversationId
                );
                putRequest.onsuccess = () => resolve(true);
                putRequest.onerror = () => reject(putRequest.error);
            };

            getRequest.onerror = () => reject(getRequest.error);
        });
    }

    async repairDatabase(): Promise<void> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async _lock => {
                return this._repairDatabase();
            });
        }
        return this._repairDatabase();
    }
    private async _repairDatabase(): Promise<void> {
        console.debug('Starting database repair...');

        if (this.db) {
            this.db.close();
            this.db = null;
        }

        this.initPromise = null;
        await this.init();

        const conversations = await this.getConversations();
        const validConversations = conversations.filter(conv =>
            this.validateConversations([conv])
        );

        if (validConversations.length < conversations.length) {
            await this.saveConversations(validConversations);
            message.success('Database repaired successfully');
        }
    }

    async clearDatabase(): Promise<void> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async _lock => {
                return this._clearDatabase();
            });
        }
        return this._clearDatabase();
    }

    private async _clearDatabase(): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');

        const tx = this.db.transaction([STORE_NAME], 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        return new Promise((resolve, reject) => {
            const clearRequest = store.clear();

            clearRequest.onsuccess = () => {
                console.debug('Database cleared successfully');
                resolve();
            };

            clearRequest.onerror = () => {
                console.error('Error clearing database:', clearRequest.error);
                reject(clearRequest.error);
            };

            tx.oncomplete = () => {
                console.debug('Clear transaction completed');
            };

            tx.onerror = () => {
                reject(tx.error);
            };
        });
    }

    async forceReset(): Promise<void> {
        return new Promise((resolve, reject) => {
            // Close existing connection if any
            if (this.db) {
                this.db.close();
                this.db = null;
            }

            // Delete the entire database
            const deleteRequest = indexedDB.deleteDatabase(currentDbName);

            deleteRequest.onsuccess = async () => {
                console.log('Database deleted successfully');
                try {
                    // Reset internal state
                    this.lastKnownVersion = 0;
                    this.initPromise = null;
                    this.saveInProgress = false;
                    this.migrated = false;

                    // Reinitialize the database
                    await this.init();
                    resolve();
                } catch (error) {
                    reject(error);
                }
            };

            deleteRequest.onerror = () => {
                reject(new Error('Failed to delete database'));
            };

            deleteRequest.onblocked = () => {
                reject(new Error('Database deletion blocked'));
            };
        });
    }

    async checkDatabaseHealth(): Promise<DatabaseHealth> {
        const health: DatabaseHealth = {
            isHealthy: false,
            errors: [],
            canRecover: true
        };

        try {
            // Check database connection
            if (!this.db) {
                try {
                    await this.init();
                } catch (error) {
                    health.errors.push('Failed to initialize database connection');
                    return health;
                }
            }

            // Check required stores exist
            const hasRequiredStores = this.db!.objectStoreNames.contains(STORE_NAME);
            if (!hasRequiredStores) {
                health.errors.push('Missing required object stores');
                return health;
            }

            // Try to read conversations
            try {
                const conversations = await this.getConversations();
                // Check if we can read data
                if (!Array.isArray(conversations)) {
                    health.errors.push('Invalid data structure in database');
                    return health;
                }

                // Validate conversations
                const invalidConversations = conversations.filter(
                    conv => !this.validateConversations([conv])
                );
                if (invalidConversations.length > 0) {
                    health.errors.push(`Found ${invalidConversations.length} invalid conversations`);
                    health.canRecover = true;
                }
            } catch (error) {
                health.errors.push('Failed to read conversations from database');
                return health;
            }

            // If we got here with no errors, database is healthy
            if (health.errors.length === 0) {
                health.isHealthy = true;
            }

            return health;
        } catch (error) {
            health.errors.push(`Unexpected error: ${error instanceof Error ? error.message : 'Unknown error'}`);
            return health;
        }
    }

    async searchConversations(query: string, options: SearchOptions = {}): Promise<SearchResult[]> {
        if (!query || query.trim().length === 0) {
            return [];
        }

        const { caseSensitive = false, maxSnippetLength = 150, projectId } = options;
        const searchTerm = caseSensitive ? query : query.toLowerCase();

        try {
            // Get all active conversations
            const conversations = await this.getConversations();
            const activeConversations = conversations.filter(conv => conv.isActive !== false);

            // Filter by project if requested
            const filteredConversations = projectId
                ? activeConversations.filter(conv => conv.projectId === projectId)
                : activeConversations;
            const results: SearchResult[] = [];

            for (const conv of filteredConversations) {
                const matches: MessageMatch[] = [];

                // Search through conversation title
                const titleToSearch = caseSensitive ? conv.title : conv.title.toLowerCase();
                const titleMatches = titleToSearch.includes(searchTerm);

                // Search through messages
                conv.messages.forEach((msg, index) => {
                    const contentToSearch = caseSensitive ? msg.content : msg.content.toLowerCase();

                    if (contentToSearch.includes(searchTerm)) {
                        // Find all occurrences in this message
                        const occurrences: Array<{ start: number; length: number }> = [];
                        let pos = 0;

                        while (pos < contentToSearch.length) {
                            const foundPos = contentToSearch.indexOf(searchTerm, pos);
                            if (foundPos === -1) break;

                            occurrences.push({
                                start: foundPos,
                                length: searchTerm.length
                            });
                            pos = foundPos + searchTerm.length;
                        }

                        if (occurrences.length > 0) {
                            // Create snippet around first occurrence
                            const firstOccurrence = occurrences[0];
                            const snippetStart = Math.max(0, firstOccurrence.start - 50);
                            const snippetEnd = Math.min(
                                msg.content.length,
                                firstOccurrence.start + searchTerm.length + 100
                            );

                            let snippet = msg.content.substring(snippetStart, snippetEnd);

                            // Add ellipsis if truncated
                            if (snippetStart > 0) snippet = '...' + snippet;
                            if (snippetEnd < msg.content.length) snippet = snippet + '...';

                            // Limit snippet length
                            if (snippet.length > maxSnippetLength) {
                                snippet = snippet.substring(0, maxSnippetLength) + '...';
                            }

                            matches.push({
                                messageIndex: index,
                                messageRole: msg.role,
                                snippet,
                                fullContent: msg.content,
                                timestamp: msg._timestamp || conv.lastAccessedAt || Date.now(),
                                highlightPositions: occurrences
                            });
                        }
                    }
                });

                // Add to results if there are matches in messages or title
                if (matches.length > 0 || titleMatches) {
                    results.push({
                        conversationId: conv.id,
                        conversationTitle: conv.title,
                        folderId: conv.folderId,
                        projectId: conv.projectId,
                        matches,
                        totalMatches: matches.length + (titleMatches ? 1 : 0),
                        lastAccessedAt: conv.lastAccessedAt || 0
                    });
                }
            }

            // Sort results by relevance (more matches first) and then by recency
            results.sort((a, b) => {
                // First by number of matches
                if (b.totalMatches !== a.totalMatches) {
                    return b.totalMatches - a.totalMatches;
                }
                // Then by recency
                return b.lastAccessedAt - a.lastAccessedAt;
            });

            console.log(`🔍 Search completed: found ${results.length} conversations with matches`);
            return results;
        } catch (error) {
            console.error('Error searching conversations:', error);
            return [];
        }
    }
}

export const db = new ConversationDB();

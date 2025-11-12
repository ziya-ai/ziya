import { Conversation, ConversationFolder } from './types';
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
    getConversations(): Promise<Conversation[]>;
    exportConversations(): Promise<string>;
    importConversations(data: string): Promise<void>;
    getFolders(): Promise<ConversationFolder[]>;
    saveFolder(folder: ConversationFolder): Promise<void>;
    deleteFolder(id: string): Promise<void>;
    moveConversationToFolder(conversationId: string, folderId: string | null): Promise<boolean>;
    repairDatabase(): Promise<void>;
    forceReset(): Promise<void>;
    checkDatabaseHealth(): Promise<DatabaseHealth>;
}

class ConversationDB implements DB {
    private saveInProgress = false;
    private lastSavedData: string | null = null;
    private lastKnownVersion: number = 0;
    private connectionAttempts = 0;
    private _pendingMigrationData: Conversation[] | null = null;
    private initializing = false;
    private initPromise: Promise<void> | null = null;

    db: IDBDatabase | null = null;

    async init(): Promise<void> {
        if (this.initPromise) return this.initPromise;

        this.initializing = true;
        if (navigator.locks) {
            this.initPromise = navigator.locks.request('ziya-db-init', async lock => {
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
                    checkRequest.result.close();

                    // Check for Safari migration issue - missing conversations store
                    const db = checkRequest.result;
                    const hasFolders = db.objectStoreNames.contains('folders');
                    const hasConversations = db.objectStoreNames.contains('conversations');
                    if (hasFolders && !hasConversations) {
                        console.warn('Safari migration issue detected: folders store exists but conversations store is missing');
                        // Auto-trigger emergency recovery
                        performEmergencyRecovery().then(() => {
                            console.log('Recovery completed, forcing page reload');
                            // Force reload to reinitialize everything
                            setTimeout(() => {
                                window.location.href = window.location.href;
                            }, 100);
                        }).catch(err => console.error('Auto-recovery failed:', err));
                        return;
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
                        console.warn('Database opening blocked. Closing other connections...');
                    };

                    dbRequest.onsuccess = () => {
                        this.db = dbRequest.result;

                        this.db.onversionchange = () => {
                            console.warn('Database version change detected, will reinitialize on next operation');
                            // Don't immediately close - let current operations complete
                            setTimeout(() => {
                                if (this.db && !this.saveInProgress) {
                                    this.db.close();
                                    this.db = null;
                                    this.initPromise = null;
                                }
                            }, 1000);
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
                        resolve();

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

    private validateConversations(conversations: Conversation[]): boolean {
        return conversations.every(conv =>
            typeof conv === 'object' &&
            typeof conv.id === 'string' &&
            conv.id.length > 0 &&
            typeof conv.title === 'string' &&
            Array.isArray(conv.messages) &&
            conv.messages.every(msg =>
                typeof msg === 'object' &&
                typeof msg.content === 'string' &&
                msg.content.length > 0 &&
                (msg.role === 'human' || msg.role === 'assistant')
            )
        );
    }

    private mergeConversations(local: Conversation[], remote: Conversation[]): Conversation[] {
        const merged = new Map<string, Conversation>();

        // Protect active conversations first
        const activeConvs = local.filter(conv =>
            conv.messages &&
            conv.messages.length > 0 &&
            conv.isActive !== false
        );

        console.debug('Merging conversations:', {
            localCount: local.length,
            remoteCount: remote.length,
            activeCount: activeConvs.length,
            localIds: local.map(c => c.id),
            remoteIds: remote.map(c => c.id)
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
        local.forEach(conv => {
            if (!merged.has(conv.id)) {
                merged.set(conv.id, {
                    ...conv,
                    _version: conv._version || Date.now(),
                    isActive: conv.isActive !== false
                });
            }
        });

        // Merge remote conversations
        remote.forEach(conv => {
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
            return navigator.locks.request('ziya-db-write', async lock => {
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
                                window.location.href = window.location.href;
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
            console.debug('Starting save operation:', {
                conversationCount: conversations.length,
                hasActiveConversations: conversations.some(c => c.messages?.length > 0 && c.isActive !== false)
            });

            const tx = this.db!.transaction([STORE_NAME], 'readwrite');
            console.debug('Transaction created successfully');
            const store = tx.objectStore(STORE_NAME);

            return new Promise<void>((resolve, reject) => {
                const conversationsToSave = conversations.map(conv => ({
                    ...conv,
                    _version: Date.now(),
                    messages: conv.messages.map(msg => ({
                        ...msg,
                        _timestamp: msg._timestamp || Date.now()
                    })),
                    lastAccessedAt: conv.lastAccessedAt || Date.now(),
                    isActive: conv.isActive !== false
                }));

                // Create a backup in localStorage before saving
                try {
                    const activeConversations = conversationsToSave.filter(c => c.isActive !== false);
                    if (activeConversations.length > 0) {
                        localStorage.setItem('ZIYA_CONVERSATION_BACKUP', JSON.stringify(activeConversations));
                        console.debug('Created backup of', activeConversations.length, 'conversations in localStorage');
                    }
                } catch (e) {
                    console.error('Error backing up conversations to localStorage:', e);
                }

                const putRequest = store.put(conversationsToSave, 'current');

                putRequest.onsuccess = () => {
                    console.debug('Save operation completed successfully:', {
                        savedCount: conversationsToSave.length,
                        savedIds: conversationsToSave.map(c => c.id)
                    });
                    this.lastSavedData = JSON.stringify(conversationsToSave);
                    saveCompleted = true;
                };

                putRequest.onerror = () => {
                    console.error('Save operation failed:', putRequest.error);
                    reject(putRequest.error);
                };

                tx.oncomplete = () => {
                    console.debug('Transaction completed');
                    if (!saveCompleted) {
                        reject(new Error('Transaction completed but save operation did not complete'));
                        return;
                    }
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
        try {
            const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
            if (backup) {
                const conversations = JSON.parse(backup);
                if (Array.isArray(conversations) && conversations.length > 0) {
                    console.log('Restoring', conversations.length, 'conversations from backup');

                    // Save to database
                    try {
                        await this.saveConversations(conversations);
                        console.log('Successfully restored conversations to database');
                    } catch (e) {
                        console.error('Failed to save restored conversations to database:', e);
                    }

                    return conversations;
                }
            }
        } catch (e) {
            console.error('Error restoring from backup:', e);
        }

        // Return empty array if no backup or error
        return [];
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
            return navigator.locks.request('ziya-db-read', async lock => {
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

        let result: Conversation[] = [];

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
            const request = store.get('current');

            request.onsuccess = () => {
                // Only log in development mode and with less frequency
                if (process.env.NODE_ENV === 'development' && Math.random() < 0.1) { // Only log ~10% of the time
                    console.debug('Successfully retrieved conversations from database');
                }
                const conversations = Array.isArray(request.result) ? request.result : [];

                if (conversations.length > 0) {
                    const validConversations = conversations.filter(conv =>
                        conv && this.validateConversations([conv])
                    );

                    if (validConversations.length > 0) {
                        result = validConversations;
                        resolve(validConversations);
                        return;
                    }
                }
                resolve([]);
                // If we got no valid conversations, try to restore from backup
                this.restoreFromBackup().then(backupConversations => {
                    result = backupConversations;
                    resolve(backupConversations);
                }).catch(() => resolve([]));
            };

            request.onerror = () => {
                reject(request.error);
            };
        });
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
            return navigator.locks.request('ziya-db-read', async lock => {
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
            const request = store.get('current');
            return new Promise((resolve, reject) => {
                request.onsuccess = () => {
                    const conversations = Array.isArray(request.result) ? request.result : [];
                    const activeConversations = conversations.filter(conv => conv.isActive !== false);

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

    async importConversations(data: string): Promise<void> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async lock => {
                return this._importConversations(data);
            });
        }
        return this._importConversations(data);
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
    
    private async _importConversations(data: string): Promise<void> {
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
            const processedConversations = newConversations.map(conv => ({
                ...conv,
                isActive: true,
                _version: conv._version || Date.now(),
                lastAccessedAt: conv.lastAccessedAt || Date.now()
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
            if (importedFolders.length > 0) {
                // Get existing folders to avoid duplicates
                const existingFolders = await this.getFolders();
                const existingFolderIds = new Set(existingFolders.map(f => f.id));

                // Only import folders that don't already exist
                const newFolders = importedFolders.filter(folder => !existingFolderIds.has(folder.id));
                
                console.log(`📁 IMPORT: Adding ${newFolders.length} new folders (${importedFolders.length - newFolders.length} already exist)`);

                // Save new folders
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

            // Merge conversations, keeping existing ones if IDs conflict
            const mergedConversations = [...existingConversations, ...validConversations];
            
            console.log(`💾 IMPORT: Final merge - ${existingConversations.length} existing + ${validConversations.length} new = ${mergedConversations.length} total`);
            
            // Start a transaction
            const tx = this.db.transaction([STORE_NAME], 'readwrite');
            const store = tx.objectStore(STORE_NAME);
            
            return new Promise((resolve, reject) => {
                const request = store.put(mergedConversations, 'current');

                request.onsuccess = () => {
                    console.log(`✅ IMPORT COMPLETE: Saved ${mergedConversations.length} total conversations`);
                    resolve();
                };

                request.onerror = () => reject(request.error);
                
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
            const getRequest = store.get('current');

            getRequest.onsuccess = () => {
                const conversations = Array.isArray(getRequest.result) ? getRequest.result : [];
                let found = false;

                // Log the conversation being moved
                console.log('Moving conversation to folder:', { conversationId, folderId });

                const updatedConversations = conversations.map(conv => {
                    if (conv.id === conversationId) {
                        found = true;
                        return { ...conv, folderId, _version: Date.now() };
                    }
                    return conv;
                });

                // Only update if the conversation was found
                if (found) {
                    const putRequest = store.put(updatedConversations, 'current');
                    putRequest.onsuccess = () => resolve(true);
                    putRequest.onerror = () => reject(putRequest.error);
                } else {
                    resolve(false);
                }
            };

            getRequest.onerror = () => reject(getRequest.error);
        });
    }

    async repairDatabase(): Promise<void> {
        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async lock => {
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
            return navigator.locks.request('ziya-db-write', async lock => {
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
                this.lastSavedData = null;
                this.lastKnownVersion = 0;
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
                    this.lastSavedData = null;
                    this.lastKnownVersion = 0;
                    this.initPromise = null;
                    this.saveInProgress = false;

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
}

export const db = new ConversationDB();

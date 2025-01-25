import { Conversation } from './types';
import { message } from 'antd';

declare global {
    interface Navigator {
        locks?: {
            request(name: string, callback: (lock: any) => Promise<any>): Promise<any>;
        };
    }
}

const DB_BASE_NAME = 'ZiyaDB';
let currentDbName = DB_BASE_NAME;
let currentVersion = 2;
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
    private initializing = true;
    private initPromise: Promise<void> | null = null;

    db: IDBDatabase | null = null;

    async init(): Promise<void> {
        if (this.initPromise) return this.initPromise;

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
            const checkRequest = indexedDB.open(currentDbName);

	    return new Promise((resolve, reject) => {
                checkRequest.onsuccess = () => {
                    const existingVersion = checkRequest.result.version;
                    checkRequest.result.close();

                    // Use existing version if it's higher
                    if (existingVersion > currentVersion) {
                        currentVersion = existingVersion;
                    }

                    console.debug('Database version check:', {
                        existingVersion,
                        usingVersion: currentVersion
                    });

		    // Now open with correct version
                    const dbRequest = indexedDB.open(currentDbName, currentVersion);

                    dbRequest.onerror = () => {
                        console.error('Database initialization error:', dbRequest.error);
                        this.initPromise = null;
                        reject(dbRequest.error);
                    };

		    dbRequest.onsuccess = () => {
                        this.db = dbRequest.result;

                        this.db.onversionchange = () => {
                            this.db?.close();
                            this.db = null;
                            this.initPromise = null;
                        };

                        this.db.onclose = () => {
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
                    console.debug('Upgrading database schema...');
                    const db = (event.target as IDBOpenDBRequest).result;
                    
                    if (!db.objectStoreNames.contains(STORE_NAME)) {
                        db.createObjectStore(STORE_NAME);
                    }
                    if (!db.objectStoreNames.contains(BACKUP_STORE_NAME)) {
                        db.createObjectStore(BACKUP_STORE_NAME);
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
                throw new Error('Database initialization failed');
            }
            if (!this.db) throw new Error('Database not initialized');
        }

        if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async lock => {
                return this._saveConversationsWithLock(conversations);
            });
        }
        return this._saveConversationsWithLock(conversations);
    }

    private async _saveConversationsWithLock(conversations: Conversation[]): Promise<void> {
        if (this.saveInProgress) {
            console.warn('Save already in progress, skipping');
            return;
        }

        this.saveInProgress = true;
        let saveCompleted = false;

        try {
            console.debug('Starting save operation:', {
                conversationCount: conversations.length,
                hasActiveConversations: conversations.some(c => c.messages?.length > 0 && c.isActive !== false)
            });

            const tx = this.db!.transaction([STORE_NAME], 'readwrite');
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
                    if (!saveCompleted) {
                        reject(new Error('Transaction completed but save operation did not complete'));
                        return;
                    }
                    resolve();
                };

                tx.onerror = () => {
                    reject(tx.error);
                };
            });
        } finally {
            this.saveInProgress = false;
        }
    }

    async getConversations(): Promise<Conversation[]> {
        if (!this.db || !this.db.objectStoreNames.contains(STORE_NAME)) {
            try {
                await this.init();
            } catch (error) {
                console.error('Failed to initialize database:', error);
                throw new Error('Database initialization failed');
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
        const tx = this.db!.transaction([STORE_NAME], 'readonly');
        const store = tx.objectStore(STORE_NAME);

        return new Promise<Conversation[]>((resolve, reject) => {
            const request = store.get('current');

            request.onsuccess = () => {
                const conversations = Array.isArray(request.result) ? request.result : [];
                console.debug('Retrieved conversations:', {
                    count: conversations.length,
                    ids: conversations.map(c => c.id)
                });

                if (conversations.length > 0) {
                    const validConversations = conversations.filter(conv =>
                        conv && this.validateConversations([conv])
                    );

                    if (validConversations.length > 0) {
                        resolve(validConversations);
                        return;
                    }
                }
                resolve([]);
            };

            request.onerror = () => {
                reject(request.error);
            };
        });
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
        const conversations = await this.getConversations();
        return JSON.stringify(conversations, null, 2);
    }

    async importConversations(data: string): Promise<void> {
	if (navigator.locks) {
            return navigator.locks.request('ziya-db-write', async lock => {
                return this._importConversations(data);
            });
        }
        return this._importConversations(data);
    }
    private async _importConversations(data: string): Promise<void> {
        try {
            const conversations = JSON.parse(data);
            if (!this.validateConversations(conversations)) {
                throw new Error('Invalid conversations format');
            }
            await this.saveConversations(conversations);
        } catch (error) {
            throw new Error(`Import failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
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

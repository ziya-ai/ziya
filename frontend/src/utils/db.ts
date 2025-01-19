import { Conversation } from './types';
import { message } from 'antd';
 
const DB_NAME = 'ZiyaDB';
const DB_VERSION = 1;
const STORE_NAME = 'conversations';
const BACKUP_STORE_NAME = 'conversationsBackup';
 
interface DB {
    db: IDBDatabase | null;
    init(): Promise<void>;
    saveConversations(conversations: Conversation[]): Promise<void>;
    getConversations(): Promise<Conversation[]>;
    exportConversations(): Promise<string>;
    importConversations(data: string): Promise<void>;
}
 
class ConversationDB implements DB {
    private saveInProgress = false;
    private lastSavedData: string | null = null;
    private lastKnownVersion: number = 0;
    private initializing = true;

    db: IDBDatabase | null = null;
 
    async init(): Promise<void> {
        return new Promise((resolve, reject) => {
	    console.log('Initializing database...');
            const request = indexedDB.open(DB_NAME, DB_VERSION);
	    console.log('Initializing ZiyaDB...');
 
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
		this.initializing = false;
                resolve();
		console.log('Database initialized successfully', {
                    name: this.db.name,
                    version: this.db.version,
                    stores: Array.from(this.db.objectStoreNames)
                });
            };
 
            request.onupgradeneeded = (event) => {
		console.log('Upgrading database schema...');
                const db = (event.target as IDBOpenDBRequest).result;
                
                // Create main store
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME);
                }
                
                // Create backup store
                if (!db.objectStoreNames.contains(BACKUP_STORE_NAME)) {
                    db.createObjectStore(BACKUP_STORE_NAME);
                }
            };
	    request.onblocked = () => console.error('Database upgrade was blocked');
            request.onerror = (event) => {
                const error = request.error?.message || 'Unknown database error';
                console.error('Database initialization error:', error);
                reject(new Error(`Database initialization failed: ${error}`));
            };
	});
    }
 
    async checkDatabaseHealth(): Promise<{
        isHealthy: boolean;
        errors: string[];
        canRecover: boolean;
    }> {
        return new Promise((resolve) => {
            const request = indexedDB.open(DB_NAME);
            request.onsuccess = () => {
                const db = request.result;
                const hasRequiredStores = db.objectStoreNames.contains(STORE_NAME) &&
                    db.objectStoreNames.contains(BACKUP_STORE_NAME);
                resolve({
                    isHealthy: hasRequiredStores,
                    errors: hasRequiredStores ? [] : ['Missing required object stores'],
                    canRecover: true
                });
            };
	 });
    }

    private validateConversations(conversations: Conversation[]): boolean {
        return conversations.every(conv =>
            typeof conv === 'object' &&
            Boolean(conv.id) && // Allow any non-null ID
            Boolean(conv.title) && // Allow any non-null title
            Array.isArray(conv.messages) &&
            conv.messages.every(msg => 
                typeof msg === 'object' &&
		Boolean(msg.content) && // Allow any non-null content
                (msg.role === 'human' || msg.role === 'assistant')
            )
        );
    }

    private logValidationFailure(conv: Conversation): void {
        console.warn('Validation failed for conversation:', {
            id: conv.id,
            title: conv.title,
            hasMessages: Array.isArray(conv.messages),
            messageCount: conv.messages?.length,
            invalidMessages: conv.messages?.filter(msg =>
                !msg.content || !['human', 'assistant'].includes(msg.role)
            )
        });
    }

    private logConversationStats(conversations: Conversation[]) {
        console.log('Conversation statistics:');
        console.log(`Total conversations: ${conversations.length}`);
        const validCount = conversations.filter(conv => this.validateConversations([conv])).length;
        console.log(`Valid conversations: ${validCount}`);
	const invalidCount = conversations.length - validCount;
        console.log(`Invalid conversations: ${invalidCount}`);

        // Log details of invalid conversations
        conversations.forEach((conv, index) => {
            if (!this.validateConversations([conv])) {
		this.logValidationFailure(conv);
            }
        });
    }

    private lastMergedVersion: number = 0;
    private async hasDataChanged(conversations: Conversation[]): Promise<boolean> {
        try {
            // First check our cached last saved state
            const newData = JSON.stringify(conversations);
            if (newData === this.lastSavedData) {
                console.debug('No changes detected from last save, skipping');
                return false;
            }

            // Then check current database state
            const currentData = await this.getConversations();

	    // If this is a new conversation, always save
            const hasNewConversation = conversations.some(conv =>
                !currentData.find(c => c.id === conv.id)
            );


            // Check if we have a version conflict
            const maxRemoteVersion = Math.max(...currentData.map(c => c._version || 0));
            if (maxRemoteVersion > this.lastKnownVersion) {
                console.debug('Version conflict detected, merging changes');
                // Merge the changes
                const mergedConversations = this.mergeConversations(conversations, currentData);
                // Update our version tracking
		this.lastKnownVersion = maxRemoteVersion;
                this.lastMergedVersion = Date.now();
                // Save the merged result instead of original
                await this.saveConversations(mergedConversations);
                return false; // Skip the original save since we've handled the merge
            }

	    // Always save if we have a new conversation
            if (hasNewConversation) {
                return true;
            }

            return true;
        } catch (error) {
            console.error('Error checking for changes:', error);
            return true; // If check fails, attempt save anyway
        }
    }

    private updateLastSavedData(conversations: Conversation[]) {
	console.debug(`Updating last saved data with ${conversations.length} conversations`);
        this.lastSavedData = JSON.stringify(conversations);
    }

    private mergeConversations(local: Conversation[], remote: Conversation[]): Conversation[] {
        const merged = new Map<string, Conversation>();

        // First, add all local conversations to the map
        local.forEach(conv => {
	    const isNewConv = !remote.find(r => r.id === conv.id);
            merged.set(conv.id, {
                ...conv,
		_version: conv._version || this.lastMergedVersion || Date.now(),
		isNew: isNewConv,
                isActive: conv.isActive || isNewConv // Ensure new conversations are active
            });
        });

        // Then, merge remote conversations, keeping the newer version
	for (const conv of remote) {
            const localConv = merged.get(conv.id);
	    // Skip if local conversation exists and is marked as inactive
            if (localConv?.isActive === false) {
                console.debug(`Keeping local inactive state for ${conv.id}`);
                continue;
            }

            // Only update if remote version is newer
            if (!localConv || (conv._version && localConv._version && conv._version > localConv._version)) {
                merged.set(conv.id, {
                    ...conv,
                    _version: conv._version || this.lastMergedVersion || Date.now(),
                    isNew: false
                });
                console.debug(`Merge decision for ${conv.id}:`, { action: localConv?.isNew ? 'kept local' : 'used remote' });
            }
        }

        return Array.from(merged.values()).sort((a, b) => {
	    // Sort by lastAccessedAt, putting new conversations first
            if (a.isNew && !b.isNew) return -1;
            if (!a.isNew && b.isNew) return 1;

            const aTime = a.lastAccessedAt || 0;
            const bTime = b.lastAccessedAt || 0;
	    return bTime - aTime;
        });
    }

    private logMergeResults(mergedConversations: Conversation[]): void {
        console.debug('Merged conversations:', mergedConversations.map(c => ({ id: c.id, isNew: c.isNew })));
    }

    async saveConversations(conversations: Conversation[]): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');

	// During initialization, just save without checking for changes
        if (this.initializing) {
            console.debug('Initial save, skipping change detection');
            await this._forceSave(conversations);
            return;
        }

	// Prevent concurrent saves
        if (this.saveInProgress) {
            console.warn('Save already in progress, skipping');
            return;
        }
        if (!await this.hasDataChanged(conversations)) {
            console.debug('No data changes detected, skipping save');
	    return;
	}

    	this.saveInProgress = true;
	console.debug('Starting save operation:', { conversationCount: conversations.length });
        
        if (!this.validateConversations(conversations)) {
	    console.warn('Some conversations failed validation but proceeding with save');
        }
 
	this.logConversationStats(conversations);
	
        
        try {
            // Check if this is a new conversation
            const currentData = await this.getConversations();
            const hasNewConversation = conversations.some(conv => 
                !currentData.find(c => c.id === conv.id)
            );
 
            if (hasNewConversation) {
                console.debug('New conversation detected, forcing save');
                await this._forceSave(conversations);
                return;
            }
		
            // First, backup current state
            const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readwrite');
            const store = tx.objectStore(STORE_NAME);
            const backupStore = tx.objectStore(BACKUP_STORE_NAME);
            
	    console.log('Starting transaction to save conversations:', conversations.length);
            return new Promise<void>((resolve, reject) => {
                // Get current state for backup
                const getRequest = store.get('current');
                
		console.debug('Retrieving current state for backup');
                getRequest.onsuccess = () => {
                    if (getRequest.result) {
			console.debug('Backing up current state before save');
                        backupStore.put(getRequest.result, 'backup');
                    }

		    // Keep most recent 100 conversations
		    const conversationsToSave = conversations.slice(-100).map(conv => ({
                        ...conv,
                        _version: Date.now(),
                        messages: conv.messages.map(msg => ({
                            ...msg,
                            _timestamp: msg._timestamp || Date.now()
                        }))
                    }));
		    console.debug('Preparing to save conversations:', {
                        count: conversationsToSave.length,
                        firstId: conversationsToSave[0]?.id,
                        lastId: conversationsToSave[conversationsToSave.length - 1]?.id
                    });
		    this.updateLastSavedData(conversationsToSave);
                    
                    // Save new state
                    const putRequest = store.put(conversationsToSave, 'current');
		    putRequest.onsuccess = () => {
			console.debug('Save operation completed successfully');
                        resolve();
                    };

		    putRequest.onerror = () => {
                        reject(putRequest.error);
                    };

		    tx.oncomplete = () => {
                        console.debug('Transaction completed successfully');
                    };
                };

                getRequest.onerror = () => {
                    reject(getRequest.error);
                };
                tx.oncomplete = () => {
                    resolve();
                };
 
                tx.onerror = () => {
                    reject(tx.error);
                };
            });
        } finally {
	    console.log('Save transaction completed');	
            this.saveInProgress = false;
        }
    }
 
    private async _forceSave(conversations: Conversation[]): Promise<void> {
	if (!this.db) {
            throw new Error('Database not initialized');
        }

	console.debug('Force saving conversations:', {
            total: conversations.length,
            active: conversations.filter(c => c.isActive).length,
            inactive: conversations.filter(c => !c.isActive).length,
            activeIds: conversations.filter(c => c.isActive).map(c => c.id),
            inactiveIds: conversations.filter(c => !c.isActive).map(c => c.id)
        });
        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const backupStore = tx.objectStore(BACKUP_STORE_NAME);

        return new Promise<void>((resolve, reject) => {
            // Backup current state
            const getRequest = store.get('current');
            getRequest.onsuccess = () => {
                if (getRequest.result) {
                    backupStore.put(getRequest.result, 'backup');
                }

                // Force save new state
                const putRequest = store.put(conversations, 'current');
                putRequest.onsuccess = () => {
                    console.debug('Force save completed successfully');
                    resolve();
                };
                putRequest.onerror = () => reject(putRequest.error);
            };
            getRequest.onerror = () => reject(getRequest.error);
        });
    }

    async getConversations(): Promise<Conversation[]> {
        if (!this.db) throw new Error('Database not initialized');
 
        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const backupStore = tx.objectStore(BACKUP_STORE_NAME);
	let recoveredFromBackup = false;
 
        return new Promise((resolve, reject) => {
            const request = store.get('current');
            
            request.onsuccess = () => {
		let conversations = request.result || [];
		console.log('Retrieved conversations from store:', conversations?.length || 0);

                if (Array.isArray(conversations) && conversations.length > 0) {
                    // Filter out invalid conversations
                    const validConversations = conversations.filter(conv =>
                        this.validateConversations([conv])
                    );

                    if (validConversations.length > 0) {
                        resolve(validConversations);
                        return;
                    }
                }

                // If we get here, either no conversations or all invalid
                console.warn('No valid conversations found in main store, attempting backup recovery...');

                // Try to recover from backup
                const backupRequest = backupStore.get('backup');
                backupRequest.onsuccess = () => {
                    const backupConversations = backupRequest.result || [];
                    const validBackupConversations = backupConversations.filter(conv =>
                        this.validateConversations([conv])
                    );

                    if (validBackupConversations.length > 0) {
                        console.log(`Recovered ${validBackupConversations.length} conversations from backup`);
                        message.info('Recovered conversations from backup store');
                        recoveredFromBackup = true;
                    }
                    resolve(validBackupConversations);
                };
                backupRequest.onerror = () => {
                    console.error('Failed to recover from backup');
                    resolve([]);
                };
            };

	    request.onerror = () => reject(request.error);

            request.onerror = () => {
                console.error('Error reading conversations:', request.error);
                reject(request.error);
            };
        });
    }

    async forceReset(): Promise<void> {
        return new Promise((resolve, reject) => {
            // Delete the entire database
            const deleteRequest = indexedDB.deleteDatabase(DB_NAME);

            deleteRequest.onsuccess = async () => {
                console.log('Database deleted successfully');
                try {
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

    async isAccessible(): Promise<boolean> {
        if (!this.db) return false;
        try {
            await this.getConversations();
            return true;
        } catch (error) {
            return false;
        }
    }

    async repairDatabase(): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');

        try {
            // Get all conversations
            const conversations = await this.getConversations();

            // Filter out invalid conversations
            const validConversations = conversations.filter(conv =>
                this.validateConversations([conv])
            );

            if (validConversations.length < conversations.length) {
                console.log(`Removed ${conversations.length - validConversations.length} invalid conversations`);
                // Save valid conversations back to database
                await this.saveConversations(validConversations);
                message.success('Successfully repaired conversation database');
            }
        } catch (error) {
            console.error('Error repairing database:', error);
            message.error('Failed to repair conversation database');
        }
    }

    async clearDatabase(): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');

        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const backupStore = tx.objectStore(BACKUP_STORE_NAME);

        return new Promise((resolve, reject) => {
            store.clear();
            backupStore.clear();

            tx.oncomplete = () => {
                console.log('Database cleared successfully');
                resolve();
            };
            tx.onerror = () => {
                console.error('Error clearing database:', tx.error);
                reject(tx.error);
            };
        });
    }

    async exportConversations(): Promise<string> {
        const conversations = await this.getConversations();
        return JSON.stringify(conversations, null, 2);
    }
 
    async importConversations(data: string): Promise<void> {
        try {
            const conversations = JSON.parse(data);
            if (!this.validateConversations(conversations)) {
                throw new Error('Invalid conversations format');
            }
            await this.saveConversations(conversations);
        } catch (error) {
            const errorMessage = error instanceof Error
                ? error.message
                : 'Unknown error during import';

            throw new Error(`Import failed: ${errorMessage}`);
        }
    }
}
 
export const db = new ConversationDB();

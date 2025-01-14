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
    db: IDBDatabase | null = null;
 
    async init(): Promise<void> {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
	    console.log('Initializing ZiyaDB...');
 
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
                resolve();
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
 
    async saveConversations(conversations: Conversation[]): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');

	// Prevent concurrent saves
        if (this.saveInProgress) {
            console.warn('Save already in progress, skipping');
            return;
        }
        this.saveInProgress = true;
        
        if (!this.validateConversations(conversations)) {
	    console.warn('Some conversations failed validation but proceeding with save');
        }
 
	this.logConversationStats(conversations);
	
        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readwrite');
        
        try {
            // First, backup current state
            const backupStore = tx.objectStore(BACKUP_STORE_NAME);
            const store = tx.objectStore(STORE_NAME);
            
            return new Promise<void>((resolve, reject) => {
                // Get current state for backup
                const getRequest = store.get('current');
                
                getRequest.onsuccess = () => {
                    if (getRequest.result) {
                        backupStore.put(getRequest.result, 'backup');
                    }

		    // Keep most recent 100 conversations
                    const conversationsToSave = conversations.slice(-100);
                    
                    // Save new state
                    const putRequest = store.put(conversationsToSave, 'current');

		    putRequest.onsuccess = () => {
                        resolve();
                    };

		    putRequest.onerror = () => {
                        reject(putRequest.error);
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
            this.saveInProgress = false;
        }
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

                if (Array.isArray(conversations) && conversations.length > 0) {
                    // Filter out invalid conversations
                    const validConversations = conversations.filter(conv =>
                        this.validateConversations([conv])
                    );

                    if (validConversations.length > 0) {
                        console.log(`Retrieved ${validConversations.length} valid conversations`);
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

            request.onerror = () => {
                console.error('Error reading conversations:', request.error);
                reject(request.error);
            };
        });
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

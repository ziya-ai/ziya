import { Conversation } from './types';
 
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
    db: IDBDatabase | null = null;
 
    async init(): Promise<void> {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
 
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
                resolve();
            };
 
            request.onupgradeneeded = (event) => {
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
        });
    }
 
    private validateConversations(conversations: Conversation[]): boolean {
        return conversations.every(conv =>
            typeof conv === 'object' &&
            typeof conv.id === 'string' &&
            typeof conv.title === 'string' &&
            Array.isArray(conv.messages) &&
            conv.messages.every(msg =>
                typeof msg === 'object' &&
                typeof msg.content === 'string' &&
                (msg.role === 'human' || msg.role === 'assistant')
            )
        );
    }
 
    async saveConversations(conversations: Conversation[]): Promise<void> {
        if (!this.db) throw new Error('Database not initialized');
        
        if (!this.validateConversations(conversations)) {
            throw new Error('Invalid conversations structure');
        }
 
        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readwrite');
        
        // First, backup current state
        const backupStore = tx.objectStore(BACKUP_STORE_NAME);
        const store = tx.objectStore(STORE_NAME);
        
        return new Promise((resolve, reject) => {
            // Get current state for backup
            const getRequest = store.get('current');
            
            getRequest.onsuccess = () => {
                if (getRequest.result) {
                    backupStore.put(getRequest.result, 'backup');
                }
                
                // Save new state
                store.put(conversations.slice(-50), 'current');
            };
 
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    }
 
    async getConversations(): Promise<Conversation[]> {
        if (!this.db) throw new Error('Database not initialized');
 
        const tx = this.db.transaction([STORE_NAME, BACKUP_STORE_NAME], 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const backupStore = tx.objectStore(BACKUP_STORE_NAME);
 
        return new Promise((resolve, reject) => {
            const request = store.get('current');
            
            request.onsuccess = () => {
                if (request.result && this.validateConversations(request.result)) {
                    resolve(request.result);
                } else {
                    // Try to recover from backup
                    const backupRequest = backupStore.get('backup');
                    backupRequest.onsuccess = () => {
                        resolve(backupRequest.result || []);
                    };
                }
            };
            
            request.onerror = () => reject(request.error);
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

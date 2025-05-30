// emergencyRecovery.js
export async function performEmergencyRecovery() {
  console.log('Starting emergency database recovery for Safari');
  let savedConversations = null;

  // Check for recovery loop
  const recoveryAttempts = parseInt(localStorage.getItem('ZIYA_RECOVERY_ATTEMPTS') || '0', 10);
  if (recoveryAttempts > 3) {
    console.error('Too many recovery attempts, preventing recovery loop');
    localStorage.removeItem('ZIYA_RECOVERY_ATTEMPTS'); // Reset for next session
    return {
      success: false,
      message: 'Too many recovery attempts. Please clear your browser data and try again.'
    };
  }
  localStorage.setItem('ZIYA_RECOVERY_ATTEMPTS', (recoveryAttempts + 1).toString());

  const recoverySteps = [];

  // Close any existing connections
  if (window.indexedDB) {
    const dbName = 'ZiyaDB';
    const currentVersion = 3; // Current version in your code

    // First try to get the current version
    const checkRequest = indexedDB.open(dbName);
    await new Promise((resolve, reject) => {
      checkRequest.onsuccess = () => {
        const db = checkRequest.result;
        console.log('Current database version:', db.version);
        console.log('Object stores:', Array.from(db.objectStoreNames));

        // Verify that all required stores were created
        const requiredStores = ['conversations', 'folders', 'conversationsBackup'];
        const missingStores = requiredStores.filter(store => !db.objectStoreNames.contains(store));

        if (missingStores.length > 0) {
          console.error('Database recovery failed: missing stores:', missingStores);
          recoverySteps.push(`Recovery failed: missing stores: ${missingStores.join(', ')}`);
          reject(new Error(`Failed to create all required stores: ${missingStores.join(', ')}`));
          return;
        }

        recoverySteps.push('Schema verification successful');

        // Check if we're missing the conversations store but have the folders store
        const hasFolders = db.objectStoreNames.contains('folders');
        const hasConversations = db.objectStoreNames.contains('conversations');

        // Try to backup conversations from localStorage first
        try {
          const localBackup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
          if (localBackup) {
            savedConversations = JSON.parse(localBackup);
            console.log('Found conversation backup in localStorage:', savedConversations.length);
            recoverySteps.push(`Found ${savedConversations.length} conversations in localStorage backup`);
          } else {
            // Try to get conversations from IndexedDB if available
            // We need to handle this synchronously since we're in a callback
            if (hasConversations) {
              try {
                const tx = db.transaction(['conversations'], 'readonly');
                const store = tx.objectStore('conversations');
                const request = store.get('current');
                request.onsuccess = function() {
                  savedConversations = request.result || [];
                  console.log('Retrieved', savedConversations.length, 'conversations from IndexedDB');
                };
              } catch (err) {
                console.error('Error retrieving conversations from IndexedDB:', err);
              }
            }
          }
        } catch (e) {
          console.error('Error backing up conversations:', e);
        }

        recoverySteps.push(`Initial check: folders=${hasFolders}, conversations=${hasConversations}`);
        if (hasFolders && !hasConversations) {
          console.log('Safari migration issue detected: folders store exists but conversations store is missing');
        }
        db.close();
        resolve();
      };
      checkRequest.onerror = () => {
        console.error('Error checking database:', checkRequest.error);
        reject(checkRequest.error);
      };

      // Create a backup in localStorage before deleting the database
      if (savedConversations) {
        try {
          localStorage.setItem('ZIYA_CONVERSATION_BACKUP', JSON.stringify(savedConversations));
          recoverySteps.push('Created backup of conversations in localStorage');
        } catch (e) {
          console.error('Error saving conversations to localStorage:', e);
        }
      }
    });

    // Delete the database to start fresh
    console.log('Deleting database to recreate with proper schema');
    recoverySteps.push('Deleting database to recreate schema');
    await new Promise((resolve, reject) => {
      const deleteRequest = indexedDB.deleteDatabase(dbName);
      deleteRequest.onsuccess = () => {
        console.log('Database deleted successfully');
        resolve();
      };
      deleteRequest.onerror = () => {
        console.error('Error deleting database:', deleteRequest.error);
        reject(deleteRequest.error);
      };
      deleteRequest.onblocked = () => {
        console.warn('Database deletion blocked');
        setTimeout(resolve, 1000);
      };
    });

    // Create a new database with the correct schema
    console.log('Creating new database with correct schema');
    const newVersion = currentVersion + 1;
    const openRequest = indexedDB.open(dbName, newVersion);

    await new Promise((resolve, reject) => {
      openRequest.onupgradeneeded = (event) => {
        console.log('Upgrading database to version', newVersion);
        recoverySteps.push(`Creating new database with version ${newVersion}`);
        const db = event.target.result;

        // Create all required stores
        if (!db.objectStoreNames.contains('conversations')) {
          console.log('Creating conversations store');
          db.createObjectStore('conversations');
        }

        if (!db.objectStoreNames.contains('folders')) {
          console.log('Creating folders store');
          db.createObjectStore('folders', { keyPath: 'id' });
        }

        if (!db.objectStoreNames.contains('conversationsBackup')) {
          console.log('Creating conversationsBackup store');
          db.createObjectStore('conversationsBackup');
        }
      };

      // Function to restore conversations after database is created
      const restoreConversations = (db) => {
        if (!savedConversations || !Array.isArray(savedConversations) || savedConversations.length === 0) {
          console.log('No conversations to restore');
          return;
        }

        try {
          console.log('Restoring', savedConversations.length, 'conversations');
          recoverySteps.push(`Restoring ${savedConversations.length} conversations`);

          const tx = db.transaction(['conversations'], 'readwrite');
          const store = tx.objectStore('conversations');

          const request = store.put(savedConversations, 'current');
          request.onsuccess = function() {
            console.log('Successfully restored conversations');
          };
          request.onerror = function() {
            console.error('Failed to restore conversations:', request.error);
          };
        } catch (e) {
          console.error('Error restoring conversations:', e);
        }
      };

      openRequest.onsuccess = () => {
        console.log('Database created successfully');
        const db = openRequest.result;
        console.log('New database version:', db.version);
        recoverySteps.push(`Database created with stores: ${Array.from(db.objectStoreNames).join(', ')}`);
        console.log('Object stores:', Array.from(db.objectStoreNames));
        db.close();

        // Restore conversations
        restoreConversations(db);
        resolve();
      };

      openRequest.onblocked = () => {
        reject(new Error('Database creation blocked'));
      };

      openRequest.onerror = () => {
        console.error('Error creating database:', openRequest.error);
        reject(openRequest.error);
      };
    });

    // Avoid using object spread syntax
    const result = {
      success: true,
      message: 'Database repair completed. Please refresh the page.'
    };
    result.steps = recoverySteps;

    // Reset recovery attempts counter on success
    localStorage.removeItem('ZIYA_RECOVERY_ATTEMPTS');

    // Force a reload after a short delay to ensure all operations complete
    setTimeout(() => {
      window.location.href = window.location.href; // Force a hard reload
    }, 500);

    return result;
  } else {
    recoverySteps.push('IndexedDB not supported');
    const result = {
      success: false,
      message: 'IndexedDB not supported in this browser'
    };
    result.steps = recoverySteps;
    return result;
  }
}





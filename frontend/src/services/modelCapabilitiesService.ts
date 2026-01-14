// Singleton service to manage model capabilities - fetch ONCE on app load

interface ModelCapabilities {
    supports_vision?: boolean;
    token_limit?: number;
    max_input_tokens?: number;
    max_output_tokens?: number;
    [key: string]: any;
}

class ModelCapabilitiesService {
    private cache: ModelCapabilities | null = null;
    private fetchPromise: Promise<ModelCapabilities> | null = null;

    async getCapabilities(): Promise<ModelCapabilities> {
        // Return cached data immediately if available
        if (this.cache) {
            return this.cache;
        }

        // If already fetching, return the existing promise
        if (this.fetchPromise) {
            return this.fetchPromise;
        }

        // Fetch once and cache forever (until model change event)
        this.fetchPromise = fetch('/api/model-capabilities')
            .then(async (response) => {
                if (!response.ok) {
                    throw new Error(`Failed to fetch capabilities: ${response.status}`);
                }
                this.cache = await response.json();
                return this.cache!;
            });

        return this.fetchPromise;
    }

    // Only invalidate when model actually changes
    invalidateCache() {
        this.cache = null;
        this.fetchPromise = null;
    }
}

export const modelCapabilitiesService = new ModelCapabilitiesService();

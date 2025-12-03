/**
 * Icon Registry - Complete Implementation
 * 
 * Handles loading architecture icons from various providers.
 * Icons are fetched on-demand, converted to data URIs, and cached.
 */

// Complete AWS service name to icon path mapping
// Based on weibeld/aws-icons-svg repository structure (q1-2022)
const AWS_ICON_PATHS: Record<string, string> = {
    // Compute
    'ec2': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_Amazon-EC2_64.svg',
    'lambda_function': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Lambda_64.svg',
    'lambda': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Lambda_64.svg', // Alias for lambda_function
    'elastic_beanstalk': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Elastic-Beanstalk_64.svg',
    'lightsail': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_Amazon-Lightsail_64.svg',
    'batch': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Batch_64.svg',
    'outposts': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Outposts-family_64.svg',
    'auto_scaling': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Auto-Scaling_64.svg',
    
    // Containers
    'ecs': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Containers/64/Arch_Amazon-Elastic-Container-Service_64.svg',
    'ecr': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Containers/64/Arch_Amazon-Elastic-Container-Registry_64.svg',
    'eks': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Containers/64/Arch_Amazon-Elastic-Kubernetes-Service_64.svg',
    'fargate': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Compute/64/Arch_AWS-Fargate_64.svg',
    
    // Storage
    's3': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_Amazon-Simple-Storage-Service_64.svg',
    's3_glacier': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_Amazon-S3-Glacier_64.svg',
    'efs': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_Amazon-Elastic-File-System_64.svg',
    'fsx': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_Amazon-FSx_64.svg',
    'ebs': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_Amazon-Elastic-Block-Store_64.svg',
    'storage_gateway': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_AWS-Storage-Gateway_64.svg',
    'backup': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Storage/64/Arch_AWS-Backup_64.svg',
    
    // Database
    'dynamodb': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-DynamoDB_64.svg',
    'rds': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-RDS_64.svg',
    'aurora': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Aurora_64.svg',
    'elasticache': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-ElastiCache_64.svg',
    'neptune': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Neptune_64.svg',
    'documentdb': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-DocumentDB_64.svg',
    'redshift': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Redshift_64.svg',
    'keyspaces': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Keyspaces_64.svg',
    'timestream': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Timestream_64.svg',
    'qldb': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Database/64/Arch_Amazon-Quantum-Ledger-Database_64.svg',
    'dms': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Migration-Transfer/Arch_64/Arch_AWS-Database-Migration-Service_64.svg',
    
    // Networking & Content Delivery
    'vpc': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_Amazon-Virtual-Private-Cloud_64.svg',
    'api_gateway': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_Amazon-API-Gateway_64.svg',
    'cloudfront': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_Amazon-CloudFront_64.svg',
    'route_53': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_Amazon-Route-53_64.svg',
    'elastic_load_balancing': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_Elastic-Load-Balancing_64.svg',
    'transit_gateway': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Transit-Gateway_64.svg',
    'vpn_gateway': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Site-to-Site-VPN_64.svg',
    'direct_connect': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Direct-Connect_64.svg',
    'global_accelerator': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Global-Accelerator_64.svg',
    'privatelink': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-PrivateLink_64.svg',
    'cloud_map': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Cloud-Map_64.svg',
    'app_mesh': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-App-Mesh_64.svg',
    'nat_gateway': 'q1-2022/Resource-Icons_01312022/Res_Networking-and-Content-Delivery/Res_48_Light/Res_Amazon-VPC_NAT-Gateway_48_Light.svg',
    'internet_gateway': 'q1-2022/Resource-Icons_01312022/Res_Networking-and-Content-Delivery/Res_48_Light/Res_Amazon-VPC_Internet-Gateway_48_Light.svg',
    'client_vpn': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Networking-Content-Delivery/64/Arch_AWS-Client-VPN_64.svg',
    
    // App Integration
    'sqs': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_Amazon-Simple-Queue-Service_64.svg',
    'sns': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_Amazon-Simple-Notification-Service_64.svg',
    'eventbridge': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_Amazon-EventBridge_64.svg',
    'step_functions': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_AWS-Step-Functions_64.svg',
    'mq': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_Amazon-MQ_64.svg',
    'appsync': 'q1-2022/Architecture-Service-Icons_01312022/Arch_App-Integration/Arch_64/Arch_AWS-AppSync_64.svg',
    
    // Security, Identity & Compliance
    'iam': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Identity-and-Access-Management_64.svg',
    'cognito': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_Amazon-Cognito_64.svg',
    'kms': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Key-Management-Service_64.svg',
    'secrets_manager': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Secrets-Manager_64.svg',
    'waf': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-WAF_64.svg',
    'shield': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Shield_64.svg',
    'guardduty': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_Amazon-GuardDuty_64.svg',
    'inspector': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_Amazon-Inspector_64.svg',
    'macie': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_Amazon-Macie_64.svg',
    'certificate_manager': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Certificate-Manager_64.svg',
    'directory_service': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Directory-Service_64.svg',
    'security_hub': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Security-Identity-Compliance/64/Arch_AWS-Security-Hub_64.svg',
    
    // Management & Governance
    'cloudwatch': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_Amazon-CloudWatch_64.svg',
    'cloudformation': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-CloudFormation_64.svg',
    'cloudtrail': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-CloudTrail_64.svg',
    'config': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Config_64.svg',
    'systems_manager': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Systems-Manager_64.svg',
    'opsworks': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-OpsWorks_64.svg',
    'organizations': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Organizations_64.svg',
    'service_catalog': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Service-Catalog_64.svg',
    'trusted_advisor': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Trusted-Advisor_64.svg',
    'control_tower': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Management-Governance/64/Arch_AWS-Control-Tower_64.svg',
    
    // Analytics
    'kinesis': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-Kinesis_64.svg',
    'athena': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-Athena_64.svg',
    'glue': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_AWS-Glue_64.svg',
    'emr': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-EMR_64.svg',
    'quicksight': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-QuickSight_64.svg',
    'data_pipeline': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_AWS-Data-Pipeline_64.svg',
    'lake_formation': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_AWS-Lake-Formation_64.svg',
    'msk': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-Managed-Streaming-for-Apache-Kafka_64.svg',
    'elasticsearch_service': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Analytics/Arch_64/Arch_Amazon-OpenSearch-Service_64.svg',
    
    // Developer Tools
    'codecommit': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-CodeCommit_64.svg',
    'codebuild': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-CodeBuild_64.svg',
    'codedeploy': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-CodeDeploy_64.svg',
    'codepipeline': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-CodePipeline_64.svg',
    'codeartifact': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-CodeArtifact_64.svg',
    'x_ray': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-X-Ray_64.svg',
    'cloud9': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Developer-Tools/64/Arch_AWS-Cloud9_64.svg',
    
    // Machine Learning
    'sagemaker': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-SageMaker_64.svg',
    'comprehend': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Comprehend_64.svg',
    'rekognition': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Rekognition_64.svg',
    'polly': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Polly_64.svg',
    'transcribe': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Transcribe_64.svg',
    'translate': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Translate_64.svg',
    'lex': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Lex_64.svg',
    'textract': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Textract_64.svg',
    'forecast': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Forecast_64.svg',
    'personalize': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Personalize_64.svg',
    'bedrock': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Machine-Learning/64/Arch_Amazon-Bedrock_64.svg',
    
    // IoT
    'iot_core': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Internet-of-Things/64/Arch_AWS-IoT-Core_64.svg',
    'greengrass': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Internet-of-Things/64/Arch_AWS-IoT-Greengrass_64.svg',
    
    // Application Integration  
    'ses': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Customer-Enablement/64/Arch_Amazon-Simple-Email-Service_64.svg',
    'pinpoint': 'q1-2022/Architecture-Service-Icons_01312022/Arch_Customer-Enablement/64/Arch_Amazon-Pinpoint_64.svg',
};

interface CachedIcon {
    dataUri: string;
    timestamp: number;
}

class IconRegistry {
    private cache: Map<string, CachedIcon> = new Map();
    private loading: Map<string, Promise<string | null>> = new Map();
    private readonly CACHE_KEY_PREFIX = 'ziya_aws_icon_';
    private readonly CACHE_EXPIRY_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
    private readonly BASE_URL = 'https://raw.githubusercontent.com/weibeld/aws-icons-svg/main';
    
    constructor() {
        this.loadCacheFromStorage();
    }
    
    /**
     * Get icon as data URI
     */
    async getIconAsDataUri(provider: string, iconId: string): Promise<string | null> {
        if (provider !== 'aws') {
            console.warn(`Only AWS provider supported currently, got: ${provider}`);
            return null;
        }
        
        const cacheKey = `${provider}:${iconId}`;
        
        // Check memory cache
        const cached = this.cache.get(cacheKey);
        if (cached) {
            return cached.dataUri;
        }
        
        // Check if already loading
        if (this.loading.has(cacheKey)) {
            return this.loading.get(cacheKey)!;
        }
        
        // Start fetch
        const loadPromise = this.fetchIcon(iconId);
        this.loading.set(cacheKey, loadPromise);
        
        try {
            const dataUri = await loadPromise;
            if (dataUri) {
                const entry: CachedIcon = {
                    dataUri,
                    timestamp: Date.now(),
                };
                this.cache.set(cacheKey, entry);
                this.saveCacheToStorage(cacheKey, entry);
            }
            return dataUri;
        } finally {
            this.loading.delete(cacheKey);
        }
    }
    
    /**
     * Get icon as blob URL (for Mermaid/Graphviz)
     */
    async getIconAsBlobUrl(provider: string, iconId: string): Promise<string | null> {
        const dataUri = await this.getIconAsDataUri(provider, iconId);
        if (!dataUri) return null;
        
        // Convert data URI to blob URL
        try {
            const base64Data = dataUri.split(',')[1];
            const svgData = atob(base64Data);
            const blob = new Blob([svgData], { type: 'image/svg+xml' });
            return URL.createObjectURL(blob);
        } catch (error) {
            console.error('Failed to create blob URL:', error);
            return null;
        }
    }
    
    /**
     * Fetch icon SVG and convert to data URI
     */
    private async fetchIcon(iconId: string): Promise<string | null> {
        const iconPath = AWS_ICON_PATHS[iconId];
        if (!iconPath) {
            console.warn(`No icon mapping for: ${iconId}`);
            return null;
        }
        
        const encodedPath = iconPath; // Path is already correctly formatted
        const url = `${this.BASE_URL}/${encodedPath}`;
        
        try {
            console.log(`📦 Fetching AWS icon: ${iconId} from ${url}`);
            const response = await fetch(url);
            
            if (!response.ok) {
                console.error(`Failed to fetch icon ${iconId}: ${response.status}`);
                return null;
            }
            
            let svgData = await response.text();
            
            // Ensure SVG has viewBox
            if (!svgData.includes('viewBox')) {
                svgData = svgData.replace('<svg', '<svg viewBox="0 0 64 64"');
            }
            
            // Convert to data URI
            const dataUri = `data:image/svg+xml;base64,${btoa(svgData)}`;
            
            console.log(`✅ Loaded icon: ${iconId} (${Math.round(svgData.length / 1024)}KB)`);
            return dataUri;
            
        } catch (error) {
            console.error(`Error fetching icon ${iconId}:`, error);
            return null;
        }
    }
    
    /**
     * Load cache from localStorage
     */
    private loadCacheFromStorage(): void {
        try {
            const keys = Object.keys(localStorage);
            let loaded = 0;
            
            for (const key of keys) {
                if (key.startsWith(this.CACHE_KEY_PREFIX)) {
                    const data = localStorage.getItem(key);
                    if (data) {
                        const cached: CachedIcon = JSON.parse(data);
                        
                        // Check expiry
                        const age = Date.now() - cached.timestamp;
                        if (age < this.CACHE_EXPIRY_MS) {
                            const cacheKey = key.substring(this.CACHE_KEY_PREFIX.length);
                            this.cache.set(cacheKey, cached);
                            loaded++;
                        } else {
                            localStorage.removeItem(key);
                        }
                    }
                }
            }
            
            if (loaded > 0) {
                console.log(`📦 Loaded ${loaded} cached AWS icons`);
            }
        } catch (error) {
            console.warn('Failed to load icon cache:', error);
        }
    }
    
    /**
     * Save to localStorage
     */
    private saveCacheToStorage(cacheKey: string, entry: CachedIcon): void {
        try {
            localStorage.setItem(
                this.CACHE_KEY_PREFIX + cacheKey,
                JSON.stringify(entry)
            );
        } catch (error) {
            console.warn('Failed to save icon to cache:', error);
        }
    }
}

// Global instance
export const iconRegistry = new IconRegistry();

/**
 * Ensure icons are loaded for shape IDs
 */
export async function ensureIconsLoaded(shapeIds: string[]): Promise<void> {
    const promises: Promise<string | null>[] = [];
    
    for (const shapeId of shapeIds) {
        if (shapeId.startsWith('aws_')) {
            const iconId = shapeId.substring(4);
            promises.push(iconRegistry.getIconAsDataUri('aws', iconId));
        }
    }
    
    if (promises.length > 0) {
        await Promise.all(promises);
    }
}

"""
Amazon Internal MCP Registry Provider.

Authentication:
- For internal Amazon: Uses ADA/Midway credentials (multiple strategies)
- Secrets Manager stores Cognito client credentials for MCP Gateway
- Cognito provides OAuth2 client_credentials flow for M2M auth
- Tries multiple credential sources automatically
- Tries multiple regions to find the secret
"""

import json
import os
import subprocess
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import httpx

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.mcp.registry.installation_helper import InstallationHelper
from app.utils.logging_utils import logger


class AmazonInternalRegistryProvider(RegistryProvider):
    """Provider for Amazon's internal MCP registry using the Smithy API."""
    
    def __init__(
        self, 
        registry_name: str = "MainRegistry", 
        region: str = "us-west-2",
        profile_name: Optional[str] = None,
        secret_name: Optional[str] = None
    ):
        self.registry_name = registry_name
        self.region = region
        self.profile_name = profile_name
        self.endpoint_url = os.environ.get(
            'MCP_REGISTRY_ENDPOINT',
            'https://bedrock-agentcore-gateway.integ.us-east-1.app.beta.apollo.aws.dev/mcp'
        )
        # Secret containing Cognito credentials
        self.secret_name = secret_name or os.environ.get(
            'MCP_REGISTRY_SECRET',
            'mcp-registry-client-credentials'
        )
        # Try multiple regions for secret lookup
        self.secret_regions = [self.region, 'us-east-1', 'us-west-2']
        self._boto_session: Optional[boto3.Session] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._executor = ThreadPoolExecutor(max_workers=4)
    
    @property
    def name(self) -> str:
        return "Amazon Internal Registry"
    
    @property
    def identifier(self) -> str:
        return "amazon-internal"
    
    @property
    def is_internal(self) -> bool:
        return True
    
    @property
    def supports_search(self) -> bool:
        return True
    
    def _get_boto_session(self) -> boto3.Session:
        """Get or create boto3 session."""
        if not self._boto_session:
            import os
            import subprocess
            
            logger.info("Attempting to acquire AWS credentials for Amazon Internal Registry...")
            
            # Strategy 1: Try to use ADA credentials directly
            if not self.profile_name:
                logger.info("Strategy 1: Trying ADA credential process...")
                
                try:
                    # Try to call ada credentials to get temporary creds
                    result = subprocess.run(
                        ['ada', 'credentials', 'update', '--account=339712844704', '--provider=isengard', '--role=IibsAdminAccess-DO-NOT-DELETE', '--once'],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result.returncode == 0:
                        logger.info("✓ ADA credentials updated successfully")
                        # ADA writes to ~/.aws/credentials, create session now
                        self._boto_session = boto3.Session()
                        
                        # Verify it works
                        try:
                            sts = self._boto_session.client('sts')
                            identity = sts.get_caller_identity()
                            logger.info(f"✓ Using identity: {identity.get('Arn')}")
                            return self._boto_session
                        except Exception as e:
                            logger.warning(f"ADA credentials didn't work: {e}")
                    else:
                        logger.warning(f"ADA credentials update failed: {result.stderr}")
                        
                except FileNotFoundError:
                    logger.info("ADA not found in PATH")
                except Exception as e:
                    logger.warning(f"Could not use ADA: {e}")
            
            # Strategy 2: Try specified profile
            if self.profile_name:
                logger.info(f"Strategy 2: Using specified profile: {self.profile_name}")
                try:
                    self._boto_session = boto3.Session(profile_name=self.profile_name)
                    
                    # Verify it works
                    sts = self._boto_session.client('sts')
                    identity = sts.get_caller_identity()
                    logger.info(f"✓ Using identity: {identity.get('Arn')}")
                    return self._boto_session
                except Exception as e:
                    logger.error(f"Specified profile failed: {e}")
                    raise
            
            # Strategy 3: Try environment variables (in case user set them manually)
            if not self._boto_session:
                logger.info("Strategy 3: Trying environment variables...")
                self._boto_session = boto3.Session()
                
                # Try to verify
                try:
                    sts = self._boto_session.client('sts')
                    identity = sts.get_caller_identity()
                    logger.info(f"✓ Using identity: {identity.get('Arn')}")
                    return self._boto_session
                except Exception as e:
                    logger.error(f"Environment credentials failed: {e}")
                    logger.error("All credential strategies failed!")
                    logger.error("Please ensure you have valid AWS credentials configured.")
                    raise RuntimeError(
                        "No valid AWS credentials found. "
                        "Try: ada credentials update --account=YOUR_ACCOUNT --provider=isengard --role=YOUR_ROLE"
                    )
        
        return self._boto_session
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={'User-Agent': 'Ziya-Amazon-MCP-Client/1.0'}
            )
        return self._http_client
    
    async def _run_in_executor(self, func, *args, **kwargs):
        """Run blocking boto3 calls in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))
    
    async def _get_cognito_credentials(self) -> Dict[str, str]:
        """Fetch Cognito client credentials from Secrets Manager."""
        last_error = None
        
        # Try multiple regions
        for region in self.secret_regions:
            try:
                logger.info(f"Trying to fetch secret from {region}...")
                
                session = self._get_boto_session()
                
                # Configure client with retries
                config = Config(
                    retries={'max_attempts': 3, 'mode': 'adaptive'},
                    connect_timeout=10,
                    read_timeout=30
                )
                
                secrets_client = session.client(
                    'secretsmanager',
                    region_name=region,
                    config=config
                )
                
                # Fetch secret
                response = await self._run_in_executor(
                    secrets_client.get_secret_value,
                    SecretId=self.secret_name
                )
                
                secret_data = json.loads(response['SecretString'])
                
                # Expected format: {client_id, client_secret, discovery_url}
                required_keys = ['client_id', 'client_secret', 'discovery_url']
                if not all(key in secret_data for key in required_keys):
                    raise ValueError(f"Secret missing required keys: {required_keys}")
                
                logger.info(f"✓ Successfully fetched secret from {region}")
                return secret_data
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                
                if error_code == 'ResourceNotFoundException':
                    logger.debug(f"Secret not found in {region}")
                    last_error = e
                    continue  # Try next region
                elif error_code == 'AccessDeniedException':
                    logger.warning(f"Access denied to secret in {region}")
                    last_error = e
                    continue  # Try next region
                else:
                    # Other errors are fatal
                    last_error = e
                    break
            except Exception as e:
                logger.debug(f"Error fetching from {region}: {e}")
                last_error = e
                continue
        
        # If we get here, all regions failed
        if last_error:
            if isinstance(last_error, ClientError):
                error_code = last_error.response.get('Error', {}).get('Code', 'Unknown')
                
                if error_code == 'ResourceNotFoundException':
                    logger.error(f"Secret '{self.secret_name}' not found in any region: {self.secret_regions}")
                    logger.error("Possible solutions:")
                    logger.error("  1. Verify secret name is correct")
                    logger.error("  2. Check if secret exists in a different region")
                    logger.error("  3. Request MCP Registry access if you don't have it yet")
                elif error_code == 'AccessDeniedException':
                    logger.error(f"Access denied to secret in all regions")
                    logger.error("Your IAM role needs these permissions:")
                    logger.error("  - secretsmanager:GetSecretValue")
                    logger.error("  - kms:Decrypt (for the secret's KMS key)")
            
            raise last_error
    
    async def _discover_token_endpoint(self, discovery_url: str) -> Optional[str]:
        """Discover Cognito token endpoint from discovery URL."""
        try:
            client = self._get_http_client()
            response = await client.get(discovery_url)
            response.raise_for_status()
            
            discovery_data = response.json()
            token_endpoint = discovery_data.get('token_endpoint')
            
            if not token_endpoint:
                logger.error("Token endpoint not found in discovery response")
                return None
            
            logger.info(f"Discovered token endpoint: {token_endpoint}")
            return token_endpoint
            
        except Exception as e:
            logger.error(f"Failed to discover token endpoint: {e}")
            return None
    
    async def _get_access_token(self) -> str:
        """Get Cognito access token using client credentials grant."""
        # Check if we have a valid cached token
        if self._access_token and self._token_expiry:
            if datetime.now() < self._token_expiry:
                logger.debug("Using cached access token")
                return self._access_token
        
        # Fetch new token
        logger.info("Fetching new Cognito access token...")
        
        try:
            # Get credentials from Secrets Manager
            creds = await self._get_cognito_credentials()
            
            # Discover token endpoint
            token_endpoint = await self._discover_token_endpoint(creds['discovery_url'])
            if not token_endpoint:
                raise ValueError("Could not discover Cognito token endpoint")
            
            # Encode client credentials for Basic auth (OAuth2 best practice)
            credentials_str = f"{creds['client_id']}:{creds['client_secret']}"
            encoded_credentials = base64.b64encode(credentials_str.encode()).decode()
            
            # Request token
            client = self._get_http_client()
            response = await client.post(
                token_endpoint,
                headers={
                    'Authorization': f'Basic {encoded_credentials}',
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                data={'grant_type': 'client_credentials'}
            )
            response.raise_for_status()
            
            token_data = response.json()
            access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 3600)
            
            # Cache token with 5 minute buffer
            self._access_token = access_token
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 300)
            
            logger.info(f"Cognito authentication successful! Token expires in {expires_in}s")
            return access_token
            
        except Exception as e:
            logger.error(f"Failed to get Cognito access token: {e}")
            raise
    
    async def _make_mcp_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make authenticated request to MCP Gateway."""
        try:
            # Get access token
            token = await self._get_access_token()
            
            # Make request with Bearer token
            client = self._get_http_client()
            url = f"{self.endpoint_url.rstrip('/')}/{path.lstrip('/')}"
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
            
            if method.upper() == 'GET':
                response = await client.get(url, headers=headers, **kwargs)
            elif method.upper() == 'POST':
                response = await client.post(url, headers=headers, **kwargs)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            logger.error(f"MCP request failed: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"MCP request error: {e}")
            raise
    
    def _parse_amazon_service(self, service_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse Amazon MCP service into standard format."""
        # Map Amazon-specific fields to standard format
        instructions = service_data.get('instructions', {})
        tags = self._extract_amazon_tags(service_data)
        install_type = InstallationHelper.detect_installation_type(instructions)
        
        # Parse timestamps
        created_at = service_data.get('createdAt')
        updated_at = service_data.get('lastUpdatedAt')
        
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        elif not isinstance(created_at, datetime):
            created_at = datetime.now()
        
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        elif not isinstance(updated_at, datetime):
            updated_at = created_at
        
        # Map support levels
        support_level_map = {
            'Recommended': SupportLevel.RECOMMENDED,
            'Supported': SupportLevel.SUPPORTED,
            'Under assessment': SupportLevel.UNDER_ASSESSMENT,
            'In development': SupportLevel.IN_DEVELOPMENT,
            'Community': SupportLevel.COMMUNITY
        }
        
        support_level = support_level_map.get(
            service_data.get('supportLevel', 'Community'),
            SupportLevel.UNDER_ASSESSMENT
        )
        
        # Map status
        status_map = {
            'ACTIVE': ServiceStatus.ACTIVE,
            'PENDING': ServiceStatus.PENDING,
            'DELETED': ServiceStatus.DELETED,
            'DEPRECATED': ServiceStatus.DEPRECATED
        }
        
        status = status_map.get(
            service_data.get('status', 'ACTIVE'),
            ServiceStatus.ACTIVE
        )
        
        cti = service_data.get('cti', 'Unknown/Unknown/Unknown')
        bindle_id = service_data.get('bindleId')
        
        return RegistryServiceInfo(
            service_id=service_data['serviceId'],
            service_name=service_data['serviceName'],
            service_description=service_data['serviceDescription'],
            version=service_data.get('version', 1),
            status=status,
            support_level=support_level,
            created_at=created_at,
            last_updated_at=updated_at,
            installation_instructions=instructions,
            installation_type=install_type,
            tags=tags,
            security_review_url=service_data.get('securityReviewLink'),
            provider_metadata={
                'provider_id': 'amazon-internal',
                'bindleId': bindle_id,
                'cti': cti,
                'registry_name': self.registry_name
            }
        )
    
    def _extract_amazon_tags(self, service_data: Dict[str, Any]) -> List[str]:
        """Extract tags from Amazon service metadata."""
        tags = []
        
        # Extract from CTI
        cti = service_data.get('cti', '')
        if cti:
            # CTI format: "Category/Type/Item"
            parts = cti.split('/')
            tags.extend([p.lower().replace(' ', '-') for p in parts if p])
        
        # Extract from description
        description = service_data.get('serviceDescription', '').lower()
        if 'database' in description or 'sql' in description:
            tags.append('database')
        if 'file' in description or 'storage' in description:
            tags.append('files')
        if 'api' in description or 'rest' in description:
            tags.append('api')
        
        return list(set(tags))  # Deduplicate
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from Amazon MCP Registry Service."""
        try:
            # Build query parameters
            params = {'maxResults': max_results}
            if next_token:
                params['nextToken'] = next_token
            
            # Make request to MCP Gateway
            # Note: Exact endpoint path may need adjustment based on actual API
            response = await self._make_mcp_request(
                'GET',
                f'/registries/{self.registry_name}/services',
                params=params
            )
            
            # Parse services
            services = []
            for service_data in response.get('services', []):
                try:
                    service = self._parse_amazon_service(service_data)
                    services.append(service)
                except Exception as e:
                    logger.warning(f"Failed to parse Amazon service: {e}")
                    continue
            
            return {
                'services': services,
                'next_token': response.get('nextToken')
            }
            
        except ClientError as e:
            logger.error(f"AWS error listing services: {e}")
            raise
        except Exception as e:
            logger.error(f"Error listing Amazon MCP services: {e}")
            raise
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        try:
            # Make request to service detail endpoint
            response = await self._make_mcp_request(
                'GET',
                f'/registries/{self.registry_name}/services/{service_id}'
            )
            
            service = self._parse_amazon_service(response)
            
            # Enhance with bundle information if available
            if 'bundle' in response:
                service.provider_metadata['bundle'] = response['bundle']
            
            return service
            
        except Exception as e:
            logger.error(f"Error getting Amazon service detail: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search tools using Amazon's registry search API."""
        try:
            # Make search request
            response = await self._make_mcp_request(
                'POST',
                f'/registries/{self.registry_name}/tools/search',
                json={
                    'query': query,
                    'maxResults': max_results
                }
            )
            
            # Group tools by service
            server_tools: Dict[str, List[RegistryTool]] = {}
            for tool_data in response.get('tools', []):
                server_id = tool_data['mcpServerId']
                tool_name = tool_data['toolName']
                
                if server_id not in server_tools:
                    server_tools[server_id] = []
                
                server_tools[server_id].append(RegistryTool(
                    tool_name=tool_name,
                    service_id=server_id,
                    description=tool_data.get('description')
                ))
            
            # Get service info for matching tools
            results = []
            for server_id, tools in server_tools.items():
                try:
                    service = await self.get_service_detail(server_id)
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=100.0  # Amazon API doesn't provide scores
                    ))
                except Exception as e:
                    logger.warning(f"Failed to get details for {server_id}: {e}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching Amazon tools: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service using Amazon's registry instructions."""
        try:
            service = await self.get_service_detail(service_id)
            instructions = service.installation_instructions
            install_type = service.installation_type
            
            # Check prerequisites
            has_prereq, error_msg = InstallationHelper.check_prerequisites(install_type)
            if not has_prereq:
                raise RuntimeError(error_msg)
            
            # Create installation directory
            install_dir = Path.home() / ".ziya" / "mcp_services" / service_id.replace('/', '_')
            install_dir.mkdir(parents=True, exist_ok=True)
            
            command_array = []
            env_vars = {}
            
            # Handle different installation types
            if install_type == InstallationType.NPM:
                package = instructions.get('package')
                if not package:
                    raise ValueError("NPM package not specified")
                
                install_result = InstallationHelper.install_npm_package(package, install_dir)
                if not install_result['success']:
                    raise RuntimeError(install_result['error'])
                command_array = install_result['command']
                
            elif install_type == InstallationType.PYPI:
                package = instructions.get('package')
                if not package:
                    raise ValueError("PyPI package not specified")
                
                install_result = InstallationHelper.install_pypi_package(package)
                if not install_result['success']:
                    raise RuntimeError(install_result['error'])
                command_array = install_result['command']
                
            elif install_type == InstallationType.DOCKER:
                image = instructions.get('image')
                if not image:
                    raise ValueError("Docker image not specified")
                
                setup_result = InstallationHelper.setup_docker_container(image)
                if not setup_result['success']:
                    raise RuntimeError(setup_result['error'])
                command_array = setup_result['command']
                
            elif install_type == InstallationType.REMOTE:
                # Remote server - no installation needed
                command_array = None
                
            else:
                # Try to use command from instructions
                command_array = instructions.get('command', [])
                if not command_array:
                    raise ValueError(f"Unsupported installation type: {install_type}")
            
            # Extract environment variables
            if 'env' in instructions:
                env_vars = instructions['env']
            
            # Build configuration entries
            server_name = service.service_name.lower().replace(' ', '_').replace('-', '_')
            config_entries = {
                "enabled": True,
                "description": service.service_description,
                "registry_provider": self.identifier,
                "service_id": service_id,
                "version": service.version,
                "support_level": service.support_level.value,
                "installed_at": datetime.now().isoformat(),
                "installation_path": str(install_dir),
                "cti": service.provider_metadata.get('cti'),
                "bindle_id": service.provider_metadata.get('bindleId'),
                "security_review_url": service.security_review_url
            }
            
            # Add command or remote configuration
            if command_array:
                config_entries['command'] = command_array
                if env_vars:
                    config_entries['env'] = env_vars
            elif install_type == InstallationType.REMOTE:
                config_entries['remote_url'] = instructions.get('url')
                config_entries['transport'] = instructions.get('transport', 'streamable-http')
            else:
                raise ValueError(f"No valid installation method found for {service_id}")
            
            logger.info(f"Successfully prepared installation for {service_id}")
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir) if command_array else None,
                config_entries=config_entries
            )
            
        except Exception as e:
            logger.error(f"Error installing Amazon service {service_id}: {e}")
            return InstallationResult(
                success=False,
                service_id=service_id,
                server_name="",
                error_message=str(e)
            )
    
    async def validate_service(self, service_id: str) -> bool:
        """Validate service availability."""
        try:
            await self.get_service_detail(service_id)
            return True
        except Exception:
            return False
    
    async def test_connection(self) -> bool:
        """Test the connection to the MCP Registry Service."""
        try:
            logger.info("Testing connection to Amazon MCP Registry Service...")
            
            # Try to get an access token
            token = await self._get_access_token()
            
            if not token:
                logger.error("Failed to obtain access token")
                return False
            
            # Try to list services
            result = await self.list_services(max_results=1)
            
            logger.info(f"Connection test successful! Found {len(result['services'])} services")
            return True
            
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            logger.error(f"Error details: {type(e).__name__}: {str(e)}")
            return False
    
    async def check_permissions(self) -> Dict[str, Any]:
        """
        Check if the current AWS identity has required permissions.
        Returns detailed permission status.
        """
        results = {
            'has_aws_credentials': False,
            'can_access_secrets_manager': False,
            'can_decrypt_secret': False,
            'can_authenticate_cognito': False,
            'can_call_mcp_api': False,
            'identity': None,
            'errors': []
        }
        
        try:
            # Check AWS credentials
            session = self._get_boto_session()
            sts_client = session.client('sts')
            identity = await self._run_in_executor(sts_client.get_caller_identity)
            
            results['has_aws_credentials'] = True
            results['identity'] = {
                'user_id': identity.get('UserId'),
                'arn': identity.get('Arn'),
                'account': identity.get('Account')
            }
            
            logger.info(f"✓ AWS credentials valid: {identity.get('Arn')}")
            
        except Exception as e:
            results['errors'].append(f"AWS credentials: {e}")
            logger.error(f"✗ AWS credentials invalid: {e}")
            return results
        
        try:
            # Check Secrets Manager access
            creds = await self._get_cognito_credentials()
            results['can_access_secrets_manager'] = True
            logger.info("✓ Can access Secrets Manager")
            
            # Check Cognito authentication
            token = await self._get_access_token()
            results['can_authenticate_cognito'] = True
            logger.info("✓ Cognito authentication successful")
            
            # Check MCP API access
            result = await self.list_services(max_results=1)
            results['can_call_mcp_api'] = True
            logger.info("✓ Can call MCP Registry API")
            
        except Exception as e:
            results['errors'].append(f"Permission check: {e}")
            logger.error(f"✗ Permission check failed: {e}")
        
        return results
    
    async def close(self):
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        
        if self._executor:
            self._executor.shutdown(wait=False)

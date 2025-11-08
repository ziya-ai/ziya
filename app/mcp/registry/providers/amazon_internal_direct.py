"""
Amazon Internal MCP Registry Provider - Direct API Access.

Updated for direct programmatic access without Cognito authentication.
The bedrock-chatter role has been granted direct access to MCP Registry APIs.
"""

import json
import os
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


class AmazonInternalDirectProvider(RegistryProvider):
    """Provider for Amazon's internal MCP registry using direct API access."""
    
    def __init__(
        self, 
        registry_name: str = "MainRegistry", 
        region: str = "us-west-2",
        profile_name: Optional[str] = None
    ):
        self.registry_name = registry_name
        self.region = region
        self.profile_name = profile_name
        
        # Direct API endpoint - PDX only (us-west-2)
        self.endpoint_url = os.environ.get(
            'MCP_REGISTRY_ENDPOINT',
            'https://bedrock-agentcore-gateway.integ.us-west-2.app.beta.apollo.aws.dev/mcp'
        )
        
        self._boto_session: Optional[boto3.Session] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._executor = ThreadPoolExecutor(max_workers=4)
    
    @property
    def name(self) -> str:
        return "Amazon Internal Registry (Direct)"
    
    @property
    def identifier(self) -> str:
        return "amazon-internal-direct"
    
    @property
    def is_internal(self) -> bool:
        return True
    
    @property
    def supports_search(self) -> bool:
        return True
    
    def _get_boto_session(self) -> boto3.Session:
        """Get or create boto3 session."""
        if not self._boto_session:
            if self.profile_name:
                logger.info(f"Using AWS profile: {self.profile_name}")
                self._boto_session = boto3.Session(profile_name=self.profile_name)
            else:
                logger.info("Using default AWS credentials")
                self._boto_session = boto3.Session()
            
            # Verify credentials work
            try:
                sts = self._boto_session.client('sts')
                identity = sts.get_caller_identity()
                logger.info(f"✓ Using identity: {identity.get('Arn')}")
            except Exception as e:
                logger.error(f"AWS credentials failed: {e}")
                raise
        
        return self._boto_session
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={'User-Agent': 'Ziya-Amazon-MCP-Direct/1.0'}
            )
        return self._http_client
    
    async def _run_in_executor(self, func, *args, **kwargs):
        """Run blocking boto3 calls in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))
    
    async def _get_aws_auth_headers(self) -> Dict[str, str]:
        """Get AWS authentication headers using SigV4."""
        try:
            session = self._get_boto_session()
            credentials = session.get_credentials()
            
            if not credentials:
                raise ValueError("No AWS credentials available")
            
            # For now, use a simple approach - in production you'd use proper SigV4
            # The role has been granted direct access, so we might not need complex auth
            headers = {
                'X-Amz-Security-Token': credentials.token,
                'Authorization': f'AWS4-HMAC-SHA256 Credential={credentials.access_key}',
                'X-Amz-Date': datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            }
            
            return headers
            
        except Exception as e:
            logger.error(f"Failed to get AWS auth headers: {e}")
            raise
    
    async def _make_mcp_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make authenticated request to MCP Registry API."""
        try:
            client = self._get_http_client()
            url = f"{self.endpoint_url.rstrip('/')}/{path.lstrip('/')}"
            
            # Try different authentication approaches
            auth_attempts = [
                ("Direct with AWS headers", self._get_direct_headers),
                ("Simple with role", self._get_simple_headers),
                ("No auth", self._get_no_auth_headers)
            ]
            
            last_error = None
            
            for auth_name, header_func in auth_attempts:
                try:
                    logger.info(f"Trying {auth_name} for {url}")
                    headers = await header_func()
                    
                    if method.upper() == 'GET':
                        response = await client.get(url, headers=headers, **kwargs)
                    elif method.upper() == 'POST':
                        response = await client.post(url, headers=headers, **kwargs)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")
                    
                    if response.status_code == 200:
                        logger.info(f"✓ Success with {auth_name}")
                        return response.json()
                    elif response.status_code in [401, 403]:
                        logger.warning(f"Auth failed with {auth_name}: {response.status_code}")
                        last_error = f"{auth_name}: {response.status_code} - {response.text[:100]}"
                        continue
                    else:
                        logger.warning(f"Unexpected status with {auth_name}: {response.status_code}")
                        last_error = f"{auth_name}: {response.status_code} - {response.text[:100]}"
                        continue
                        
                except Exception as e:
                    logger.warning(f"{auth_name} failed: {e}")
                    last_error = f"{auth_name}: {e}"
                    continue
            
            # If we get here, all auth methods failed
            raise RuntimeError(f"All authentication methods failed. Last error: {last_error}")
            
        except Exception as e:
            logger.error(f"MCP request error: {e}")
            raise
    
    async def _get_direct_headers(self) -> Dict[str, str]:
        """Get headers for direct AWS authentication."""
        session = self._get_boto_session()
        credentials = session.get_credentials()
        
        headers = {
            'Content-Type': 'application/json',
            'X-Amz-Security-Token': credentials.token or '',
            'X-Amz-Date': datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        }
        
        return headers
    
    async def _get_simple_headers(self) -> Dict[str, str]:
        """Get simple headers with role information."""
        session = self._get_boto_session()
        sts = session.client('sts')
        identity = sts.get_caller_identity()
        
        headers = {
            'Content-Type': 'application/json',
            'X-AWS-Role-Arn': identity.get('Arn', ''),
            'X-AWS-Account': identity.get('Account', '')
        }
        
        return headers
    
    async def _get_no_auth_headers(self) -> Dict[str, str]:
        """Get basic headers without authentication."""
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
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
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except:
                created_at = datetime.now()
        elif not isinstance(created_at, datetime):
            created_at = datetime.now()
        
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            except:
                updated_at = created_at
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
        
        return RegistryServiceInfo(
            service_id=service_data.get('serviceId', service_data.get('id', 'unknown')),
            service_name=service_data.get('serviceName', service_data.get('name', 'Unknown Service')),
            service_description=service_data.get('serviceDescription', service_data.get('description', '')),
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
                'provider_id': 'amazon-internal-direct',
                'bindleId': service_data.get('bindleId'),
                'cti': service_data.get('cti', 'Unknown/Unknown/Unknown'),
                'registry_name': self.registry_name
            }
        )
    
    def _extract_amazon_tags(self, service_data: Dict[str, Any]) -> List[str]:
        """Extract tags from Amazon service metadata."""
        tags = []
        
        # Extract from CTI
        cti = service_data.get('cti', '')
        if cti:
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
        
        return list(set(tags))
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from Amazon MCP Registry."""
        try:
            # Build query parameters
            params = {'maxResults': max_results}
            if next_token:
                params['nextToken'] = next_token
            
            # Try different API paths
            api_paths = [
                f'/registries/{self.registry_name}/services',
                '/services',
                '/api/v1/services',
                '/v1/services'
            ]
            
            for path in api_paths:
                try:
                    logger.info(f"Trying API path: {path}")
                    response = await self._make_mcp_request('GET', path, params=params)
                    
                    # Parse services
                    services = []
                    service_list = response.get('services', response.get('items', []))
                    
                    for service_data in service_list:
                        try:
                            service = self._parse_amazon_service(service_data)
                            services.append(service)
                        except Exception as e:
                            logger.warning(f"Failed to parse service: {e}")
                            continue
                    
                    logger.info(f"✓ Successfully listed {len(services)} services")
                    return {
                        'services': services,
                        'next_token': response.get('nextToken')
                    }
                    
                except Exception as e:
                    logger.warning(f"API path {path} failed: {e}")
                    continue
            
            raise RuntimeError("All API paths failed")
            
        except Exception as e:
            logger.error(f"Error listing Amazon MCP services: {e}")
            raise
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        try:
            # Try different detail endpoints
            detail_paths = [
                f'/registries/{self.registry_name}/services/{service_id}',
                f'/services/{service_id}',
                f'/api/v1/services/{service_id}',
                f'/v1/services/{service_id}'
            ]
            
            for path in detail_paths:
                try:
                    response = await self._make_mcp_request('GET', path)
                    service = self._parse_amazon_service(response)
                    
                    # Enhance with bundle information if available
                    if 'bundle' in response:
                        service.provider_metadata['bundle'] = response['bundle']
                    
                    return service
                    
                except Exception as e:
                    logger.warning(f"Detail path {path} failed: {e}")
                    continue
            
            raise RuntimeError(f"Could not get details for service {service_id}")
            
        except Exception as e:
            logger.error(f"Error getting Amazon service detail: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search tools using Amazon's registry search API."""
        try:
            # Try different search endpoints
            search_paths = [
                f'/registries/{self.registry_name}/tools/search',
                '/tools/search',
                '/api/v1/search',
                '/v1/search'
            ]
            
            for path in search_paths:
                try:
                    response = await self._make_mcp_request(
                        'POST',
                        path,
                        json={
                            'query': query,
                            'maxResults': max_results
                        }
                    )
                    
                    # Group tools by service
                    server_tools: Dict[str, List[RegistryTool]] = {}
                    for tool_data in response.get('tools', response.get('results', [])):
                        server_id = tool_data.get('mcpServerId', tool_data.get('serviceId', 'unknown'))
                        tool_name = tool_data.get('toolName', tool_data.get('name', 'unknown'))
                        
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
                                relevance_score=100.0
                            ))
                        except Exception as e:
                            logger.warning(f"Failed to get details for {server_id}: {e}")
                    
                    return results
                    
                except Exception as e:
                    logger.warning(f"Search path {path} failed: {e}")
                    continue
            
            logger.warning("All search paths failed, returning empty results")
            return []
            
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
            
            # Handle installation based on type
            if install_type == InstallationType.REMOTE:
                config_entries['remote_url'] = instructions.get('url')
                config_entries['transport'] = instructions.get('transport', 'streamable-http')
            else:
                # For other types, use the command from instructions
                command_array = instructions.get('command', [])
                if command_array:
                    config_entries['command'] = command_array
                    if 'env' in instructions:
                        config_entries['env'] = instructions['env']
            
            logger.info(f"Successfully prepared installation for {service_id}")
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir),
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
            logger.info("Testing connection to Amazon MCP Registry Service (Direct Access)...")
            
            # Try to list services
            result = await self.list_services(max_results=1)
            
            logger.info(f"✓ Connection test successful! Found {len(result['services'])} services")
            return True
            
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    async def close(self):
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        
        if self._executor:
            self._executor.shutdown(wait=False)

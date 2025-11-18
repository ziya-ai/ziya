"""
Amazon Internal MCP Registry Provider - Final Working Implementation.

Uses proper SigV4 signing and no registry name (access to all public servers).
"""

import json
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
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
    """Final working provider for Amazon's internal MCP registry."""
    
    def __init__(
        self, 
        region: str = "us-west-2",
        profile_name: Optional[str] = None,
        use_beta: bool = False
    ):
        self.region = region
        self.profile_name = profile_name
        
        # Use correct endpoints from coral-config
        if use_beta:
            self.endpoint_url = "https://api.beta.registry.mcp.aws.dev/"
        else:
            self.endpoint_url = "https://api.registry.mcp.aws.dev/"
        
        self._boto_session: Optional[boto3.Session] = None
        self._http_client: Optional[httpx.AsyncClient] = None
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
            if self.profile_name:
                logger.info(f"Using AWS profile: {self.profile_name}")
                self._boto_session = boto3.Session(
                    profile_name=self.profile_name,
                    region_name=self.region
                )
            else:
                logger.info("Using default AWS credentials")
                self._boto_session = boto3.Session(region_name=self.region)
            
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
                headers={'User-Agent': 'Ziya-Amazon-MCP-Final/1.0'}
            )
        return self._http_client
    
    async def _run_in_executor(self, func, *args, **kwargs):
        """Run blocking boto3 calls in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))
    
    async def _make_mcp_request(self, operation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make authenticated request to MCP Registry API using proper SigV4."""
        try:
            session = self._get_boto_session()
            credentials = session.get_credentials()
            
            # Create AWS request for signing
            request = AWSRequest(
                method='POST',
                url=self.endpoint_url,
                data=json.dumps(payload),
                headers={
                    'Content-Type': 'application/x-amz-json-1.0',
                    'X-Amz-Target': f'MCPRegistryService.{operation}'
                }
            )
            
            # Sign the request with proper SigV4
            signer = SigV4Auth(credentials, 'mcp-registry-service', self.region)
            signer.add_auth(request)
            
            # Make the request
            client = self._get_http_client()
            response = await client.post(
                self.endpoint_url,
                headers=dict(request.headers),
                content=request.body
            )
            
            logger.info(f"MCP API call: {operation} -> {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                error_text = response.text[:200]
                logger.error(f"MCP API error: {response.status_code} - {error_text}")
                
                # Try to parse error response
                try:
                    error_data = response.json()
                    if '__type' in error_data:
                        error_type = error_data['__type']
                        message = error_data.get('Message', error_data.get('message', ''))
                        logger.error(f"Coral error: {error_type} - {message}")
                except:
                    pass
                
                raise RuntimeError(f"MCP API call failed: {response.status_code} - {error_text}")
            
        except Exception as e:
            logger.error(f"MCP request error for {operation}: {e}")
            raise
    
    def _parse_service_summary(self, service_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse ServiceSummary from the API into standard format."""
        # Map support levels
        support_level_map = {
            'Recommended': SupportLevel.RECOMMENDED,
            'Supported': SupportLevel.SUPPORTED,
            'Under assessment': SupportLevel.UNDER_ASSESSMENT,
            'In development': SupportLevel.IN_DEVELOPMENT
        }
        
        support_level = support_level_map.get(
            service_data.get('supportLevel', 'Under assessment'),
            SupportLevel.UNDER_ASSESSMENT
        )
        
        # Map status
        status_map = {
            'ACTIVE': ServiceStatus.ACTIVE,
            'PENDING': ServiceStatus.PENDING,
            'DELETED': ServiceStatus.DELETED
        }
        
        status = status_map.get(
            service_data.get('status', 'ACTIVE'),
            ServiceStatus.ACTIVE
        )
        
        # Parse timestamps
        created_at = service_data.get('createdAt')
        updated_at = service_data.get('lastUpdatedAt')
        
        if isinstance(created_at, (int, float)):
            created_at = datetime.fromtimestamp(created_at)
        elif isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except:
                created_at = datetime.now()
        else:
            created_at = datetime.now()
        
        if isinstance(updated_at, (int, float)):
            updated_at = datetime.fromtimestamp(updated_at)
        elif isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            except:
                updated_at = created_at
        else:
            updated_at = created_at
        
        # Parse instructions
        instructions = service_data.get('instructions', {})
        install_type = InstallationHelper.detect_installation_type(instructions)
        
        # Extract tags from CTI and other fields
        tags = []
        cti = service_data.get('cti', '')
        if cti:
            parts = cti.split('/')
            tags.extend([p.lower().replace(' ', '-') for p in parts if p])
        
        # Add tags based on service name and description
        service_name = service_data.get('serviceName', '').lower()
        service_desc = service_data.get('serviceDescription', '').lower()
        
        if any(word in service_name + service_desc for word in ['database', 'sql', 'db']):
            tags.append('database')
        if any(word in service_name + service_desc for word in ['file', 'storage', 'fs']):
            tags.append('files')
        if any(word in service_name + service_desc for word in ['api', 'rest', 'http']):
            tags.append('api')
        if any(word in service_name + service_desc for word in ['git', 'github', 'repo']):
            tags.append('git')
        
        return RegistryServiceInfo(
            service_id=service_data.get('serviceId', 'unknown'),
            service_name=service_data.get('serviceName', 'Unknown Service'),
            service_description=service_data.get('serviceDescription', ''),
            version=service_data.get('version', 1),
            status=status,
            support_level=support_level,
            created_at=created_at,
            last_updated_at=updated_at,
            installation_instructions=instructions,
            installation_type=install_type,
            tags=list(set(tags)),  # Remove duplicates
            security_review_url=service_data.get('securityReviewLink'),
            provider_metadata={
                'provider_id': self.identifier,
                'bindleId': service_data.get('bindleId'),
                'cti': cti,
                'is_remote_mcp_server': service_data.get('isRemoteMCPServer', False),
                'auth_types': service_data.get('authTypes', [])
            }
        )
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services using the ListServices operation (no registry name needed)."""
        try:
            # Build request payload - no registryName needed!
            payload = {'maxResults': max_results}
            
            if next_token:
                payload['nextToken'] = next_token
            
            # Make API call
            response = await self._make_mcp_request('ListServices', payload)
            
            # Parse services
            services = []
            service_list = response.get('services', [])
            
            for service_data in service_list:
                try:
                    service = self._parse_service_summary(service_data)
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
            error_str = str(e)
            # Handle NotAuthorizedException gracefully - user doesn't have access
            if 'NotAuthorizedException' in error_str or 'Not Authorized' in error_str:
                logger.warning(f"Amazon MCP registry access not available (API permissions required)")
                return {'services': [], 'next_token': None}
            logger.error(f"Error listing Amazon MCP services: {e}")
            raise
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information using GetServiceDetail operation."""
        try:
            payload = {'serviceId': service_id}
            response = await self._make_mcp_request('GetServiceDetail', payload)
            
            # The response includes bundle information
            bundle = response.get('bundle', {})
            metadata = response.get('metadata', {})
            
            # Extract service name from various possible locations
            service_name = (
                metadata.get('name') or 
                response.get('serviceName') or 
                response.get('name') or
                service_id  # Use service ID as fallback
            )
            
            # Extract description from various possible locations  
            service_description = (
                metadata.get('description') or
                response.get('serviceDescription') or
                response.get('description') or
                f"MCP service: {service_id}"  # Descriptive fallback
            )
            
            # Create a service summary from the response data
            service_data = {
                'serviceId': response.get('serviceId', service_id),
                'status': response.get('status', 'ACTIVE'),
                'version': response.get('version', 1),
                'serviceName': service_name,
                'serviceDescription': service_description,
                'instructions': bundle.get('instructions', {}),
                'cti': bundle.get('cti', ''),
                'bindleId': bundle.get('bindleId', ''),
                'securityReviewLink': bundle.get('securityReviewLink'),
                'supportLevel': bundle.get('supportLevel', 'Under assessment'),
                'createdAt': bundle.get('createdAt'),
                'lastUpdatedAt': bundle.get('lastUpdatedAt'),
                'isRemoteMCPServer': bundle.get('isRemoteMCPServer', False),
                'authTypes': bundle.get('authTypes', [])
            }
            
            service = self._parse_service_summary(service_data)
            
            # Add bundle information to metadata
            service.provider_metadata['bundle'] = bundle
            
            return service
            
        except Exception as e:
            logger.error(f"Error getting Amazon service detail: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search tools using the SearchTools operation."""
        try:
            payload = {
                'query': query,
                'numberOfTools': max_results
            }
            
            response = await self._make_mcp_request('SearchTools', payload)
            
            # Group tools by service
            server_tools: Dict[str, List[RegistryTool]] = {}
            for tool_data in response.get('tools', []):
                server_id = tool_data.get('mcpServerId')
                tool_name = tool_data.get('toolName', 'unknown')
                
                # Skip tools without valid server ID
                if not server_id or server_id == 'unknown':
                    continue
                
                if server_id not in server_tools:
                    server_tools[server_id] = []
                
                server_tools[server_id].append(RegistryTool(
                    tool_name=tool_name,
                    service_id=server_id,
                    description=f"Tool from {server_id}"
                ))
            
            # Also search by service name/ID regardless of tool matches
            try:
                services_result = await self.list_services(max_results=1000)
                all_services = services_result['services']
                query_lower = query.lower()
                
                # Create a lookup map for service details from list_services
                service_details_map = {s.service_id: s for s in all_services}
                
                for service in all_services:
                    if (query_lower in service.service_id.lower() or 
                        query_lower in service.service_name.lower() or
                        query_lower in service.service_description.lower()):
                        
                        # Add to results if not already found via tools
                        if service.service_id not in server_tools:
                            server_tools[service.service_id] = [RegistryTool(
                                tool_name="Service Match",
                                service_id=service.service_id,
                                description=f"Matched service: {service.service_name}"
                            )]
                            
            except Exception as e:
                logger.warning(f"Failed to search services by name: {e}")
            
            # Get service info for matching tools
            results = []
            for server_id, tools in server_tools.items():
                try:
                    logger.info(f"Getting service details for: {server_id}")
                    service = await self.get_service_detail(server_id)
                    logger.info(f"Successfully got service: {service.service_name} (ID: {service.service_id})")
                    
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=100.0
                    ))
                except Exception as e:
                    logger.error(f"Failed to get details for {server_id}: {e}")
                    logger.error(f"Exception type: {type(e).__name__}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
            
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
            
            # Check platform compatibility
            import platform
            current_os = platform.system().lower()
            supported_platforms = service.provider_metadata.get('supportedPlatforms', [])
            
            if supported_platforms and current_os not in [p.lower() for p in supported_platforms]:
                raise RuntimeError(f"Service {service_id} does not support {current_os}. Supported platforms: {supported_platforms}")
            
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
                "security_review_url": service.security_review_url,
                "_comment": "Installed via Ziya MCP Registry Manager"
            }
            
            # Handle installation based on bundle information
            bundle = service.provider_metadata.get('bundle', {})
            generic_bundle = bundle.get('genericBundle', {})
            
            if install_type == InstallationType.REMOTE:
                config_entries['remote_url'] = instructions.get('url')
                config_entries['transport'] = instructions.get('transport', 'streamable-http')
            else:
                # Execute installation commands from bundle
                install_commands = generic_bundle.get('install', [])
                if install_commands:
                    logger.info(f"Executing installation commands for {service_id}: {install_commands}")
                    
                    for install_cmd in install_commands:
                        executable = install_cmd.get('executable')
                        args = install_cmd.get('args', [])
                        
                        if executable and args:
                            import subprocess
                            try:
                                # Execute the installation command
                                result = subprocess.run([executable] + args, 
                                                      capture_output=True, text=True, timeout=300)
                                if result.returncode != 0:
                                    logger.error(f"Installation command failed: {result.stderr}")
                                    raise RuntimeError(f"Installation failed: {result.stderr}")
                                else:
                                    logger.info(f"Installation command succeeded: {result.stdout}")
                            except subprocess.TimeoutExpired:
                                raise RuntimeError("Installation command timed out")
                            except FileNotFoundError:
                                raise RuntimeError(f"Installation executable '{executable}' not found")
                else:
                    # Fallback: try mcp-registry install command for Amazon internal services
                    logger.info(f"No install commands found, trying mcp-registry install {service_id}")
                    import subprocess
                    try:
                        result = subprocess.run(['mcp-registry', 'install', service_id], 
                                              capture_output=True, text=True, timeout=300, cwd=str(install_dir))
                        if result.returncode != 0:
                            logger.warning(f"mcp-registry install failed: {result.stderr}")
                            # Don't fail completely, continue with config-only installation
                        else:
                            logger.info(f"mcp-registry install succeeded: {result.stdout}")
                    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                        logger.warning(f"Could not run mcp-registry install: {e}")
                        # Continue with config-only installation
                
                # Set up runtime configuration
                run_config = generic_bundle.get('run', {})
                if run_config:
                    executable = run_config.get('executable')
                    if executable:
                        config_entries['command'] = executable
                        # Add any environment variables if specified
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
            logger.info("Testing connection to Amazon MCP Registry Service...")
            
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

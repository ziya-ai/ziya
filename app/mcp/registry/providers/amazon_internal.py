"""
Amazon Internal MCP Registry Provider.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel
)
from app.utils.logging_utils import logger


class AmazonInternalRegistryProvider(RegistryProvider):
    """Provider for Amazon's internal MCP registry using the Smithy API."""
    
    def __init__(self, registry_name: str = "MainRegistry", region: str = "us-west-2"):
        self.registry_name = registry_name
        self.region = region
        self.endpoint_url = os.environ.get('MCP_REGISTRY_ENDPOINT', 
            'https://api.registry.mcp.aws.dev/')
        self._session = None
    
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
    
    def _get_session(self):
        """Get or create boto3 session with Midway credentials."""
        if not self._session:
            # For Midway, use the default credential chain which includes Midway
            # Make sure AWS_PROFILE is not set to avoid using AWS credentials
            import os
            env = os.environ.copy()
            env.pop('AWS_PROFILE', None)
            env.pop('AWS_ACCESS_KEY_ID', None)
            env.pop('AWS_SECRET_ACCESS_KEY', None)
            env.pop('AWS_SESSION_TOKEN', None)
            
            # Create session without profile to use Midway credentials
            self._session = boto3.Session()
        return self._session
    
    def _make_request(self, operation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make a signed request to the MCP Registry Service using SigV4."""
        session = self._get_session()
        credentials = session.get_credentials()
        
        # Prepare request
        headers = {
            'Content-Type': 'application/x-amz-json-1.0',
            'X-Amz-Target': f'com.amazon.mcpregistryservice.MCPRegistryService.{operation}'
        }
        
        body = json.dumps(payload)
        
        # Create AWS request for signing
        request = AWSRequest(
            method='POST',
            url=self.endpoint_url,
            data=body,
            headers=headers
        )
        
        # Sign with SigV4
        SigV4Auth(credentials, 'mcp-registry-service', self.region).add_auth(request)
        
        # Make the request
        response = requests.post(
            self.endpoint_url,
            headers=dict(request.headers),
            data=body,
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"Request failed with status {response.status_code}: {response.text}")
        
        response.raise_for_status()
        return response.json()
    
    def _convert_service_status(self, smithy_status: str) -> ServiceStatus:
        """Convert Smithy API status to our enum."""
        status_map = {
            'ACTIVE': ServiceStatus.ACTIVE,
            'PENDING': ServiceStatus.PENDING,
            'DELETED': ServiceStatus.DELETED
        }
        return status_map.get(smithy_status, ServiceStatus.PENDING)
    
    def _convert_support_level(self, smithy_level: str) -> SupportLevel:
        """Convert Smithy API support level to our enum."""
        level_map = {
            'Recommended': SupportLevel.RECOMMENDED,
            'Supported': SupportLevel.SUPPORTED,
            'Under assessment': SupportLevel.UNDER_ASSESSMENT,
            'In development': SupportLevel.IN_DEVELOPMENT
        }
        return level_map.get(smithy_level, SupportLevel.UNDER_ASSESSMENT)
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from Amazon's internal registry."""
        try:
            payload = {
                'registryName': self.registry_name,
                'maxResults': max_results
            }
            if next_token:
                payload['nextToken'] = next_token
            
            response = self._make_request('ListServices', payload)
            
            services = []
            for service_data in response.get('services', []):
                service = RegistryServiceInfo(
                    service_id=service_data['serviceId'],
                    service_name=service_data['serviceName'],
                    service_description=service_data['serviceDescription'],
                    version=service_data.get('version', 1),
                    status=self._convert_service_status(service_data['status']),
                    support_level=self._convert_support_level(service_data['supportLevel']),
                    created_at=datetime.fromisoformat(service_data['createdAt']),
                    last_updated_at=datetime.fromisoformat(service_data['lastUpdatedAt']),
                    installation_instructions=service_data['instructions'],
                    security_review_url=service_data.get('securityReviewLink'),
                    provider_metadata={
                        'cti': service_data['cti'],
                        'bindle_id': service_data.get('bindleId')
                    }
                )
                services.append(service)
            
            return {
                'services': services,
                'next_token': response.get('nextToken')
            }
            
        except ClientError as e:
            logger.error(f"Error listing services from Amazon registry: {e}")
            raise
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        try:
            response = self._make_request('GetServiceDetail', {'serviceId': service_id})
            
            # For detailed info, we might need to combine with list data
            # This is a simplified implementation
            services = await self.list_services(max_results=1000)
            service = next(
                (s for s in services['services'] if s.service_id == service_id),
                None
            )
            
            if not service:
                raise ValueError(f"Service {service_id} not found")
            
            # Enhance with bundle information
            service.provider_metadata.update({
                'bundle': response['bundle'],
                'detailed_status': response['status']
            })
            
            return service
            
        except ClientError as e:
            logger.error(f"Error getting service detail: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search tools using Amazon's registry search API."""
        try:
            response = self._make_request('SearchTools', {
                'query': query,
                'numberOfTools': max_results
            })
            
            # Group tools by service
            service_tools = {}
            for tool_data in response.get('tools', []):
                service_id = tool_data['mcpServerId']
                if service_id not in service_tools:
                    service_tools[service_id] = []
                
                service_tools[service_id].append(RegistryTool(
                    tool_name=tool_data['toolName'],
                    service_id=service_id
                ))
            
            # Get service info for each service that has matching tools
            service_ids = list(service_tools.keys())
            if not service_ids:
                return []
            
            # Get service summaries
            batch_response = self._make_request('BatchGetSummary', {'serviceIds': service_ids})
            
            results = []
            for service_data in batch_response['summary']['resolvedSummary']:
                service = RegistryServiceInfo(
                    service_id=service_data['serviceId'],
                    service_name=service_data['serviceName'],
                    service_description=service_data['serviceDescription'],
                    version=service_data.get('version', 1),
                    status=self._convert_service_status(service_data['status']),
                    support_level=self._convert_support_level(service_data['supportLevel']),
                    created_at=datetime.fromisoformat(service_data['createdAt']),
                    last_updated_at=datetime.fromisoformat(service_data['lastUpdatedAt']),
                    installation_instructions=service_data['instructions'],
                    provider_metadata={
                        'cti': service_data['cti'],
                        'bindle_id': service_data.get('bindleId')
                    }
                )
                
                results.append(ToolSearchResult(
                    service=service,
                    matching_tools=service_tools[service_data['serviceId']]
                ))
            
            return results
            
        except ClientError as e:
            logger.error(f"Error searching tools: {e}")
            raise
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service using Amazon's registry instructions."""
        try:
            service = await self.get_service_detail(service_id)
            
            # Create installation directory
            install_dir = Path.home() / ".ziya" / "mcp_services" / service_id
            install_dir.mkdir(parents=True, exist_ok=True)
            
            # Execute installation instructions
            instructions = service.installation_instructions
            if 'install' in instructions and instructions['install']:
                install_cmd = instructions['install']
                logger.info(f"Installing {service.service_name}: {install_cmd}")
                
                result = subprocess.run(
                    install_cmd,
                    shell=True,
                    cwd=str(install_dir),
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode != 0:
                    return InstallationResult(
                        success=False,
                        service_id=service_id,
                        server_name="",
                        error_message=f"Installation failed: {result.stderr}"
                    )
            
            # Build server configuration
            server_name = service.service_name.lower().replace(' ', '_').replace('-', '_')
            config_entries = {
                "command": self._build_command_array(instructions, str(install_dir)),
                "args": instructions.get('args', []),
                "enabled": True,
                "description": service.service_description,
                "registry_provider": self.identifier,
                "service_id": service_id,
                "version": service.version,
                "support_level": service.support_level.value,
                "installed_at": datetime.now().isoformat(),
                "installation_path": str(install_dir)
            }
            
            if 'env' in instructions:
                config_entries['env'] = instructions['env']
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir),
                config_entries=config_entries
            )
            
        except Exception as e:
            logger.error(f"Error installing service {service_id}: {e}")
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
    
    def _build_command_array(self, instructions: Dict[str, Any], install_path: str) -> List[str]:
        """Build command array from installation instructions."""
        command = instructions.get('command', '')
        
        if not command:
            # Look for executables in install directory
            install_dir = Path(install_path)
            for pattern in ['*.py', 'server.js', 'index.js', 'bin/*']:
                matches = list(install_dir.glob(pattern))
                if matches:
                    command = str(matches[0])
                    break
        
        # Convert to command array
        if isinstance(command, str):
            if command.endswith('.py'):
                return ['python', '-u', command]
            elif command.endswith('.js'):
                return ['node', command]
            else:
                return [command]
        
        return command if isinstance(command, list) else [str(command)]
    
    async def test_connection(self) -> bool:
        """Test the connection to the MCP Registry Service."""
        try:
            logger.info("Testing connection to MCP Registry Service...")
            
            response = self._make_request('ListServices', {
                'registryName': self.registry_name,
                'maxResults': 1
            })
            
            logger.info(f"Connection test successful. Found {len(response.get('services', []))} services")
            return True
            
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            logger.error(f"Error details: {type(e).__name__}: {str(e)}")
            return False

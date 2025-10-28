"""
MCP Registry Service client for discovering and installing MCP servers.
"""

import json
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from app.utils.logging_utils import logger


@dataclass
class MCPServiceSummary:
    """Represents an MCP service from the registry."""
    service_id: str
    version: int
    service_name: str
    service_description: str
    instructions: Dict[str, Any]
    status: str
    support_level: str
    created_at: datetime
    last_updated_at: datetime
    cti: str
    security_review_link: Optional[str] = None
    bindle_id: Optional[str] = None


@dataclass
class MCPTool:
    """Represents a tool from the registry search."""
    mcp_server_id: str
    tool_name: str


class MCPRegistryClient:
    """Client for interacting with the MCP Registry Service."""
    
    def __init__(self, registry_name: str = "MainRegistry", region: str = "us-west-2"):
        """Initialize the registry client."""
        self.registry_name = registry_name
        self.region = region
        self._client = None
        
    def _get_client(self):
        """Get or create the boto3 client."""
        if not self._client:
            self._client = boto3.client('mcp-registry', region_name=self.region)
        return self._client
    
    async def list_services(self, max_results: int = 50, next_token: Optional[str] = None) -> Dict[str, Any]:
        """List available MCP services from the registry."""
        try:
            client = self._get_client()
            
            params = {
                'registryName': self.registry_name,
                'maxResults': max_results
            }
            if next_token:
                params['nextToken'] = next_token
                
            response = client.list_services(**params)
            
            # Convert response to our format
            services = []
            for service_data in response.get('services', []):
                service = MCPServiceSummary(
                    service_id=service_data['serviceId'],
                    version=service_data.get('version', 1),
                    service_name=service_data['serviceName'],
                    service_description=service_data['serviceDescription'],
                    instructions=service_data['instructions'],
                    status=service_data['status'],
                    support_level=service_data['supportLevel'],
                    created_at=datetime.fromisoformat(service_data['createdAt']),
                    last_updated_at=datetime.fromisoformat(service_data['lastUpdatedAt']),
                    cti=service_data['cti'],
                    security_review_link=service_data.get('securityReviewLink'),
                    bindle_id=service_data.get('bindleId')
                )
                services.append(service)
                
            return {
                'services': services,
                'next_token': response.get('nextToken')
            }
            
        except ClientError as e:
            logger.error(f"Error listing MCP services: {e}")
            raise
    
    async def get_service_detail(self, service_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific MCP service."""
        try:
            client = self._get_client()
            response = client.get_service_detail(serviceId=service_id)
            
            return {
                'service_id': response['serviceId'],
                'bundle': response['bundle'],
                'status': response['status'],
                'version': response.get('version')
            }
            
        except ClientError as e:
            logger.error(f"Error getting service detail for {service_id}: {e}")
            raise
    
    async def search_tools(self, query: str, number_of_tools: int = 10) -> List[MCPTool]:
        """Search for tools matching a natural language query."""
        try:
            client = self._get_client()
            response = client.search_tools(
                query=query,
                numberOfTools=number_of_tools
            )
            
            tools = []
            for tool_data in response.get('tools', []):
                tool = MCPTool(
                    mcp_server_id=tool_data['mcpServerId'],
                    tool_name=tool_data['toolName']
                )
                tools.append(tool)
                
            return tools
            
        except ClientError as e:
            logger.error(f"Error searching tools: {e}")
            raise
    
    async def batch_get_summary(self, service_ids: List[str]) -> Dict[str, Any]:
        """Get summaries for multiple services at once."""
        try:
            client = self._get_client()
            response = client.batch_get_summary(serviceIds=service_ids)
            
            return {
                'resolved': response['summary']['resolvedSummary'],
                'unresolved': response['summary']['unresolvedSummary']
            }
            
        except ClientError as e:
            logger.error(f"Error batch getting summaries: {e}")
            raise


# Global registry client instance
_registry_client: Optional[MCPRegistryClient] = None

def get_registry_client() -> MCPRegistryClient:
    """Get the global registry client instance."""
    global _registry_client
    if _registry_client is None:
        _registry_client = MCPRegistryClient()
    return _registry_client

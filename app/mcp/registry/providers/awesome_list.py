"""
Awesome List Registry Provider for community MCP servers.
Scrapes and parses awesome-mcp-servers lists from GitHub.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import httpx

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.mcp.registry.installation_helper import InstallationHelper
from app.utils.logging_utils import logger


class AwesomeListRegistryProvider(RegistryProvider):
    """Provider that aggregates multiple awesome-mcp-servers lists."""
    
    def __init__(
        self,
        lists: Optional[List[str]] = None
    ):
        """
        Initialize with list of awesome-list repos.
        
        Args:
            lists: List of GitHub repos in format "owner/repo"
        """
        self.lists = lists or [
            "punkpeye/awesome-mcp-servers",
            "wong2/awesome-mcp-servers",
            "appcypher/awesome-mcp-servers"
        ]
        self._cache = {}
        self._cache_time = None
        self._http_client: Optional[httpx.AsyncClient] = None
    
    @property
    def name(self) -> str:
        return "Awesome MCP Lists"
    
    @property
    def identifier(self) -> str:
        return "awesome-lists"
    
    @property
    def is_internal(self) -> bool:
        return False
    
    @property
    def supports_search(self) -> bool:
        return True
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={'User-Agent': 'Ziya-AwesomeList-Client/1.0'}
            )
        return self._http_client
    
    def _should_refresh_cache(self) -> bool:
        """Check if cache should be refreshed (2 hour TTL for awesome lists)."""
        if not self._cache_time:
            return True
        
        from datetime import timedelta
        return datetime.now() - self._cache_time > timedelta(hours=2)
    async def _fetch_awesome_list(self, repo: str) -> List[Dict[str, Any]]:
        """Fetch and parse an awesome-mcp-servers README."""
        try:
            client = self._get_http_client()
            
            # Fetch raw README
            url = f"https://raw.githubusercontent.com/{repo}/master/README.md"
            response = await client.get(url)
            
            # Try 'main' branch if 'master' fails
            if response.status_code == 404:
                url = f"https://raw.githubusercontent.com/{repo}/main/README.md"
                response = await client.get(url)
            
            response.raise_for_status()
            
            
            return self._parse_markdown_list(response.text, repo)
            
        except Exception as e:
            logger.error(f"Error fetching awesome list {repo}: {e}")
            return []
    
    def _parse_markdown_list(self, markdown: str, source_repo: str) -> List[Dict[str, Any]]:
        """Parse markdown awesome list into server entries."""
        servers = []
        
        # Enhanced pattern to match various formats:
        # - **[Name](url)** icons - description
        # - [owner/repo](url) icons - description  
        # - <img...> **[Name](url)** - description
        
        pattern = r'-\s+(?:<img[^>]+>)?\s*(?:\*\*)?(?:\[([^\]]+)\]\(([^\)]+)\))(?:\*\*)?\s+([^-\n]*?)(?:-\s*(.+?))?(?=\n-|\n###|\n##|\n\n|\Z)'
        
        matches = re.finditer(pattern, markdown, re.MULTILINE | re.DOTALL)
        
        parsed_count = 0
        for match in matches:
            try:
                name_raw = match.group(1)
                url = match.group(2)
                metadata = match.group(3) or ""
                description = match.group(4) or ""
                
                if not name_raw or not url:
                    continue
                
                name = name_raw.strip()
                url = url.strip()
                metadata = metadata.strip()
                description = description.strip()
                
                # Clean up description - remove leading/trailing whitespace and newlines
                description = ' '.join(description.split())
            except Exception as e:
                logger.debug(f"Failed to extract match groups: {e}")
                continue
            
            # Skip section headers and non-server entries
            if not url.startswith('http'):
                continue
            
            # Skip entries that look like documentation or resources
            name_lower = name.lower()
            if any(skip in name_lower for skip in ['tutorial', 'documentation', 'guide', 'awesome', 'community', 'discord']):
                continue
            
            # Extract GitHub repo if it's a GitHub URL
            repo_url = None
            if 'github.com' in url:
                repo_url = url
            # Parse metadata for icons/platforms
            tags = self._extract_tags_from_metadata(metadata, description)
            
            # Extract language and installation method
            install_info = self._infer_installation_method(name, url, metadata, description)
            
            servers.append({
                'name': name,
                'url': url,
                'repository': repo_url,
                'description': description,
                'tags': tags,
                'source_list': source_repo,
                'metadata': metadata,
                'install_type': install_info['type'],
                'install_package': install_info.get('package')
            })
            parsed_count += 1
        
        logger.info(f"Parsed {parsed_count} servers from {source_repo} (from {len(list(re.finditer(pattern, markdown, re.MULTILINE | re.DOTALL)))} matches)")
        return servers
    
    
    def _extract_tags_from_metadata(self, metadata: str, description: str) -> List[str]:
        """Extract tags from emoji metadata and description."""
        tags = []
        
        # Icon to tag mapping
        icon_map = {
            '🐍': 'python',
            '📇': 'typescript',
            '🏎️': 'go',
            '🦀': 'rust',
            '#️⃣': 'csharp',
            '☕': 'java',
            '☁️': 'cloud',
            '🏠': 'local',
            '🍎': 'macos',
            '🪟': 'windows',
            '🐧': 'linux',
            '🎖️': 'official'
        }
        
        for icon, tag in icon_map.items():
            if icon in metadata:
                tags.append(tag)
        
        # Extract category from description
        description_lower = description.lower()
        categories = {
            'database': ['database', 'sql', 'postgres', 'mysql'],
            'files': ['file', 'filesystem', 'storage'],
            'web': ['web', 'browser', 'http', 'scraping'],
            'cloud': ['aws', 'azure', 'gcp', 'cloud'],
            'dev-tools': ['git', 'github', 'gitlab'],
            'search': ['search', 'query']
        }
        
        for category, keywords in categories.items():
            if any(kw in description_lower for kw in keywords):
                tags.append(category)
        
        return tags
    
    def _infer_installation_method(
        self, 
        name: str, 
        url: str, 
        metadata: str, 
        description: str
    ) -> Dict[str, Any]:
        """Infer installation method from available information."""
        
        # Check for npm package indicators
        if 'npm' in description.lower() or 'npx' in description.lower():
            # Try to extract package name from description
            # Match pattern: npm install [-g] package-name
            npm_match = re.search(r'npm install\s+(?:-g\s+)?(@?[\w-]+/[\w-]+|[\w-]+)', description, re.IGNORECASE)
            if npm_match:
                return {'type': 'npm', 'package': npm_match.group(1)}
            return {'type': 'npm'}
        
        # Check for Python package indicators
        if 'pip install' in description.lower() or '🐍' in metadata:
            pip_match = re.search(r'pip install\s+([\w-]+)', description, re.IGNORECASE)
            if pip_match:
                return {'type': 'pypi', 'package': pip_match.group(1)}
            return {'type': 'pypi'}
        
        # Default to git clone
        return {'type': 'git', 'repository': url if 'github.com' in url else None}
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from awesome lists."""
        try:
            # Refresh cache if needed
            if self._should_refresh_cache():
                all_servers = []
                for repo in self.lists:
                    servers = await self._fetch_awesome_list(repo)
                    all_servers.extend(servers)
                
                # Deduplicate by repository URL
                seen = set()
                unique_servers = []
                for server in all_servers:
                    repo = server.get('repository', server.get('url'))
                    if repo not in seen:
                        seen.add(repo)
                        unique_servers.append(server)
                
                self._cache = {'servers': unique_servers}
                self._cache_time = datetime.now()
                logger.info(f"Cached {len(unique_servers)} unique servers from awesome lists")
            
            servers = self._cache.get('servers', [])
            
            # Parse into standard format
            services = []
            for server_data in servers:
                try:
                    service = self._parse_awesome_server(server_data)
                    services.append(service)
                except Exception as e:
                    logger.warning(f"Failed to parse awesome list server: {e}")
                    continue
            
            # Apply pagination
            start_idx = int(next_token or 0)
            end_idx = min(start_idx + max_results, len(services))
            paginated_services = services[start_idx:end_idx]
            
            return {
                'services': paginated_services,
                'next_token': str(end_idx) if end_idx < len(services) else None
            }
            
        except Exception as e:
            logger.error(f"Error listing awesome list services: {e}")
            raise
    
    def _parse_awesome_server(self, server_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse awesome list server entry into standard format."""
        # Generate service ID from repository or name
        repo_or_url = server_data.get('repository') or server_data.get('url', '')
        service_id = repo_or_url.replace('https://github.com/', '').replace('/', '.')
        
        return RegistryServiceInfo(
            service_id=service_id,
            service_name=server_data['name'],
            service_description=server_data.get('description', 'No description'),
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY if 'official' not in server_data.get('tags', []) else SupportLevel.RECOMMENDED,
            created_at=datetime.now(),  # Awesome lists don't have timestamps
            last_updated_at=datetime.now(),
            installation_instructions={
                'type': server_data.get('install_type', 'git'),
                'package': server_data.get('install_package'),
                'repository': server_data.get('repository'),
                'url': server_data.get('url')
            },
            installation_type=InstallationHelper.detect_installation_type({
                'type': server_data.get('install_type', 'git')
            }),
            tags=server_data.get('tags', []),
            repository_url=server_data.get('repository'),
            homepage_url=server_data.get('url'),
            provider_metadata={
                'provider_id': 'awesome-lists',
                'source_list': server_data.get('source_list'),
                'metadata': server_data.get('metadata')
            }
        )
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        services_result = await self.list_services(max_results=10000)
        service = next(
            (s for s in services_result['services'] if s.service_id == service_id),
            None
        )
        
        if not service:
            raise ValueError(f"Service {service_id} not found in awesome lists")
        
        return service
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search awesome lists for tools."""
        try:
            services_result = await self.list_services(max_results=max_results * 3)
            
            results = []
            query_lower = query.lower()
            
            for service in services_result['services']:
                relevance_score = 0
                
                # Exact name match
                if query_lower == service.service_name.lower():
                    relevance_score += 100
                
                # Name contains
                if query_lower in service.service_name.lower():
                    relevance_score += 50
                
                # Description contains
                if query_lower in service.service_description.lower():
                    relevance_score += 20
                
                # Tag matches
                for tag in service.tags:
                    if query_lower == tag.lower():
                        relevance_score += 40
                    elif query_lower in tag.lower():
                        relevance_score += 20
                
                if relevance_score > 0:
                    tools = [RegistryTool(
                        tool_name=f"{service.service_name}_tool",
                        service_id=service.service_id,
                        description=service.service_description[:100]
                    )]
                    
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=relevance_score
                    ))
            
            results.sort(key=lambda x: x.relevance_score or 0, reverse=True)
            return results[:max_results]
            
        except Exception as e:
            logger.error(f"Error searching awesome lists: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service from awesome list."""
        return InstallationResult(
            success=False,
            service_id=service_id,
            server_name="",
            error_message="Awesome list servers must be installed manually from their repositories."
        )
    
    async def validate_service(self, service_id: str) -> bool:
        """Validate service availability."""
        try:
            await self.get_service_detail(service_id)
            return True
        except Exception:
            return False
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

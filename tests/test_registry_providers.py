"""
Unit tests for registry provider implementations.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock
import json

from app.mcp.registry.providers.official_mcp import OfficialMCPRegistryProvider
from app.mcp.registry.providers.pulsemcp import PulseMCPRegistryProvider
from app.mcp.registry.providers.smithery import SmitheryRegistryProvider
from app.mcp.registry.providers.awesome_list import AwesomeListRegistryProvider
from app.mcp.registry.interface import ServiceStatus, SupportLevel, InstallationType


class TestOfficialMCPProvider:
    """Tests for Official MCP Registry Provider."""
    
    @pytest.fixture
    def provider(self):
        return OfficialMCPRegistryProvider()
    
    @pytest.fixture
    def mock_api_response(self):
        """Mock API response from official registry."""
        return {
            "servers": [
                {
                    "server": {
                        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-09-29/server.schema.json",
                        "name": "io.github.test/example",
                        "description": "Example MCP server",
                        "repository": {
                            "url": "https://github.com/test/example",
                            "source": "github"
                        },
                        "version": "1.0.0",
                        "packages": [
                            {
                                "registryType": "npm",
                                "identifier": "example-mcp",
                                "version": "1.0.0",
                                "runtimeHint": "npx",
                                "transport": {"type": "stdio"}
                            }
                        ]
                    },
                    "_meta": {
                        "io.modelcontextprotocol.registry/official": {
                            "status": "active",
                            "publishedAt": "2025-01-01T00:00:00Z",
                            "updatedAt": "2025-01-02T00:00:00Z",
                            "isLatest": True
                        }
                    }
                }
            ],
            "metadata": {
                "count": 1
            }
        }
    
    def test_provider_properties(self, provider):
        """Test provider basic properties."""
        assert provider.name == "Official MCP Registry"
        assert provider.identifier == "official-mcp"
        assert provider.is_internal == False
        assert provider.supports_search == True
    
    def test_map_status(self, provider):
        """Test status mapping."""
        assert provider._map_status('active') == ServiceStatus.ACTIVE
        assert provider._map_status('deleted') == ServiceStatus.DELETED
        assert provider._map_status('deprecated') == ServiceStatus.DEPRECATED
        assert provider._map_status('unknown') == ServiceStatus.ACTIVE  # Default
    
    def test_infer_support_level(self, provider):
        """Test support level inference."""
        # Official modelcontextprotocol server
        server1 = {'name': 'io.modelcontextprotocol/test'}
        assert provider._infer_support_level(server1) == SupportLevel.RECOMMENDED
        
        # Official repo but different namespace
        server2 = {
            'name': 'io.github.test/server',
            'repository': {'url': 'https://github.com/modelcontextprotocol/test'}
        }
        assert provider._infer_support_level(server2) == SupportLevel.RECOMMENDED
        
        # Third-party
        server3 = {
            'name': 'io.github.someone/server',
            'repository': {'url': 'https://github.com/someone/server'}
        }
        assert provider._infer_support_level(server3) == SupportLevel.COMMUNITY
    
    def test_parse_server_entry(self, provider, mock_api_response):
        """Test parsing of server entry."""
        entry = mock_api_response['servers'][0]
        service = provider._parse_server_entry(entry)
        
        assert service.service_id == "io.github.test/example"
        assert service.service_name == "io.github.test/example"
        assert service.service_description == "Example MCP server"
        assert service.status == ServiceStatus.ACTIVE
        assert service.repository_url == "https://github.com/test/example"
        
        # Check installation instructions
        assert service.installation_instructions['type'] == 'npm'
        assert service.installation_instructions['package'] == 'example-mcp'
        assert service.installation_instructions['runtime_hint'] == 'npx'
    
    def test_build_installation_instructions_npm(self, provider):
        """Test building NPM installation instructions."""
        server = {
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": "my-package",
                    "version": "1.0.0",
                    "runtimeHint": "npx",
                    "environmentVariables": [
                        {"name": "API_KEY", "isRequired": True, "isSecret": True}
                    ]
                }
            ]
        }
        
        instructions = provider._build_installation_instructions(server)
        
        assert instructions['type'] == 'npm'
        assert instructions['package'] == 'my-package'
        assert instructions['version'] == '1.0.0'
        assert len(instructions['env_vars']) == 1
    
    def test_build_installation_instructions_pypi(self, provider):
        """Test building PyPI installation instructions."""
        server = {
            "packages": [
                {
                    "registryType": "pypi",
                    "identifier": "my-python-package",
                    "version": "0.5.0"
                }
            ]
        }
        
        instructions = provider._build_installation_instructions(server)
        
        assert instructions['type'] == 'pypi'
        assert instructions['package'] == 'my-python-package'
        assert instructions['version'] == '0.5.0'
    
    def test_build_installation_instructions_remote(self, provider):
        """Test building remote server instructions."""
        server = {
            "remotes": [
                {
                    "type": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": [
                        {"name": "Authorization", "isSecret": True}
                    ]
                }
            ]
        }
        
        instructions = provider._build_installation_instructions(server)
        
        assert instructions['type'] == 'remote'
        assert instructions['url'] == 'https://example.com/mcp'
        assert instructions['transport'] == 'streamable-http'
        assert len(instructions['headers']) == 1
    
    def test_extract_tags(self, provider):
        """Test tag extraction from server metadata."""
        server = {
            'name': 'io.github.test/postgres-server',
            'description': 'A PostgreSQL database server with query capabilities',
            'packages': [{'registryType': 'pypi'}]
        }
        
        tags = provider._extract_tags(server)
        
        assert 'database' in tags
        assert 'python' in tags
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient')
    async def test_list_services(self, mock_client_class, provider, mock_api_response):
        """Test listing services from API."""
        # Setup mock
        mock_response = Mock()
        mock_response.json.return_value = mock_api_response
        mock_response.raise_for_status = Mock()
        
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        
        # Force create new client
        provider._http_client = mock_client
        
        # Test
        result = await provider.list_services(max_results=50)
        
        assert 'services' in result
        assert len(result['services']) == 1
        assert result['services'][0].service_id == "io.github.test/example"
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient')
    async def test_search_tools(self, mock_client_class, provider, mock_api_response):
        """Test searching for tools."""
        # Setup mock
        mock_response = Mock()
        mock_response.json.return_value = mock_api_response
        mock_response.raise_for_status = Mock()
        
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        provider._http_client = mock_client
        
        # Test
        results = await provider.search_tools("example", max_results=10)
        
        assert len(results) == 1
        assert results[0].service.service_name == "io.github.test/example"
        assert results[0].relevance_score > 0


class TestAwesomeListProvider:
    """Tests for Awesome List Provider."""
    
    @pytest.fixture
    def provider(self):
        return AwesomeListRegistryProvider()
    
    @pytest.fixture
    def sample_markdown(self):
        """Sample markdown from awesome list."""
        return """
# Awesome MCP Servers

## Databases

- **[PostgreSQL](https://github.com/user/postgres-mcp)** 🐍 🏠 - PostgreSQL database integration
- [MySQL Server](https://github.com/user/mysql-mcp) 📇 ☁️ - MySQL database server with query tools

## File Systems

- **[Filesystem](https://github.com/modelcontextprotocol/filesystem)** 🎖️ 📇 🏠 - Secure file operations
"""
    
    def test_provider_properties(self, provider):
        """Test provider properties."""
        assert provider.name == "Awesome MCP Lists"
        assert provider.identifier == "awesome-lists"
        assert provider.is_internal == False
        assert provider.supports_search == True
    
    def test_parse_markdown_list(self, provider, sample_markdown):
        """Test parsing markdown list."""
        servers = provider._parse_markdown_list(sample_markdown, "test/awesome-list")
        
        assert len(servers) >= 2
        
        # Check first server
        postgres = next(s for s in servers if 'postgres' in s['name'].lower())
        assert postgres['name'] == 'PostgreSQL'
        assert postgres['repository'] == 'https://github.com/user/postgres-mcp'
        assert 'python' in postgres['tags']
        assert 'local' in postgres['tags']
        
        # Check second server
        mysql = next(s for s in servers if 'mysql' in s['name'].lower())
        assert 'typescript' in mysql['tags']
        assert 'cloud' in mysql['tags']
    
    def test_extract_tags_from_metadata(self, provider):
        """Test tag extraction from emoji metadata."""
        metadata = "🐍 🏠 🍎 🪟"
        description = "A database server with PostgreSQL support"
        
        tags = provider._extract_tags_from_metadata(metadata, description)
        
        assert 'python' in tags
        assert 'local' in tags
        assert 'macos' in tags
        assert 'windows' in tags
        assert 'database' in tags
    
    def test_infer_installation_method_npm(self, provider):
        """Test NPM installation inference."""
        name = "Test Server"
        url = "https://github.com/user/repo"
        metadata = "📇"
        description = "Install with: npm install test-server"
        
        install_info = provider._infer_installation_method(name, url, metadata, description)
        
        assert install_info['type'] == 'npm'
        assert install_info['package'] == 'test-server'
    
    def test_infer_installation_method_python(self, provider):
        """Test Python installation inference."""
        name = "Test Server"
        url = "https://github.com/user/repo"
        metadata = "🐍"
        description = "Install with: pip install my-mcp-server"
        
        install_info = provider._infer_installation_method(name, url, metadata, description)
        
        assert install_info['type'] == 'pypi'
        assert install_info['package'] == 'my-mcp-server'
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient')
    async def test_fetch_awesome_list(self, mock_client_class, provider, sample_markdown):
        """Test fetching and parsing awesome list."""
        # Setup mock
        mock_response = Mock()
        mock_response.text = sample_markdown
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        provider._http_client = mock_client
        
        # Test
        servers = await provider._fetch_awesome_list("test/repo")
        
        assert len(servers) >= 2
        assert any('postgres' in s['name'].lower() for s in servers)


class TestPulseMCPProvider:
    """Tests for PulseMCP Provider."""
    
    @pytest.fixture
    def provider(self):
        return PulseMCPRegistryProvider()
    
    def test_provider_properties(self, provider):
        """Test provider properties."""
        assert provider.name == "PulseMCP"
        assert provider.identifier == "pulsemcp"
        assert provider.is_internal == False
    
    def test_build_installation_instructions_from_packages(self, provider):
        """Test building installation instructions from package data."""
        server = {
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": "test-package",
                    "version": "2.0.0",
                    "runtimeHint": "npx"
                }
            ]
        }
        
        instructions = provider._build_installation_instructions(server)
        
        assert instructions['type'] == 'npm'
        assert instructions['package'] == 'test-package'
        assert instructions['version'] == '2.0.0'
    
    def test_build_installation_instructions_from_remotes(self, provider):
        """Test building instructions for remote servers."""
        server = {
            "remotes": [
                {
                    "type": "sse",
                    "url": "https://example.com/mcp/sse"
                }
            ]
        }
        
        instructions = provider._build_installation_instructions(server)
        
        assert instructions['type'] == 'remote'
        assert instructions['url'] == 'https://example.com/mcp/sse'
        assert instructions['transport'] == 'sse'


class TestSmitheryProvider:
    """Tests for Smithery Provider."""
    
    @pytest.fixture
    def provider(self):
        return SmitheryRegistryProvider()
    
    @pytest.fixture
    def mock_html(self):
        """Mock HTML from Smithery website."""
        return """
        <html>
            <body>
                <div class="server-list">
                    <a href="/server/filesystem">
                        <h3>Filesystem Server</h3>
                        <p>Secure file operations with access controls</p>
                    </a>
                    <a href="/server/postgres">
                        <h3>PostgreSQL</h3>
                        <p>Database integration for PostgreSQL</p>
                    </a>
                </div>
            </body>
        </html>
        """
    
    def test_provider_properties(self, provider):
        """Test provider properties."""
        assert provider.name == "Smithery"
        assert provider.identifier == "smithery"
        assert provider.is_internal == False
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient')
    async def test_fetch_servers_from_web(self, mock_client_class, provider, mock_html):
        """Test web scraping from Smithery."""
        # Setup mock
        mock_response = Mock()
        mock_response.text = mock_html
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        provider._http_client = mock_client
        
        # Test
        servers = await provider._fetch_servers_from_web()
        
        assert len(servers) == 2
        assert any('filesystem' in s['id'] for s in servers)
        assert any('postgres' in s['id'] for s in servers)


class TestInstallationHelper:
    """Tests for InstallationHelper."""
    
    def test_detect_installation_type(self):
        """Test installation type detection."""
        from app.mcp.registry.installation_helper import InstallationHelper
        
        assert InstallationHelper.detect_installation_type({'type': 'npm'}) == InstallationType.NPM
        assert InstallationHelper.detect_installation_type({'type': 'pypi'}) == InstallationType.PYPI
        assert InstallationHelper.detect_installation_type({'type': 'docker'}) == InstallationType.DOCKER
        assert InstallationHelper.detect_installation_type({'type': 'remote'}) == InstallationType.REMOTE
        assert InstallationHelper.detect_installation_type({'type': 'unknown'}) == InstallationType.UNKNOWN
    
    def test_check_prerequisites(self):
        """Test prerequisite checking."""
        from app.mcp.registry.installation_helper import InstallationHelper
        
        # This will depend on what's installed on the test system
        # We can only test the logic, not actual installations
        has_prereq, msg = InstallationHelper.check_prerequisites(InstallationType.REMOTE)
        assert has_prereq == True  # Remote needs no prerequisites
        
        has_prereq, msg = InstallationHelper.check_prerequisites(InstallationType.UNKNOWN)
        assert has_prereq == True  # Unknown types pass through


@pytest.mark.integration
class TestRealAPIIntegration:
    """Integration tests with real APIs (marked to skip by default)."""
    
    @pytest.mark.asyncio
    async def test_fetch_official_registry_real(self):
        """Test fetching from real official registry."""
        provider = OfficialMCPRegistryProvider()
        
        result = await provider.list_services(max_results=5)
        
        assert 'services' in result
        assert len(result['services']) > 0
        
        # Check first service has required fields
        service = result['services'][0]
        assert service.service_id
        assert service.service_name
        assert service.service_description
        assert service.installation_instructions
        
        await provider.close()
    
    @pytest.mark.asyncio
    async def test_search_official_registry_real(self):
        """Test searching real official registry."""
        provider = OfficialMCPRegistryProvider()
        
        results = await provider.search_tools("filesystem", max_results=5)
        
        assert len(results) > 0
        assert any('file' in r.service.service_name.lower() for r in results)
        
        await provider.close()
    
    @pytest.mark.asyncio
    async def test_fetch_awesome_list_real(self):
        """Test fetching from real awesome list."""
        provider = AwesomeListRegistryProvider(lists=["punkpeye/awesome-mcp-servers"])
        
        servers = await provider._fetch_awesome_list("punkpeye/awesome-mcp-servers")
        
        assert len(servers) > 50  # Should have many servers
        
        # Check structure
        server = servers[0]
        assert 'name' in server
        assert 'description' in server or 'url' in server
        
        await provider.close()

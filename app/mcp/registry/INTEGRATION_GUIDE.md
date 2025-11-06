# MCP Registry Integration Guide

## Overview

The MCP Registry system integrates **5 major registry sources** into a unified interface:

| Provider | Type | Servers | Priority | Status |
|----------|------|---------|----------|--------|
| **Official MCP** | REST API | ~500+ | Highest | ✅ Implemented |
| **PulseMCP** | REST API (mirrors official) | 6360+ | High | ✅ Implemented |
| **Smithery** | Web Scraping | ~200+ | Medium | ✅ Implemented |
| **Awesome Lists** | Markdown Parsing | ~500+ | Lower | ✅ Implemented |
| **Amazon Internal** | Boto3 API | Internal only | Highest (Amazon) | ✅ Implemented |

## Quick Start

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# For development/testing
pip install -r requirements-test.txt
```

### Basic Usage

```python
from app.mcp.registry_manager import get_registry_manager

# Get the manager
manager = get_registry_manager()

# List all available servers (deduplicated across all registries)
services = await manager.get_available_services(max_results=50)

for service in services:
    print(f"{service.service_name}: {service.service_description}")
    print(f"  Available in: {service.provider_metadata.get('available_in', [])}")
    print(f"  Install: {service.installation_type.value}")
    print()

# Search for specific functionality
results = await manager.search_services_by_tools("database")

for result in results:
    service = result.service
    tools = result.matching_tools
    print(f"{service.service_name} - {len(tools)} matching tools")

# Install a server
result = await manager.install_service("io.github.user/awesome-server")
if result['status'] == 'success':
    print(f"Installed: {result['server_name']}")
else:
    print(f"Error: {result['error']}")
```

## Architecture

### Layer 1: Providers (Data Sources)

Each provider implements the `RegistryProvider` interface:

```python
class RegistryProvider(ABC):
    @abstractmethod
    async def list_services(...) -> Dict[str, Any]
    
    @abstractmethod
    async def get_service_detail(service_id: str) -> RegistryServiceInfo
    
    @abstractmethod
    async def search_tools(query: str) -> List[ToolSearchResult]
    
    @abstractmethod
    async def install_service(...) -> InstallationResult
    
    @abstractmethod
    async def validate_service(service_id: str) -> bool
```

### Layer 2: Aggregator (Deduplication & Merging)

The `RegistryAggregator` combines results from all providers:

**Deduplication Algorithm:**
```
1. Compute fingerprint for each service:
   - repo:owner/repo (if has repository URL)
   - npm:package-name (if NPM package)
   - pypi:package-name (if PyPI package)
   - name:service-name (fallback)

2. Group services by fingerprint

3. Merge grouped services:
   - Use highest-priority provider as base
   - Combine tags from all sources
   - Use best (longest) description
   - Track all providers in metadata
```

**Provider Priority:**
```
Amazon Internal: 0 (highest for Amazon users)
Official MCP:    1
PulseMCP:        2
Smithery:        3
Awesome Lists:   4
GitHub:          5 (deprecated)
```

### Layer 3: Integration Manager (User-Facing API)

The `RegistryIntegrationManager` provides the main interface:

- Service discovery
- Search functionality
- Installation orchestration
- Configuration management
- MCP manager integration

## Provider Details

### Official MCP Registry

**API Endpoint:** `https://registry.modelcontextprotocol.io/v0/servers`

**Response Format:**
```json
{
  "servers": [
    {
      "server": {
        "$schema": "https://...",
        "name": "io.github.user/server",
        "description": "Server description",
        "version": "1.0.0",
        "repository": {
          "url": "https://github.com/user/server",
          "source": "github"
        },
        "packages": [
          {
            "registryType": "npm",
            "identifier": "package-name",
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
          "isLatest": true
        }
      }
    }
  ],
  "metadata": {
    "nextCursor": "cursor-value",
    "count": 30
  }
}
```

**Query Parameters:**
- `cursor`: Pagination cursor
- `limit`: Results per page (max 100)
- `search`: Filter by name substring
- `version`: Filter by version (use `latest` for latest only)
- `updated_since`: RFC3339 timestamp for incremental sync

### PulseMCP

Currently mirrors the official registry API. PulseMCP maintains entries for popular servers that haven't been published to the official registry yet.

**Key Difference:** May have more servers than official registry during transition period.

### Smithery

**No public API** - uses web scraping from https://smithery.ai/servers

**Installation:** Smithery has a CLI
```bash
npm install -g @smithery/cli
smithery install <server-name>
```

### Awesome Lists

**Format:** Markdown files on GitHub

**Parsing Strategy:**
1. Fetch raw README.md from multiple repos
2. Parse markdown list items with regex
3. Extract metadata from emoji icons:
   - 🐍 = Python
   - 📇 = TypeScript/JavaScript
   - 🏎️ = Go
   - 🦀 = Rust
   - ☁️ = Cloud service
   - 🏠 = Local service
   - 🎖️ = Official

**Sources:**
- punkpeye/awesome-mcp-servers (original)
- wong2/awesome-mcp-servers (popular fork)
- appcypher/awesome-mcp-servers (curated)

### Amazon Internal

**Service:** AWS MCP Registry Service (via boto3)

**Authentication:** Midway credentials (auto-detected)

**API Operations:**
- `ListServices`
- `GetServiceDetail`
- `SearchTools`
- `BatchGetSummary`

**Requirements:**
- AWS credentials with MCP Registry access
- Midway authentication for internal users
- VPN connection if outside corporate network

## Testing

### Run Unit Tests

```bash
# Run all unit tests (excludes integration tests)
pytest tests/test_registry_*.py

# Run with verbose output
pytest tests/test_registry_*.py -v

# Run with coverage
pytest tests/test_registry_*.py --cov=app/mcp/registry --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Run Integration Tests

Integration tests require network access and will hit real APIs:

```bash
# Run integration tests
pytest tests/test_registry_*.py -m integration

# Run all tests including integration
pytest tests/test_registry_*.py -m ""
```

### Test Coverage

Target: **90%+ coverage** for all registry code

Current test files:
- `tests/test_registry_providers.py` - Provider implementations
- `tests/test_registry_aggregator.py` - Aggregation logic
- `tests/conftest.py` - Shared fixtures

## Adding a New Registry

### Step 1: Create Provider Class

```python
# app/mcp/registry/providers/my_registry.py

from app.mcp.registry.interface import RegistryProvider, ...

class MyRegistryProvider(RegistryProvider):
    def __init__(self, base_url: str = "https://api.myregistry.com"):
        self.base_url = base_url
        self._http_client = None
    
    @property
    def name(self) -> str:
        return "My Registry"
    
    @property
    def identifier(self) -> str:
        return "my-registry"
    
    @property
    def is_internal(self) -> bool:
        return False
    
    @property
    def supports_search(self) -> bool:
        return True
    
    async def list_services(self, max_results=50, next_token=None, filter_params=None):
        # Fetch from your API
        # Parse into RegistryServiceInfo objects
        # Return {'services': [...], 'next_token': ...}
        pass
    
    # Implement other required methods...
```

### Step 2: Register Provider

```python
# app/mcp/registry/registry.py

from app.mcp.registry.providers.my_registry import MyRegistryProvider

def initialize_registry_providers():
    registry = get_provider_registry()
    
    # Add your provider
    registry.register_provider_class(
        "my-registry",
        MyRegistryProvider,
        is_default=True  # Include by default
    )
```

### Step 3: Add Tests

```python
# tests/test_my_registry.py

class TestMyRegistryProvider:
    @pytest.fixture
    def provider(self):
        return MyRegistryProvider()
    
    @pytest.mark.asyncio
    async def test_list_services(self, provider):
        result = await provider.list_services(max_results=10)
        assert 'services' in result
        assert len(result['services']) <= 10
```

That's it! The aggregator automatically includes your new provider.

## Troubleshooting

### Provider Not Loading

Check logs for initialization errors:
```python
from app.mcp.registry.registry import get_provider_registry

registry = get_provider_registry()
providers = registry.get_available_providers()
print(f"Loaded providers: {[p.identifier for p in providers]}")
```

### Services Not Deduplicating

Check fingerprints:
```python
from app.mcp.registry.aggregator import get_registry_aggregator

aggregator = get_registry_aggregator()
services = await aggregator.get_all_services(max_results=100)

for service in services:
    fp = aggregator._compute_service_fingerprint(service)
    print(f"{service.service_name}: {fp}")
```

### Installation Failing

Check prerequisites:
```python
from app.mcp.registry.installation_helper import InstallationHelper
from app.mcp.registry.interface import InstallationType

for install_type in [InstallationType.NPM, InstallationType.PYPI, InstallationType.DOCKER]:
    has_prereq, msg = InstallationHelper.check_prerequisites(install_type)
    print(f"{install_type.value}: {'✓' if has_prereq else '✗'} {msg}")
```

## Performance Considerations

- **Caching**: All providers cache results (1-2 hour TTL)
- **Parallel Fetching**: Aggregator fetches from all providers in parallel
- **Lazy Initialization**: Providers only initialized when first used
- **Incremental Sync**: Official registry supports `updated_since` parameter

## Security Notes

- All HTTP clients use explicit timeouts
- Credentials handled via environment variables
- Amazon internal requires proper AWS authentication
- Remote servers may require API keys/tokens
- Installation commands are validated before execution

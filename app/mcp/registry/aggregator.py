"""
Registry Aggregator for combining multiple registry sources.
Provides unified search, deduplication, and ranking across all registries.
"""

import asyncio
import re
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict
from datetime import datetime
from datetime import timezone

from app.mcp.registry.interface import (
    RegistryServiceInfo, ToolSearchResult, InstallationType
)
from app.mcp.registry.registry import get_provider_registry
from app.utils.logging_utils import logger


class RegistryAggregator:
    """Aggregates and deduplicates MCP servers from multiple registry sources."""
    
    def __init__(self):
        """Initialize the aggregator."""
        self.provider_registry = get_provider_registry()
        self._unified_cache: Dict[str, RegistryServiceInfo] = {}
        self._last_refresh: Optional[datetime] = None
    
    def _compute_service_fingerprint(self, service: RegistryServiceInfo) -> str:
        """
        Compute a fingerprint for deduplication.
        Services with the same repository or package name are considered the same.
        """
        # Priority 1: Repository URL (most reliable)
        if service.repository_url:
            # Normalize GitHub URLs
            repo = service.repository_url.lower().strip('/')
            repo = repo.replace('https://github.com/', '').replace('.git', '')
            # Also normalize http:// and remove www.
            repo = repo.replace('http://github.com/', '').replace('www.github.com/', '')
            return f"repo:{repo}"
        
        # Priority 1.5: Try to extract GitHub repo from service name if it looks like github format
        if '/' in service.service_name and service.service_name.startswith(('io.github', 'github')):
            # Convert io.github.user/repo to user/repo format
            name_parts = service.service_name.replace('io.github.', '').replace('github.', '')
            if '/' in name_parts:
                return f"repo:{name_parts.lower()}"
        
        # Priority 2: Package identifier from installation instructions
        instructions = service.installation_instructions
        if service.installation_type == InstallationType.NPM:
            package = instructions.get('package', '')
            if package:
                return f"npm:{package}"
        elif service.installation_type == InstallationType.PYPI:
            package = instructions.get('package', '')
            if package:
                return f"pypi:{package}"
        elif service.installation_type == InstallationType.DOCKER:
            image = instructions.get('image', '')
            if image:
                return f"docker:{image}"
        
        # Priority 3: Normalize service name for better matching
        name = service.service_name.lower()
        
        # Remove common prefixes/suffixes that make names different
        name = re.sub(r'^(mcp[-_]?server[-_]?|server[-_]?)', '', name)
        name = re.sub(r'[-_]?(mcp[-_]?)?server$', '', name) 
        name = re.sub(r'^(io\.github\.|github\.)', '', name)
        
        # Normalize separators
        name = re.sub(r'[-_\s]+', '-', name).strip('-')
        
        # For very generic names, include provider to avoid false matches
        generic_names = {'server', 'mcp', 'tool', 'client', 'api', 'test'}
        if name in generic_names:
            provider_id = service.provider_metadata.get('provider_id', 'unknown')
            return f"name:{name}:{provider_id}"
            
        return f"name:{name}"
    
    def _merge_services(self, services: List[RegistryServiceInfo]) -> RegistryServiceInfo:
        """
        Merge multiple service entries for the same server.
        Prioritizes: Official > PulseMCP > Smithery > Awesome Lists
        """
        if len(services) == 1:
            return services[0]
        
        # Provider priority for merging
        provider_priority = {
            'official-mcp': 0,  # Highest priority
            'pulsemcp': 1,
            'smithery': 2,
            'awesome-lists': 3,
            'github': 4,
        }
        
        # Sort by priority
        services.sort(key=lambda s: provider_priority.get(
            s.provider_metadata.get('provider_id', ''), 999
        ))
        
        # Use highest priority service as base
        primary = services[0]
        
        # Merge metadata from other sources
        all_tags = set(primary.tags)
        primary_provider = primary.provider_metadata.get('provider_id')
        all_providers = [primary_provider] if primary_provider else []
        
        for service in services[1:]:
            # Collect tags
            all_tags.update(service.tags)
            
            # Track which registries have this server
            provider_id = service.provider_metadata.get('provider_id')
            if provider_id:
                all_providers.append(provider_id)
            
            # Use better description if primary is lacking
            if len(service.service_description) > len(primary.service_description):
                primary.service_description = service.service_description
            
            # Prefer more recent update time
            try:
                # Make both datetimes timezone-aware for comparison
                service_time = service.last_updated_at
                primary_time = primary.last_updated_at
                
                if service_time.tzinfo is None:
                    service_time = service_time.replace(tzinfo=timezone.utc)
                if primary_time.tzinfo is None:
                    primary_time = primary_time.replace(tzinfo=timezone.utc)
                    
                if service_time > primary_time:
                    primary.last_updated_at = service.last_updated_at
            except (AttributeError, TypeError) as e:
                # If comparison fails, just keep the primary timestamp
                logger.debug(f"Could not compare timestamps for {primary.service_name}: {e}")
        
        # Update merged metadata
        primary.tags = list(all_tags)
        primary.provider_metadata['available_in'] = all_providers
        primary.provider_metadata['merged_from'] = len(services)
        
        logger.debug(f"Merged {len(services)} entries for {primary.service_name} from {all_providers}")
        
        return primary
    
    async def get_all_services(
        self,
        max_results: int = 100,
        include_internal: bool = True,
        force_refresh: bool = False
    ) -> List[RegistryServiceInfo]:
        """
        Get unified list of all services across all registries with deduplication.
        
        Args:
            max_results: Maximum number of results to return
            include_internal: Whether to include internal registries
            force_refresh: Force refresh even if cache is valid
        """
        # Check cache (5 minute TTL)
        from datetime import timedelta
        if (not force_refresh and 
            self._last_refresh and 
            datetime.now() - self._last_refresh < timedelta(minutes=5) and
            self._unified_cache):
            logger.info(f"Using cached unified registry data ({len(self._unified_cache)} services)")
            return list(self._unified_cache.values())[:max_results]
        
        logger.info("Refreshing unified registry data from all sources...")
        
        # Get all providers
        providers = self.provider_registry.get_available_providers(include_internal)
        
        # Fetch from all providers in parallel
        tasks = []
        for provider in providers:
            tasks.append(self._fetch_from_provider(provider, max_results * 2))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect all services
        all_services: Dict[str, List[RegistryServiceInfo]] = defaultdict(list)
        
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Provider fetch failed: {result}")
                continue
            
            for service in result:
                fingerprint = self._compute_service_fingerprint(service)
                all_services[fingerprint].append(service)
        
        # Merge duplicates
        unified: Dict[str, RegistryServiceInfo] = {}
        for fingerprint, services in all_services.items():
            merged = self._merge_services(services)
            unified[fingerprint] = merged
        
        # Update cache
        self._unified_cache = unified
        self._last_refresh = datetime.now()
        
        logger.info(f"Unified registry: {len(unified)} unique services from {len(providers)} providers")
        
        # Sort by relevance (support level, download count, etc.)
        sorted_services = sorted(
            unified.values(),
            key=lambda s: (
                s.support_level.value,  # Higher support level first
                s.download_count or 0,  # More downloads first
                s.star_count or 0,      # More stars first
                -len(s.tags)            # More tags last (less is more specific)
            ),
            reverse=True
        )
        
        return sorted_services[:max_results]
    
    async def _fetch_from_provider(
        self,
        provider,
        max_results: int
    ) -> List[RegistryServiceInfo]:
        """Fetch services from a single provider with pagination support."""
        try:
            logger.info(f"Fetching from provider: {provider.identifier}")
            
            all_services = []
            next_token = None
            page = 1
            
            while len(all_services) < max_results:
                # Calculate how many more services we need
                remaining = max_results - len(all_services)
                page_size = min(remaining, 500)  # Max 500 per page
                
                result = await provider.list_services(
                    max_results=page_size,
                    next_token=next_token
                )
                
                services = result.get('services', [])
                next_token = result.get('next_token')
                
                all_services.extend(services)
                
                if not next_token or not services:
                    break  # No more pages
                
                page += 1
                if page > 10:  # Safety limit
                    logger.warning(f"Reached page limit for {provider.identifier}")
                    break
            
            logger.info(f"Fetched {len(all_services)} services from {provider.identifier}")
            return all_services
            
        except Exception as e:
            logger.error(f"Error fetching from provider {provider.identifier}: {e}")
            logger.exception(e)  # Full traceback for debugging
            return []
    
    async def search_unified(
        self,
        query: str,
        max_results: int = 20,
        include_internal: bool = True
    ) -> List[ToolSearchResult]:
        """
        Search across all registries with unified results.
        """
        providers = self.provider_registry.get_available_providers(include_internal)
        
        # Search all providers in parallel
        tasks = []
        for provider in providers:
            if provider.supports_search:
                logger.info(f"Adding search task for provider: {provider.identifier}")
                tasks.append(provider.search_tools(query, max_results))
            else:
                logger.info(f"Provider {provider.identifier} does not support search")
        
        if not tasks:
            logger.warning("No providers support search")
            return []
        
        logger.info(f"Executing {len(tasks)} search tasks for query: '{query}'")
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect and deduplicate results
        unified_results: Dict[str, ToolSearchResult] = {}
        
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Search task failed: {result}")
                continue
            
            if not result:
                continue
            
            for tool_result in result:
                # Debug unknown services
                if 'unknown' in tool_result.service.service_id.lower() or 'unknown' in tool_result.service.service_name.lower():
                    logger.warning(f"Found unknown service: ID={tool_result.service.service_id}, Name={tool_result.service.service_name}, Provider={tool_result.service.provider_metadata}")
                
                fingerprint = self._compute_service_fingerprint(tool_result.service)
                
                # Keep highest relevance score for each unique service
                if fingerprint not in unified_results or \
                   (tool_result.relevance_score or 0) > (unified_results[fingerprint].relevance_score or 0):
                    unified_results[fingerprint] = tool_result
        
        # Sort by relevance
        sorted_results = sorted(
            unified_results.values(),
            key=lambda r: r.relevance_score or 0,
            reverse=True
        )
        
        return sorted_results[:max_results]


# Global aggregator instance
_aggregator: Optional[RegistryAggregator] = None

def get_registry_aggregator() -> RegistryAggregator:
    """Get the global registry aggregator."""
    global _aggregator
    if _aggregator is None:
        _aggregator = RegistryAggregator()
    return _aggregator

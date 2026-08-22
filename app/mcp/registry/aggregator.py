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
        # True when the last refresh had a provider fail. A partial result must
        # not be cached for the full TTL, or one expired credential pins an
        # incomplete catalogue in place for five minutes.
        self._last_fetch_partial: bool = False
        # Memo for the search fan-out, keyed on (query, resolved provider set).
        self._search_cache: Dict[str, tuple] = {}
        self._search_cache_max = 64
        # A search fan-out is memoised per (query, provider set). Without this
        # the debounced search box re-ran the whole fan-out on every keystroke,
        # and a provider whose search_tools re-fetches its entire catalogue
        # (official-mcp, github, and the CLI-backed internal registry, which
        # spawns a ~20s subprocess) paid that cost once per character typed.
        self._search_cache: Dict[str, tuple] = {}
        self._search_cache_max = 64
    
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
        from datetime import timedelta
        # Never cache a partial refresh: callers cannot tell that providers
        # were omitted, and an immediate retry may recover a transient failure.
        if (not force_refresh and 
            not self._last_fetch_partial and
            self._last_refresh and 
            datetime.now() - self._last_refresh < timedelta(minutes=5) and
            self._unified_cache):
            logger.info(f"Using cached unified registry data ({len(self._unified_cache)} services)")
            return list(self._unified_cache.values())[:max_results]
        
        logger.info("Refreshing unified registry data from all sources...")
        
        # Get all providers
        providers = self.provider_registry.get_available_providers(include_internal)
        
        # Fetch from all providers in parallel. A useful global ranking needs
        # more than two candidates when max_results is small; 50 is the
        # provider interface's standard page size.
        tasks = []
        for provider in providers:
            fetch_limit = max(50, max_results * 2)
            tasks.append(self._fetch_from_provider(provider, fetch_limit))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect all services
        all_services: Dict[str, List[RegistryServiceInfo]] = defaultdict(list)
        
        had_failure = False
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Provider fetch failed: {result}")
                had_failure = True
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
        self._last_fetch_partial = had_failure
        
        logger.info(f"Unified registry: {len(unified)} unique services from {len(providers)} providers")
        
        # Sort by relevance (support level, download count, etc.).
        #
        # The rank is NEGATED because the whole tuple is sorted reverse=True for
        # the count fields (more downloads/stars first). Sorting on
        # support_level.value here was sorting the DISPLAY STRING descending,
        # i.e. alphabetically, which ranked "Under assessment" above
        # "Recommended" -- so truncating to max_results discarded the
        # recommended servers first.
        sorted_services = sorted(
            unified.values(),
            key=lambda s: (
                -s.support_level.rank,  # Most trustworthy first
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
                # Do NOT clamp below what the caller asked for. A provider with
                # no pagination (the CLI-backed internal registry) returns its
                # own truncated slice and next_token=None, so the loop breaks
                # after one page -- turning a 500-item page hint into a hard
                # 500-item ceiling on a 5218-entry registry. Paginating
                # providers self-clamp to their own API max.
                page_size = remaining
                
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
            raise
    
        # Memoised per (query, resolved provider set). Without this the
        # debounced search box re-ran the entire fan-out on every keystroke,
        # and a provider whose search_tools re-fetches its whole catalogue
        # paid that cost once per character typed.
        from datetime import timedelta
        cache_key = "\x00".join([
            query.strip().lower(),
            str(max_results),
            ",".join(sorted(p.identifier for p in providers)),
        ])
        cached = self._search_cache.get(cache_key)
        if cached:
            cached_at, cached_results = cached
            if datetime.now() - cached_at < timedelta(minutes=5):
                logger.info(f"Using cached search for '{query}'")
                return cached_results

    async def search_unified(
        self,
        query: str,
        max_results: int = 20,
        include_internal: bool = True,
        provider_filter: Optional[List[str]] = None
    ) -> List[ToolSearchResult]:
        """
        Search across all registries with unified results.

        provider_filter narrows which providers are QUERIED, not merely which
        results survive. Filtering only afterwards still paid every provider's
        full search cost -- for the CLI-backed Amazon provider that is a ~20s
        subprocess per request even when one registry was wanted.
        """
        providers = self.provider_registry.get_available_providers(include_internal)
        
        if provider_filter:
            wanted = set(provider_filter)
            # A filter naming only unknown ids (e.g. the frontend's synthetic
            # "builtin") selects nothing, which matches the previous behaviour
            # of querying everything and then post-filtering it all away.
            providers = [p for p in providers if p.identifier in wanted]

        # Keyed on the RESOLVED provider set, not the requested filter: a
        # provider that has since become unavailable must not be able to serve
        # cached results still attributed to it.
        from datetime import timedelta
        cache_key = "\x00".join([
            query.strip().lower(),
            str(max_results),
            ",".join(sorted(p.identifier for p in providers)),
        ])
        cached = self._search_cache.get(cache_key)
        if cached:
            cached_at, cached_results = cached
            if datetime.now() - cached_at < timedelta(minutes=5):
                logger.info(
                    f"Using cached search for '{query}' "
                    f"({len(cached_results)} results)"
                )
                return cached_results

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
        
        final_results = sorted_results[:max_results]

        # Bounded: the debounced box produces one key per prefix typed, so an
        # unbounded dict would grow with keystrokes for the process lifetime.
        if len(self._search_cache) >= self._search_cache_max:
            self._search_cache.clear()
        self._search_cache[cache_key] = (datetime.now(), final_results)

        return final_results

    def invalidate_caches(self) -> None:
        """Drop both the unified listing and the search memo.

        A provider refresh re-fetches that provider's catalogue directly,
        bypassing the aggregator entirely, so without this a just-refreshed
        registry would keep answering searches from results computed before
        it changed -- a refresh that visibly does nothing.
        """
        self._unified_cache = {}
        self._last_refresh = None
        self._search_cache = {}


# Global aggregator instance
_aggregator: Optional[RegistryAggregator] = None

def get_registry_aggregator() -> RegistryAggregator:
    """Get the global registry aggregator."""
    global _aggregator
    if _aggregator is None:
        _aggregator = RegistryAggregator()
    return _aggregator

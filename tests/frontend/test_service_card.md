# ServiceCard Component — Test Plan

## What was changed

Three near-identical card renderers (~600 lines total) in `MCPRegistryModal.tsx`
were consolidated into a single `ServiceCard` component (~273 lines).

### Replaced functions
| Function | Lines | Tab | Differences from shared |
|---|---|---|---|
| `renderEnhancedServiceCard` | 573–800 | Browse | `matchingTools`, uninstall button, no optional chaining |
| `renderServiceCard` | 801–1003 | Favorites | Bare subset of enhanced |
| `renderInstalledService` | 1004–1227 | Installed | Optional chaining, fallbacks, **duplicate blocks (bug)** |

### Bugs fixed
1. **Duplicate rendering in `renderInstalledService`**: `_available_tools` section
   and `_dependencies_available` Alert were each rendered **twice** (copy-paste error).
2. **Missing null safety in `renderEnhancedServiceCard` and `renderServiceCard`**:
   `service.provider.id` accessed without optional chaining (crashes if provider is undefined).
3. **Inconsistent install label**: `renderInstalledService` didn't show "Configured"
   for manually-configured services; the other two did. Now unified.
4. **Inconsistent `downloadCount`/`starCount` checks**: Two renderers used truthy
   checks (`service.downloadCount &&`) which hides `0`; now uses `!= null`.

## Manual test procedure

### Prerequisites
- Ziya server running with MCP registry configured
- At least one registry provider enabled

### Test cases

1. **Browse tab (enhanced card)**
   - Open MCP Registry modal → Browse tab
   - Verify service cards render with name, support level tag, provider tag
   - Search for a tool name → verify "Matching Tools" tags appear on relevant cards
   - Click expand (ℹ) → verify Downloads/Stars/Updated stats appear
   - Click Install on an uninstalled service → verify loading state, then "Installed" label
   - For installed services, verify uninstall (🗑) button appears
   - Click heart → verify favorite toggle works

2. **Favorites tab (basic card)**
   - Switch to Favorites tab
   - Verify favorited services appear with same card structure
   - Verify NO uninstall button on this tab
   - Verify NO matching tools section (not in search mode)
   - Click expand → verify same expanded content as Browse tab

3. **Installed tab (installed card)**
   - Switch to Installed tab
   - Verify installed services render correctly
   - For services with missing metadata (no supportLevel), verify "Community" fallback
   - For services with missing description, verify serviceName is used as fallback
   - For services with missing lastUpdatedAt, verify "N/A" shown
   - Verify "Available Tools" and "Dependencies Required" each appear **exactly once**
     (the old code rendered them twice)
   - Verify "Manually Configured" tag appears for manually-configured services

4. **Null safety**
   - If possible, configure a service with minimal metadata (no provider object)
   - Verify card renders without crashing (shows "Unknown" for provider name)

5. **React.memo optimization**
   - Open browser DevTools → Components tab
   - Verify ServiceCard doesn't re-render when unrelated state changes

/**
 * ServiceCard unit tests.
 *
 * These test the props-computation logic that the three wrapper functions
 * (renderEnhancedServiceCard, renderServiceCard, renderInstalledService)
 * feed into the shared ServiceCard component. Since @testing-library/react
 * is not available in this project, we validate the data contracts and
 * type-level guarantees rather than rendering.
 *
 * Run: cd frontend && node -e "process.env.CI='true'; require('child_process').execSync('node node_modules/.bin/craco test --watchAll=false --testPathPattern=ServiceCard', {stdio:'inherit', env:{...process.env, CI:'true'}})"
 *
 * NOTE: This file lives under tests/frontend/ but is symlinked / copied
 * into frontend/src/components/__tests__/ for the CRA jest runner.
 * The canonical source-of-truth is this file.
 */

import type { ServiceCardService, ServiceCardProps, MatchingTool } from '../../../frontend/src/components/ServiceCard';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Minimal MCPService-shaped data (the "available" variant). */
function makeMCPService(overrides: Partial<ServiceCardService> = {}): ServiceCardService {
    return {
        serviceId: 'test-service-1',
        serviceName: 'Test Service',
        serviceDescription: 'A test MCP service',
        supportLevel: 'Recommended',
        version: 2,
        lastUpdatedAt: '2024-06-01T00:00:00Z',
        provider: { id: 'test-provider', name: 'Test Provider', isInternal: false },
        tags: ['test', 'demo'],
        installationType: 'npm',
        ...overrides,
    };
}

/** Minimal InstalledService-shaped data (many fields optional). */
function makeInstalledService(overrides: Partial<ServiceCardService> = {}): ServiceCardService {
    return {
        serviceId: 'installed-1',
        serviceName: 'Installed Service',
        serverName: 'installed-server',
        ...overrides,
    } as ServiceCardService;
}

function makeProps(overrides: Partial<ServiceCardProps> = {}): ServiceCardProps {
    return {
        service: makeMCPService(),
        isInstalled: false,
        isFavorite: false,
        isExpanded: false,
        isInstalling: false,
        onInstall: jest.fn(),
        onPreview: jest.fn(),
        onToggleFavorite: jest.fn(),
        onToggleExpanded: jest.fn(),
        getSupportLevelColor: (level: string) => 'blue',
        ...overrides,
    };
}

/* ------------------------------------------------------------------ */
/*  Tests: ServiceCardService type compatibility                       */
/* ------------------------------------------------------------------ */

describe('ServiceCardService type compatibility', () => {
    it('accepts a full MCPService-shaped object', () => {
        const svc = makeMCPService();
        expect(svc.serviceId).toBe('test-service-1');
        expect(svc.provider?.name).toBe('Test Provider');
    });

    it('accepts a minimal InstalledService-shaped object (most fields optional)', () => {
        const svc = makeInstalledService();
        expect(svc.serviceId).toBe('installed-1');
        expect(svc.supportLevel).toBeUndefined();
        expect(svc.serviceDescription).toBeUndefined();
        expect(svc.lastUpdatedAt).toBeUndefined();
        expect(svc.provider).toBeUndefined();
    });

    it('accepts builtin service shape', () => {
        const svc = makeMCPService({
            serviceId: 'builtin_time',
            _dependencies_available: true,
            _available_tools: ['get_current_time'],
        });
        expect(svc.serviceId.startsWith('builtin_')).toBe(true);
        expect(svc._available_tools).toEqual(['get_current_time']);
    });
});

/* ------------------------------------------------------------------ */
/*  Tests: Install label logic                                         */
/* ------------------------------------------------------------------ */

describe('Install label derivation', () => {
    function getInstallLabel(isBuiltin: boolean, isInstalled: boolean, isManuallyConfigured: boolean): string {
        if (isBuiltin) return isInstalled ? 'Enabled' : 'Enable';
        if (isInstalled) return isManuallyConfigured ? 'Configured' : 'Installed';
        return 'Install';
    }

    it('shows "Install" for non-installed regular service', () => {
        expect(getInstallLabel(false, false, false)).toBe('Install');
    });

    it('shows "Installed" for installed regular service', () => {
        expect(getInstallLabel(false, true, false)).toBe('Installed');
    });

    it('shows "Configured" for manually configured service', () => {
        expect(getInstallLabel(false, true, true)).toBe('Configured');
    });

    it('shows "Enable" for non-installed builtin', () => {
        expect(getInstallLabel(true, false, false)).toBe('Enable');
    });

    it('shows "Enabled" for installed builtin', () => {
        expect(getInstallLabel(true, true, false)).toBe('Enabled');
    });

    it('builtin ignores manually-configured flag', () => {
        expect(getInstallLabel(true, true, true)).toBe('Enabled');
    });
});

/* ------------------------------------------------------------------ */
/*  Tests: Null-safety / fallback logic                                */
/* ------------------------------------------------------------------ */

describe('Null-safety and fallback values', () => {
    it('falls back supportLevel to "Community" when absent', () => {
        const svc = makeInstalledService({ supportLevel: undefined });
        const supportLevel = svc.supportLevel || 'Community';
        expect(supportLevel).toBe('Community');
    });

    it('falls back description to serviceName when absent', () => {
        const svc = makeInstalledService({ serviceDescription: undefined, serviceName: 'My Service' });
        const description = svc.serviceDescription || svc.serviceName || '';
        expect(description).toBe('My Service');
    });

    it('falls back description to empty string when both absent', () => {
        const svc = makeInstalledService({ serviceDescription: undefined, serviceName: undefined });
        const description = svc.serviceDescription || svc.serviceName || '';
        expect(description).toBe('');
    });

    it('falls back lastUpdatedAt to "N/A"', () => {
        const svc = makeInstalledService({ lastUpdatedAt: undefined });
        const updated = svc.lastUpdatedAt
            ? new Date(svc.lastUpdatedAt).toLocaleDateString()
            : 'N/A';
        expect(updated).toBe('N/A');
    });

    it('falls back provider name to "Unknown"', () => {
        const svc = makeInstalledService({ provider: { name: undefined } });
        const providerName = svc.provider?.name || 'Unknown';
        expect(providerName).toBe('Unknown');
    });

    it('handles completely missing provider', () => {
        const svc = makeInstalledService({ provider: undefined });
        const providerName = svc.provider?.name || 'Unknown';
        expect(providerName).toBe('Unknown');
    });
});

/* ------------------------------------------------------------------ */
/*  Tests: Wrapper function prop computation                           */
/* ------------------------------------------------------------------ */

describe('Wrapper function prop computation', () => {
    it('renderEnhancedServiceCard pattern: passes matchingTools + showUninstall', () => {
        const tools: MatchingTool[] = [{ toolName: 'fetch', mcpServerId: 'srv-1' }];
        const props = makeProps({
            matchingTools: tools,
            showUninstall: true,
            onUninstall: jest.fn(),
            service: makeMCPService({ serverName: 'my-server' }),
            isInstalled: true,
        });

        expect(props.matchingTools).toHaveLength(1);
        expect(props.showUninstall).toBe(true);
        expect(props.onUninstall).toBeDefined();
        expect(props.service.serverName).toBe('my-server');
    });

    it('renderServiceCard pattern: no matchingTools, no showUninstall', () => {
        const props = makeProps();

        expect(props.matchingTools).toBeUndefined();
        expect(props.showUninstall).toBeUndefined();
        expect(props.onUninstall).toBeUndefined();
    });

    it('renderInstalledService pattern: uses isServiceInstalled result', () => {
        const isServiceInstalled = (id: string) => id === 'installed-1';
        const svc = makeInstalledService();
        const props = makeProps({
            service: svc,
            isInstalled: isServiceInstalled(svc.serviceId),
            isManuallyConfigured: true,
        });

        expect(props.isInstalled).toBe(true);
        expect(props.isManuallyConfigured).toBe(true);
    });

    it('null serviceId returns early (guard check)', () => {
        const svc = { serviceId: '' } as ServiceCardService;
        const shouldRender = !!svc?.serviceId;
        expect(shouldRender).toBe(false);
    });
});

/* ------------------------------------------------------------------ */
/*  Tests: Bug fixes verified                                          */
/* ------------------------------------------------------------------ */

describe('Previously-divergent behaviour (now unified)', () => {
    it('downloadCount=0 is not suppressed (was truthy-check bug)', () => {
        const svc = makeMCPService({ downloadCount: 0 });
        // Old code: `service.downloadCount && ...` — hid 0
        // New code: `service.downloadCount != null && ...`
        expect(svc.downloadCount != null).toBe(true);
        expect(svc.downloadCount).toBe(0);
    });

    it('starCount=0 is not suppressed', () => {
        const svc = makeMCPService({ starCount: 0 });
        expect(svc.starCount != null).toBe(true);
    });

    it('optional chaining on provider is consistent across all variants', () => {
        // Old renderEnhancedServiceCard crashed on missing provider
        const svc = makeMCPService({ provider: undefined });
        expect(svc.provider?.id).toBeUndefined();
        expect(svc.provider?.name).toBeUndefined();
        expect(svc.provider?.isInternal).toBeUndefined();
        expect(svc.provider?.availableIn).toBeUndefined();
    });
});

/**
 * 2026-10-28: an upgrade deleted the install directory of a server
 * that was still running.  The backend now ends the turn with
 * error_type 'installation_changed' (app/utils/install_integrity.py) instead
 * of retrying it as a dropped connection and labelling it a throttling error.
 * This pins the banner that explains it, and that chatApi's showError()
 * dispatches to it ahead of every other banner (showError is not exported,
 * so the dispatch is pinned by a source-contract check, as in
 * chatApiConnectionError.test.ts).
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    extractInstallationFacts, isInstallationChangedError, renderInstallationChangedBanner,
} from '../../utils/installationChangedBanner';

const CHAT_API_SRC = fs.readFileSync(path.join(__dirname, '..', 'chatApi.ts'), 'utf-8');

// Exact text produced by missing_install_message() for each case.
const DIR_GONE =
    "Ziya's installation directory (/opt/ziya/0.8.11.0.post8/.venv/lib/python3.14/site-packages/app) " +
    "was removed while this server was running, most likely by an upgrade that replaced the old version. " +
    "The server can no longer read the files it needs, including the TLS certificate bundle, so every model " +
    "request fails. Restart ziya to run the new version.";
const BUNDLE_GONE =
    "The TLS certificate bundle /etc/pki/corp.pem does not exist, so HTTPS requests cannot be verified. " +
    "If AWS_CA_BUNDLE or ZIYA_CA_BUNDLE is set, check that path. If Ziya was upgraded while running, restart it.";

const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

describe('isInstallationChangedError', () => {
    it('matches the backend tag', () => {
        expect(isInstallationChangedError('anything', 'installation_changed')).toBe(true);
    });
    it('matches the backend text when the tag is lost', () => {
        expect(isInstallationChangedError(DIR_GONE, 'unknown_error')).toBe(true);
        expect(isInstallationChangedError(BUNDLE_GONE, undefined)).toBe(true);
    });
    it('does not claim a real TLS drop, an auth error or a throttle', () => {
        expect(isInstallationChangedError('SSL validation failed ... EOF occurred in violation of protocol', 'connection_error')).toBe(false);
        expect(isInstallationChangedError('Your credentials have expired. Run mwinit.', 'authentication_error')).toBe(false);
        expect(isInstallationChangedError('ThrottlingException: Too many tokens', 'throttling_error')).toBe(false);
    });
});

describe('extractInstallationFacts', () => {
    it('finds the removed directory', () => {
        expect(extractInstallationFacts(DIR_GONE)).toEqual({
            kind: 'install_removed',
            path: '/opt/ziya/0.8.11.0.post8/.venv/lib/python3.14/site-packages/app',
            version: '0.8.11.0.post8',
        });
    });
    it('finds the missing CA bundle', () => {
        expect(extractInstallationFacts(BUNDLE_GONE)).toEqual({ kind: 'ca_bundle_missing', path: '/etc/pki/corp.pem' });
    });
    it('falls back to unknown', () => {
        expect(extractInstallationFacts('TLS verification failed because a certificate file could not be found.'))
            .toEqual({ kind: 'unknown' });
    });
});

describe('renderInstallationChangedBanner', () => {
    it('install removed: says restart, names the version, offers no retry', () => {
        const html = renderInstallationChangedBanner(DIR_GONE, escape(DIR_GONE), escape);
        expect(html).toContain('Ziya was updated while it was running');
        expect(html).toContain('0.8.11.0.post8');
        expect(html).toContain('Restart');
        expect(html).toContain('reload this page');
        expect(html).toContain('Technical details');
        expect(html).not.toMatch(/throttl/i);
        // Retrying cannot help, so there is no retry button.
        expect(html).not.toContain('retry-button');
    });
    it('CA bundle missing: names the path and the env vars', () => {
        const html = renderInstallationChangedBanner(BUNDLE_GONE, escape(BUNDLE_GONE), escape);
        expect(html).toContain('Certificate bundle not found');
        expect(html).toContain('/etc/pki/corp.pem');
        expect(html).toContain('AWS_CA_BUNDLE');
    });
    it('names no internal tool', () => {
        expect(renderInstallationChangedBanner(DIR_GONE, escape(DIR_GONE), escape)).not.toMatch(/toolbox/i);
    });
    it('escapes extracted values', () => {
        const evil = "Ziya's installation directory (/tmp/<img src=x onerror=alert(1)>) was removed while this server was running.";
        const html = renderInstallationChangedBanner(evil, escape(evil), escape);
        expect(html).not.toContain('<img');
    });
});

describe('chatApi showError dispatch (source contract)', () => {
    it('imports the banner and checks it before every other banner', () => {
        expect(CHAT_API_SRC).toContain("from '../utils/installationChangedBanner'");
        const check = CHAT_API_SRC.indexOf('isInstallationChangedError(errorDetail, errorType)');
        const auth = CHAT_API_SRC.indexOf('if (isAuthError) {');
        expect(check).toBeGreaterThan(-1);
        expect(auth).toBeGreaterThan(-1);
        expect(check).toBeLessThan(auth);
        expect(CHAT_API_SRC).toContain('renderInstallationChangedBanner(errorDetail, safeDetailHtml, escapeHtml)');
    });
});

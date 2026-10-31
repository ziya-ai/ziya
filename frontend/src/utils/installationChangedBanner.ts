/**
 * Banner for a server that can no longer read its own files: typically
 * an upgrade deleted the version directory the running server was
 * started from, so the TLS certificate bundle inside it vanished and every
 * model call fails with "SSL validation failed ... No such file or directory".
 * Before app/utils/install_integrity.py this was retried and shown as a
 * throttling error.  Retrying cannot help; only a restart can, so this
 * banner deliberately has no Retry button.
 */

/** Backend tag from app/streaming_tool_executor._classify_and_handle_error,
 *  plus the message text in case the tag is lost on the way. */
export function isInstallationChangedError(errorDetail: string, errorType?: string): boolean {
    return errorType === 'installation_changed' ||
        /was removed while this server was running|TLS certificate bundle \S+ does not exist|certificate file could not be found/i.test(errorDetail);
}

export type InstallationFacts =
    | { kind: 'install_removed'; path: string; version?: string }
    | { kind: 'ca_bundle_missing'; path: string }
    | { kind: 'unknown' };

export function extractInstallationFacts(errorDetail: string): InstallationFacts {
    const dir = errorDetail.match(/installation directory \((.+?)\) was removed/i);
    if (dir) {
        // Launchers that keep one venv per version lay out
        // .../ziya/<version>/...; other layouts just omit the version.
        const ver = dir[1].match(/\/ziya[^/]*\/(\d+\.\d+[^/]*)\//);
        return ver
            ? { kind: 'install_removed', path: dir[1], version: ver[1] }
            : { kind: 'install_removed', path: dir[1] };
    }
    const bundle = errorDetail.match(/certificate bundle (\S+) does not exist/i);
    if (bundle) return { kind: 'ca_bundle_missing', path: bundle[1] };
    return { kind: 'unknown' };
}

const CODE = 'font-family: monospace; font-size: 13px; padding: 1px 5px; border-radius: 3px; background: rgba(127,127,127,0.15);';

/**
 * @param safeDetailHtml  the server text, already escaped and with newlines as <br>
 * @param escape          HTML escaper applied to values extracted from the text
 */
export function renderInstallationChangedBanner(
    errorDetail: string,
    safeDetailHtml: string,
    escape: (s: string) => string,
): string {
    const facts = extractInstallationFacts(errorDetail);
    let title: string;
    let body: string;
    if (facts.kind === 'ca_bundle_missing') {
        title = 'Certificate bundle not found';
        body = `<div style="font-size: 14px; line-height: 1.6; margin-bottom: 10px;">
The server verifies its HTTPS connection to the model with a certificate bundle, and the file it was told to use does not exist:
<div style="margin: 6px 0;"><code style="${CODE}">${escape(facts.path)}</code></div>
Without it no model request can be made, so retrying will not help.</div>
<ol style="font-size: 14px; line-height: 1.6; margin: 0 0 4px 18px; padding: 0;">
<li>If you set <code style="${CODE}">AWS_CA_BUNDLE</code>, <code style="${CODE}">ZIYA_CA_BUNDLE</code> or <code style="${CODE}">REQUESTS_CA_BUNDLE</code>, point it at a file that exists, or unset it.</li>
<li>If Ziya was upgraded while it was running, restart the server instead.</li>
<li>Then reload this page and resend your message.</li>
</ol>`;
    } else {
        title = facts.kind === 'install_removed' ? 'Ziya was updated while it was running' : 'Certificate file missing';
        const which = facts.kind === 'install_removed' && facts.version
            ? ` (version <strong>${escape(facts.version)}</strong>)`
            : '';
        body = `<div style="font-size: 14px; line-height: 1.6; margin-bottom: 10px;">
${facts.kind === 'install_removed'
    ? `Ziya was upgraded, and the upgrade deleted the old version's files. This server was started from that deleted copy${which}.`
    : `A certificate file the server needs could not be found. The usual cause is an upgrade that deleted the copy of Ziya this server was started from.`}
The program is still running from memory, but it can no longer open files on disk, including the certificate bundle it uses to verify its HTTPS connection to the model. So every model request fails, and retrying will not help until the server is restarted.
</div>
<ol style="font-size: 14px; line-height: 1.6; margin: 0 0 4px 18px; padding: 0;">
<li><strong>Stop the server:</strong> press <code style="${CODE}">Ctrl+C</code> in the terminal where <code style="${CODE}">ziya</code> is running.</li>
<li><strong>Restart it:</strong> run <code style="${CODE}">ziya</code> again. It starts the version that is now installed.</li>
<li><strong>Then reload this page</strong> and resend your last message.</li>
</ol>`;
    }
    return `<div class="installation-changed-banner" style="margin: 16px 0; padding: 16px 20px; background: var(--auth-error-bg, linear-gradient(135deg, #fffbe6 0%, #fff7f0 100%)); border: 1px solid var(--auth-error-border, #ffe58f); border-left: 4px solid var(--auth-error-accent, #faad14); border-radius: 6px; color: var(--auth-error-text, #434343);">
<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
<span style="font-size: 24px;">🔄</span>
<span style="font-size: 16px; font-weight: 600; color: var(--auth-error-heading, #ad6800);">${title}</span>
</div>
${body}
<div style="font-size: 13px; margin-top: 10px; color: var(--auth-error-muted, #8c8c8c);">Your conversations are saved on disk; restarting does not lose them.</div>
<details style="margin-top: 14px; border-top: 1px solid var(--auth-error-border, #ffe58f); padding-top: 10px;">
<summary style="cursor: pointer; font-size: 12px; color: var(--auth-error-muted, #8c8c8c);">Technical details</summary>
<div style="margin-top: 8px; white-space: pre-wrap; font-family: monospace; font-size: 12px; color: var(--auth-error-detail, #8c1f1f); max-height: 200px; overflow-y: auto;">
${safeDetailHtml}
</div>
</details>
</div>`;
}

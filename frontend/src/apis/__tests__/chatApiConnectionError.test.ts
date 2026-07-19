/**
 * Regression test: EndpointConnectionError / connection-refused failures
 * (e.g. a botocore "Could not connect to the endpoint URL" during an
 * AWS-side outage) previously fell through showError()'s isAuthError /
 * isContextSizeError checks in chatApi.ts and rendered as a generic
 * collapsed <details> block — raw boto3 text, no plain-language
 * explanation, no retry button.
 *
 * showError() is not exported (a large, non-modular file per this repo's
 * convention for chatApi.ts/vegaLitePlugin.ts-style modules), so — per the
 * existing convention (see vegaLiteSsrf.test.ts) — this is a source-contract
 * test asserting the fix is present at the sink, plus a pure reference
 * re-implementation of the isConnectionError predicate to prove the matching
 * logic itself is correct and non-tautological.
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT_API_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'chatApi.ts'),
  'utf-8',
);

describe('chatApi.ts connection-error banner (source contract)', () => {
  it('defines isConnectionError distinct from isAuthError/isContextSizeError', () => {
    expect(CHAT_API_SRC).toMatch(/const isConnectionError\s*=/);
  });

  it('checks error_type === "connection_error" as a backend signal', () => {
    expect(CHAT_API_SRC).toMatch(/errorType === 'connection_error'/);
  });

  it('falls back to matching raw EndpointConnectionError / connection-abort text', () => {
    expect(CHAT_API_SRC).toMatch(/errorDetail\.includes\('Could not connect to the endpoint'\)/);
    expect(CHAT_API_SRC).toMatch(/errorDetail\.includes\('EndpointConnectionError'\)/);
  });

  it('renders a distinct connection-error banner with a retry button', () => {
    expect(CHAT_API_SRC).toMatch(/if \(isConnectionError\)/);
    expect(CHAT_API_SRC).toMatch(/class="connection-error-banner"/);
    // Reuses the context-error-retry-button plumbing (already wired through
    // MarkdownRenderer.tsx / StreamedContent.tsx) rather than introducing a
    // fourth parallel button type.
    expect(CHAT_API_SRC).toMatch(/class="context-error-retry-button"/);
  });

  it('keeps the raw error detail under a collapsed "Technical details" section, not as the primary content', () => {
    // The connection-error banner block specifically, not the file globally
    // (auth/context-size banners also have their own collapsible sections).
    const connectionBannerStart = CHAT_API_SRC.indexOf('class="connection-error-banner"');
    expect(connectionBannerStart).toBeGreaterThan(-1);
    const bannerBlock = CHAT_API_SRC.slice(connectionBannerStart, connectionBannerStart + 2000);
    expect(bannerBlock).toMatch(/Technical details/);
    expect(bannerBlock).toMatch(/safeDetailHtml/);
  });
});

// ---------------------------------------------------------------------------
// Reference re-implementation of the isConnectionError predicate, kept
// byte-aligned with chatApi.ts's inline expression (not exported from the
// module). The self-test below proves this reference is non-tautological —
// it must both fire on the real-world payloads and stay silent on unrelated
// error text.
// ---------------------------------------------------------------------------

function isConnectionErrorRef(errorDetail: string, errorType?: string): boolean {
    return (
        errorType === 'connection_error' ||
        errorDetail.includes('Could not connect to the endpoint') ||
        errorDetail.includes('EndpointConnectionError') ||
        errorDetail.includes('Connection aborted') ||
        errorDetail.includes('Connection broken')
    );
}

describe('isConnectionError predicate (reference re-implementation)', () => {
    it('matches on the backend error_type signal', () => {
        expect(isConnectionErrorRef('some unrelated text', 'connection_error')).toBe(true);
    });

    it('matches the real-world botocore EndpointConnectionError text', () => {
        const raw = 'Could not connect to the endpoint URL: "https://bedrock-runtime.us-west-2.amazonaws.com/model/global.anthropic.claude-opus-4-8/invoke-with-response-stream"';
        expect(isConnectionErrorRef(raw)).toBe(true);
    });

    it('matches EndpointConnectionError by class name alone', () => {
        expect(isConnectionErrorRef('botocore.exceptions.EndpointConnectionError: could not connect')).toBe(true);
    });

    it('matches "Connection aborted." / "Connection broken" substrings', () => {
        expect(isConnectionErrorRef('Connection aborted.')).toBe(true);
        expect(isConnectionErrorRef('Connection broken: IncompleteRead')).toBe(true);
    });

    it('does NOT match unrelated error text (non-tautological negative control)', () => {
        expect(isConnectionErrorRef('ThrottlingException: Too many requests')).toBe(false);
        expect(isConnectionErrorRef('ExpiredToken: session expired')).toBe(false);
        expect(isConnectionErrorRef('ValueError: bad input')).toBe(false);
    });

    it('does NOT match a plain read-timeout (distinct bucket, distinct banner)', () => {
        // "Read timed out" alone should NOT trigger the connection-error banner —
        // that is the pre-existing throttling/timeout path, unaffected by this fix.
        expect(isConnectionErrorRef('Read timed out on stream')).toBe(false);
    });
});

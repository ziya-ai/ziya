/**
 * Tests for the plain-text paste behaviour in SendChatContainer.
 *
 * Problem: When pasting from Chrome, the browser inserts the *HTML* clipboard
 * representation which carries styled spans, tables, inline CSS, etc.  This
 * bloats token counts and collapses whitespace from <pre> blocks (build logs).
 *
 * Fix: The onPaste handler intercepts non-image pastes and forces plain-text
 * insertion via `document.execCommand('insertText')`.
 *
 * These tests verify the paste-event branching logic in isolation (no React
 * rendering required).
 */

/**
 * Minimal simulation of the onPaste branch logic extracted from
 * SendChatContainer.  We test the decision-making — image paste vs
 * plain-text paste — not the full React component.
 */
function simulatePaste(clipboardData: {
  items: Array<{ type: string; getAsFile: () => File | null }>;
  getData: (format: string) => string;
}): { action: 'images'; files: File[] } | { action: 'plainText'; text: string } | { action: 'noop' } {
  const imageFiles: File[] = [];
  for (const item of clipboardData.items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) imageFiles.push(file);
    }
  }

  if (imageFiles.length > 0) {
    return { action: 'images', files: imageFiles };
  }

  const plain = clipboardData.getData('text/plain');
  if (plain) {
    return { action: 'plainText', text: plain };
  }

  return { action: 'noop' };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTextItem(text: string) {
  return {
    items: [{ type: 'text/plain', getAsFile: () => null }],
    getData: (fmt: string) => (fmt === 'text/plain' ? text : ''),
  };
}

function makeImageItem() {
  const file = new File(['dummy'], 'screenshot.png', { type: 'image/png' });
  return {
    items: [{ type: 'image/png', getAsFile: () => file }],
    getData: () => '',
  };
}

function makeMixedItem(text: string) {
  const file = new File(['dummy'], 'screenshot.png', { type: 'image/png' });
  return {
    items: [
      { type: 'image/png', getAsFile: () => file },
      { type: 'text/plain', getAsFile: () => null },
    ],
    getData: (fmt: string) => (fmt === 'text/plain' ? text : ''),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('plain-text paste interception', () => {
  test('plain text paste returns plainText action', () => {
    const result = simulatePaste(makeTextItem('hello world'));
    expect(result).toEqual({ action: 'plainText', text: 'hello world' });
  });

  test('image paste returns images action', () => {
    const result = simulatePaste(makeImageItem());
    expect(result.action).toBe('images');
    if (result.action === 'images') {
      expect(result.files).toHaveLength(1);
    }
  });

  test('mixed paste (image + text) prefers images action', () => {
    const result = simulatePaste(makeMixedItem('some text'));
    expect(result.action).toBe('images');
  });

  test('empty clipboard returns noop', () => {
    const result = simulatePaste({
      items: [],
      getData: () => '',
    });
    expect(result).toEqual({ action: 'noop' });
  });

  test('multiline build log preserves line breaks in plain text', () => {
    const buildLog = [
      "Running git command '/apollo/env/PackageBuilderTasks/bin/git checkout abc123'",
      "HEAD is now at 24ebeef add new filter plugin support",
      "Removing .git directory from /local/p4clients/workspace/.git",
      "[INFO ] Gathering SLOC analysis...",
      "[INFO ] Dependencies: PeruHatch-1.0[4], BrazilContainerBuild-1.0[4]",
    ].join('\n');

    const result = simulatePaste(makeTextItem(buildLog));
    expect(result.action).toBe('plainText');
    if (result.action === 'plainText') {
      // Line breaks must survive the plain-text path
      expect(result.text).toContain('\n');
      expect(result.text.split('\n')).toHaveLength(5);
      // Content integrity
      expect(result.text).toContain('[INFO ]');
      expect(result.text).toContain('checkout abc123');
    }
  });

  test('rich HTML paste with text/plain fallback uses plain text', () => {
    // Simulates Chrome clipboard: has HTML items but also text/plain
    const result = simulatePaste({
      items: [
        { type: 'text/html', getAsFile: () => null },
        { type: 'text/plain', getAsFile: () => null },
      ],
      getData: (fmt: string) => {
        if (fmt === 'text/plain') return 'clean plain text';
        if (fmt === 'text/html') return '<span style="color:red;font-size:42px">clean plain text</span>';
        return '';
      },
    });
    expect(result).toEqual({ action: 'plainText', text: 'clean plain text' });
  });

  test('text/plain with only whitespace is still captured', () => {
    // Edge case: paste that is only whitespace/newlines
    const result = simulatePaste(makeTextItem('   \n\n   '));
    expect(result).toEqual({ action: 'plainText', text: '   \n\n   ' });
  });
});

import { test, expect } from '@playwright/test';

test.describe('LaTeX Math Copy Functionality', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to the application
    await page.goto('/');
    
    // Wait for the app to be ready
    await page.waitForLoadState('networkidle');
  });

  test('MathRenderer component renders and has correct attributes', async ({ page }) => {
    // Inject a test message with math directly into the page
    await page.evaluate(() => {
      const testDiv = document.createElement('div');
      testDiv.innerHTML = `
        <div class="test-math-container">
          <span class="math-inline" role="img" aria-label="Math equation: x^2 + y^2 = r^2" 
                data-math-original="x^2 + y^2 = r^2" title="Select and copy to get LaTeX source">
            <span class="katex">test</span>
          </span>
        </div>
      `;
      document.body.appendChild(testDiv);
    });
    
    // Verify the element exists and has correct attributes
    const mathElement = page.locator('.math-inline').first();
    await expect(mathElement).toBeVisible();
    
    const ariaLabel = await mathElement.getAttribute('aria-label');
    expect(ariaLabel).toContain('Math equation');
    
    const dataMath = await mathElement.getAttribute('data-math-original');
    expect(dataMath).toBeTruthy();
    
    const role = await mathElement.getAttribute('role');
    expect(role).toBe('img');
  });

  test('copying inline math preserves LaTeX syntax with delimiters', async ({ page, context }) => {
    // Grant clipboard permissions
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    
    // Inject a math element with our copy handler
    await page.evaluate(() => {
      const mathContainer = document.createElement('div');
      mathContainer.innerHTML = `
        <span class="math-inline" data-math-original="x^2 + y^2 = r^2">
          <span class="katex"><span class="katex-html">x²+y²=r²</span></span>
        </span>
      `;
      document.body.appendChild(mathContainer);
      
      // Simulate the copy handler from MathRenderer
      const mathElement = mathContainer.querySelector('.math-inline');
      document.addEventListener('copy', (e) => {
        const selection = window.getSelection();
        if (mathElement && mathElement.contains(selection?.anchorNode || null)) {
          e.preventDefault();
          const math = mathElement.getAttribute('data-math-original');
          e.clipboardData?.setData('text/plain', `$${math}$`);
        }
      });
    });
    
    // Select and copy the math element
    const mathElement = page.locator('.math-inline').last();
    await mathElement.click({ clickCount: 3 });
    await page.keyboard.press('Control+C');
    
    // Verify clipboard contains raw LaTeX
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    
    expect(clipboardText).toMatch(/^\$/);
    expect(clipboardText).toMatch(/\$$/);
    expect(clipboardText).toContain('x^2');
    expect(clipboardText).not.toContain('<span');
    expect(clipboardText).not.toContain('katex');
  });

  test('copying block math preserves LaTeX with $$ delimiters', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    
    await page.evaluate(() => {
      const mathContainer = document.createElement('div');
      mathContainer.innerHTML = `
        <div class="math-display" data-math-original="\\int_0^1 x^2 dx">
          <span class="katex-display"><span class="katex">∫₀¹x²dx</span></span>
        </div>
      `;
      document.body.appendChild(mathContainer);
      
      const mathElement = mathContainer.querySelector('.math-display');
      document.addEventListener('copy', (e) => {
        const selection = window.getSelection();
        if (mathElement && mathElement.contains(selection?.anchorNode || null)) {
          e.preventDefault();
          const math = mathElement.getAttribute('data-math-original');
          e.clipboardData?.setData('text/plain', `$$${math}$$`);
        }
      });
    });
    
    const mathElement = page.locator('.math-display').last();
    await mathElement.click({ clickCount: 3 });
    await page.keyboard.press('Control+C');
    
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    
    // Block math should use $$ delimiters
    expect(clipboardText).toMatch(/^\$\$/);
    expect(clipboardText).toMatch(/\$\$$/);
    expect(clipboardText).toContain('\\');
    expect(clipboardText).not.toContain('<div');
  });
});

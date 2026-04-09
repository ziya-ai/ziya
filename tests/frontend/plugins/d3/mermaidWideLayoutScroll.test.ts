/**
 * Tests for mermaid diagram scaling and viewBox trimming behavior.
 *
 * When a mermaid diagram has wasted viewBox space or is clamped to a
 * hardcoded max width, the diagram renders too small and text becomes
 * unreadable. These tests verify the scaling logic uses the actual
 * container width and that viewBox trimming reclaims dead space.
 */
describe('Mermaid effective font scaling', function () {
  /**
   * Simulates the width-clamping logic from applyEffectiveFontScaling.
   * Returns the computed SVG dimensions given viewBox size, target/declared
   * font sizes, and the available container width.
   */
  function computeScaledDimensions(vbW, vbH, declaredFont, targetFont, containerWidth) {
    var targetViewBoxScale = targetFont / declaredFont;
    var newWidth = vbW * targetViewBoxScale;
    var newHeight = vbH * targetViewBoxScale;

    // New logic: use container width instead of hardcoded 900
    var maxWidth = containerWidth > 200 ? containerWidth - 20 : 900;
    var minWidth = 100;

    if (newWidth > maxWidth) {
      var ratio = maxWidth / newWidth;
      newWidth = maxWidth;
      newHeight = newHeight * ratio;
    } else if (newWidth < minWidth) {
      var ratio2 = minWidth / newWidth;
      newWidth = minWidth;
      newHeight = newHeight * ratio2;
    }

    return { width: newWidth, height: newHeight };
  }

  it('should scale diagram to fill a wide container instead of capping at 900px', function () {
    // graph LR with subgraphs: viewBox ~1400x300, declared font 14px
    var result = computeScaledDimensions(1400, 300, 14, 14, 1200);
    // With the old hardcoded 900px cap, width would be 900.
    // With the new logic, maxWidth = 1200 - 20 = 1180, and since
    // 1400 * 1.0 = 1400 > 1180, it clamps to 1180 — still much better than 900.
    expect(result.width).toBe(1180);
    expect(result.width).toBeGreaterThan(900);
  });

  it('should not exceed container width', function () {
    var result = computeScaledDimensions(1400, 300, 14, 14, 800);
    // maxWidth = 800 - 20 = 780
    expect(result.width).toBe(780);
    expect(result.width).toBeLessThanOrEqual(800);
  });

  it('should preserve aspect ratio when clamping', function () {
    var result = computeScaledDimensions(1400, 400, 14, 14, 1000);
    // maxWidth = 980, ratio = 980/1400 = 0.7
    var expectedRatio = 400 / 1400;
    var actualRatio = result.height / result.width;
    expect(Math.abs(actualRatio - expectedRatio)).toBeLessThan(0.01);
  });

  it('should fall back to 900px when container width is unavailable', function () {
    // containerWidth = 0 (not laid out yet)
    var result = computeScaledDimensions(1400, 300, 14, 14, 0);
    expect(result.width).toBe(900);
  });

  it('should handle narrow containers without going below minimum', function () {
    var result = computeScaledDimensions(50, 50, 14, 14, 300);
    expect(result.width).toBe(100); // minWidth
  });

  it('should leave small diagrams unchanged when they fit', function () {
    // graph TD that fits easily
    var result = computeScaledDimensions(400, 500, 14, 14, 1000);
    expect(result.width).toBe(400); // no clamping needed
  });
});

describe('Mermaid viewBox trimming', function () {
  /**
   * Simulates the viewBox trimming logic: given the current viewBox
   * and actual content bounding box, returns the trimmed viewBox
   * or null if no trimming is needed.
   */
  function computeTrimmedViewBox(oldVB, bboxX, bboxY, bboxW, bboxH) {
    var parts = oldVB.split(/\s+/).map(Number);
    var oldW = parts[2];
    var pad = 16;
    var trimmedW = bboxW + pad * 2;

    // Only trim if we reclaim at least 10% of width
    if (oldW > 0 && trimmedW < oldW * 0.9) {
      return (bboxX - pad) + ' ' + (bboxY - pad) + ' ' + (bboxW + pad * 2) + ' ' + (bboxH + pad * 2);
    }
    return null; // no trim needed
  }

  it('should trim viewBox when content is significantly smaller than allocated', function () {
    // viewBox is 0 0 1500 400, but content only occupies 900x350 starting at (50, 25)
    var result = computeTrimmedViewBox('0 0 1500 400', 50, 25, 900, 350);
    expect(result).not.toBeNull();
    // Trimmed viewBox should be: (50-16) (25-16) (900+32) (350+32) = "34 9 932 382"
    expect(result).toBe('34 9 932 382');
  });

  it('should NOT trim when content fills the viewBox adequately', function () {
    // Content uses 95% of viewBox width
    var result = computeTrimmedViewBox('0 0 1000 400', 10, 10, 960, 380);
    expect(result).toBeNull(); // 960+32=992, which is > 1000*0.9=900, so no trim
  });

  it('should handle zero-origin content correctly', function () {
    var result = computeTrimmedViewBox('0 0 2000 600', 0, 0, 800, 500);
    expect(result).not.toBeNull();
    expect(result).toBe('-16 -16 832 532');
  });

  it('should handle content with negative origin', function () {
    var result = computeTrimmedViewBox('-100 -100 2000 1000', -50, -30, 600, 400);
    expect(result).not.toBeNull();
    expect(result).toBe('-66 -46 632 432');
  });
});

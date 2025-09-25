// Test file for VegaLite error handling
// This file contains sample invalid VegaLite specs to test error rendering

// Sample 1: Missing required fields
const invalidSpec1 = {
  type: 'vega-lite',
  // Missing data and mark properties
  encoding: {
    x: { field: 'a', type: 'quantitative' },
    y: { field: 'b', type: 'quantitative' }
  }
};

// Sample 2: Invalid field reference
const invalidSpec2 = {
  type: 'vega-lite',
  data: { values: [
    { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 }
  ]},
  mark: 'bar',
  encoding: {
    x: { field: 'nonexistent', type: 'quantitative' },
    y: { field: 'b', type: 'quantitative' }
  }
};

// Sample 3: Invalid mark type
const invalidSpec3 = {
  type: 'vega-lite',
  data: { values: [
    { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 }
  ]},
  mark: 'invalid_mark_type',
  encoding: {
    x: { field: 'a', type: 'quantitative' },
    y: { field: 'b', type: 'quantitative' }
  }
};

// Sample 4: Syntax error in JSON
const invalidSpec4 = `{
  "type": "vega-lite",
  "data": { "values": [
    { "a": 1, "b": 2 }, { "a": 2, "b": 3 }, { "a": 3, "b": 4 }
  ]},
  "mark": "bar",
  "encoding": {
    "x": { "field": "a", "type": "quantitative" },
    "y": { "field": "b", "type": "quantitative" 
  }
}`;

// Sample 5: Valid spec for comparison
const validSpec = {
  type: 'vega-lite',
  data: { values: [
    { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 }
  ]},
  mark: 'bar',
  encoding: {
    x: { field: 'a', type: 'quantitative' },
    y: { field: 'b', type: 'quantitative' }
  }
};

// Export the test specs
export const vegaLiteErrorTests = {
  invalidSpec1,
  invalidSpec2,
  invalidSpec3,
  invalidSpec4,
  validSpec
};

// Function to test error handling
export function testVegaLiteErrorHandling(container, vegaLitePlugin, isDarkMode) {
  console.log('Testing VegaLite error handling...');
  
  // Create test container
  const testContainer = document.createElement('div');
  testContainer.style.width = '500px';
  testContainer.style.height = '300px';
  testContainer.style.border = '1px solid #ccc';
  testContainer.style.margin = '20px';
  testContainer.style.padding = '10px';
  
  // Add to container
  container.appendChild(testContainer);
  
  // Test with invalid spec
  try {
    vegaLitePlugin.render(testContainer, null, invalidSpec1, isDarkMode);
    console.log('Test completed - check if error is displayed properly');
  } catch (error) {
    console.error('Test failed:', error);
  }
  
  return testContainer;
}

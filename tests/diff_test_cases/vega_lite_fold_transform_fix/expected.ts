// Simplified version focusing on the fold transform fix section
export const vegaLitePlugin: D3RenderPlugin = {
  name: 'vega-lite',
  version: '1.0.0',
  
  render: async (container: HTMLElement, spec: any): Promise<void> => {
    // ... other code ...
    
    // Fix fold transform field references
    if (spec.transform && spec.transform.some((t: any) => t.fold)) {
      const foldTransform = spec.transform.find((t: any) => t.fold);
      const keyField = foldTransform?.as?.[0] || 'key';
      const valueField = foldTransform?.as?.[1] || 'value';
      
      if (spec.encoding) {
        console.log(`🔧 FOLD-FIX: Processing fold transform with keyField="${keyField}", valueField="${valueField}"`);
        
        // Fix encoding field references that use generic fold field names
          Object.keys(spec.encoding).forEach(channel => {
            const channelSpec = spec.encoding[channel];
            if (channelSpec?.field) {
              console.log(`🔧 FOLD-FIX: Checking ${channel} encoding field: "${channelSpec.field}"`);
              // Fix "value" -> actual value field name from fold transform
              if (channelSpec.field === 'value' && valueField !== 'value') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "value" -> "${valueField}" in ${channel} encoding`);
                channelSpec.field = valueField;
              }
              // Fix "key" -> actual key field name from fold transform
              if (channelSpec.field === 'key' && keyField !== 'key') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "key" -> "${keyField}" in ${channel} encoding`);
                channelSpec.field = keyField;
              }
              // Fix "dimension" -> actual key field name from fold transform (common in parallel coordinates)
              if (channelSpec.field === 'dimension' && keyField !== 'dimension') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "dimension" -> "${keyField}" in ${channel} encoding`);
                channelSpec.field = keyField;
              }
            }
          });
        }
    }
    
    // ... rest of the plugin code ...
  }
};

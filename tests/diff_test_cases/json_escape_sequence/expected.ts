function parseJson() {
    const jsonStr = `{
        "key": "value"
    }`;
      if (typeof jsonStr === 'string') {
          // Clean up the JSON string
          const cleanJson = jsonStr
              .replace(/\r\n/g, '\n')
              .split('\n')
              .map(line => line.trim())
              .join('\n');
          parsed = JSON.parse(cleanJson);
      }
    return parsed;
}

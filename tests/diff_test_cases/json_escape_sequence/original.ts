function parseJson() {
    const jsonStr = `{
        "key": "value"
    }`;
    // Some comment
    parsed = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
    return parsed;
}

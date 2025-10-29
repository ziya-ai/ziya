# Python Package Update Summary

## Date: October 28, 2025

## Overview
Updated Python packages to address security vulnerabilities while maintaining Python 3.9 compatibility.

## Security Vulnerabilities Addressed

### Fixed ✅
- **aiohttp**: 3.12.12 → 3.13.2 (GHSA-9548-qrrj-x5pj - Request smuggling)
- **pillow**: 11.2.1 → 11.3.0 (PYSEC-2025-61 - Heap buffer overflow in DDS format)
- **starlette**: 0.46.2 → 0.49.1 (GHSA-2c2j-9gv5-cj73, GHSA-7f5h-v6xp-fcq8 - DoS vulnerabilities)
- **transformers**: 4.52.4 → 4.57.1 (Multiple ReDoS vulnerabilities)
- **langchain-text-splitters**: 0.3.8 → 0.3.11 (GHSA-m42m-m8cr-8m58 - XXE vulnerability)

### Cannot Fix (Dependency Constraints) ⚠️
- **cryptography**: 42.0.8 (vulnerable, but 44.0.1+ requires Python 3.10+)
- **langchain-community**: 0.3.25 (vulnerable, but 0.3.27+ requires numpy 2.x)
- **urllib3**: 2.4.0 (vulnerable, but 2.5.0+ incompatible with boto3/botocore)
- **torch**: 2.7.1 (vulnerable, but 2.8.0+ requires Python 3.10+)
- **mcp**: 1.9.3 (vulnerable, but updates may have compatibility issues)
- **scapy**: 2.6.1 (pickle deserialization vulnerability)
- **uv**: 0.7.12 (ZIP parsing vulnerabilities)

## Major Package Updates

### Updated to Latest
- uvicorn: 0.23.2 → 0.38.0
- tiktoken: 0.8.0 → 0.12.0
- python-pptx: 0.6.23 → 1.0.2
- pdfplumber: 0.10.4 → 0.11.7
- pandas: 2.2.3 → 2.3.3
- pyopenssl: 24.3.0 → 25.3.0
- pdfminer-six: 20221105 → 20250506
- python-docx: 1.1.2 → 1.2.0
- And 80+ other packages

### Constrained by Python 3.9 Support
The following packages have newer versions but require Python 3.10+:
- cryptography (44.0.1+)
- torch (2.8.0+)
- numpy (2.x - required by newer langchain-community)

### Constrained by Dependency Conflicts
- urllib3: boto3/botocore require <1.27, but security fix is in 2.5.0
- langchain-community: 0.3.27+ requires numpy 2.x

## Remaining Outdated Packages

```
google-ai-generativelanguage: 0.6.15 → 0.9.0
grpcio-status: 1.71.2 → 1.76.0
huggingface-hub: 0.36.0 → 1.0.1
langchain: 0.3.27 → 1.0.2 (breaking)
langchain-anthropic: 0.2.4 → 1.0.0 (breaking)
langchain-aws: 0.2.35 → 1.0.0 (breaking)
langchain-core: 0.3.79 → 1.0.1 (breaking)
langchain-google-genai: 2.0.4 → 3.0.0 (breaking)
langgraph: 0.2.76 → 1.0.1 (breaking)
marshmallow: 3.26.1 → 4.0.1 (breaking)
packaging: 24.2 → 25.0
protobuf: 5.29.5 → 6.33.0 (breaking)
pydevd-pycharm: 243.26574.90 → 253.27864.50
sse-starlette: 1.8.2 → 3.0.2 (breaking)
```

## Recommendations

### Immediate Actions
1. **Upgrade to Python 3.10+** to unlock security fixes for:
   - cryptography (OpenSSL vulnerabilities)
   - torch (DoS vulnerability)
   - numpy 2.x (enables langchain-community security fix)

2. **Review boto3/urllib3 constraint**: Consider if AWS SDK can be updated to support urllib3 2.x

### Future Major Updates
When ready for breaking changes:
1. **LangChain 1.0 Migration**
   - langchain, langchain-core, langchain-anthropic, langchain-aws all have 1.0 releases
   - Review migration guide

2. **Protobuf 6.x**
   - Major version update with breaking changes

3. **Marshmallow 4.x**
   - Breaking changes in serialization library

## Python Version Support
- **Current**: Python >=3.9,<4.0
- **Recommended**: Upgrade to >=3.10,<4.0 to unlock security fixes

## Build Status
✅ Poetry lock file updated successfully
✅ All dependencies resolved

## Files Modified
- `pyproject.toml` - Updated package version constraints
- `poetry.lock` - Updated lock file with new versions

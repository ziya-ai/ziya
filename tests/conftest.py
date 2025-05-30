"""
Pytest configuration file.

This file contains fixtures and configuration for pytest.
"""

import pytest


def pytest_configure(config):
    """Configure pytest."""
    # Register the asyncio marker
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
    # Register the real_api marker
    config.addinivalue_line("markers", "real_api: mark test as making real API calls")
    # Register the mock_api marker
    config.addinivalue_line("markers", "mock_api: mark test as using mocked API calls")


@pytest.fixture
def mock_aws_credentials(monkeypatch):
    """Mock AWS credentials for testing."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")


@pytest.fixture
def mock_boto3_client(mocker):
    """Mock boto3 client for testing."""
    return mocker.patch("boto3.client")

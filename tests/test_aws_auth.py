"""
Test AWS authentication methods to understand how credentials are being found.
"""
import os
import pytest
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import subprocess
import json

def test_boto3_session_direct():
    """Test direct boto3 session creation."""
    try:
        # Create a session
        session = boto3.Session()
        # Get the credentials
        credentials = session.get_credentials()
        if credentials:
            print(f"Found credentials: access_key={credentials.access_key[:4]}...")
            assert credentials.access_key is not None
            assert credentials.secret_key is not None
        else:
            print("No credentials found in default session")
            assert False, "No credentials found in default session"
    except Exception as e:
        print(f"Error creating boto3 session: {str(e)}")
        assert False, f"Error creating boto3 session: {str(e)}"

def test_boto3_sts_direct():
    """Test direct STS call."""
    try:
        # Create a session
        session = boto3.Session()
        # Create STS client
        sts = session.client('sts')
        # Get caller identity
        identity = sts.get_caller_identity()
        print(f"Successfully authenticated as: {identity.get('Arn', 'Unknown')}")
        assert identity.get('Arn') is not None
    except Exception as e:
        print(f"Error calling STS: {str(e)}")
        assert False, f"Error calling STS: {str(e)}"

def test_aws_cli_direct():
    """Test AWS CLI directly."""
    try:
        # Run AWS CLI command
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            text=True,
            check=True
        )
        # Parse the output
        identity = json.loads(result.stdout)
        print(f"Successfully authenticated via CLI as: {identity.get('Arn', 'Unknown')}")
        assert identity.get('Arn') is not None
    except subprocess.CalledProcessError as e:
        print(f"Error calling AWS CLI: {e.stderr}")
        assert False, f"Error calling AWS CLI: {e.stderr}"
    except Exception as e:
        print(f"Error in AWS CLI test: {str(e)}")
        assert False, f"Error in AWS CLI test: {str(e)}"

def test_aws_env_vars():
    """Test AWS environment variables."""
    # Check for environment variables
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    session_token = os.environ.get("AWS_SESSION_TOKEN")
    profile = os.environ.get("AWS_PROFILE")
    
    print(f"AWS_ACCESS_KEY_ID: {'Set' if access_key else 'Not set'}")
    print(f"AWS_SECRET_ACCESS_KEY: {'Set' if secret_key else 'Not set'}")
    print(f"AWS_SESSION_TOKEN: {'Set' if session_token else 'Not set'}")
    print(f"AWS_PROFILE: {profile if profile else 'Not set'}")
    
    # Try to use these credentials
    if access_key and secret_key:
        try:
            # Create a client with explicit credentials
            sts = boto3.client(
                'sts',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token
            )
            # Get caller identity
            identity = sts.get_caller_identity()
            print(f"Successfully authenticated with env vars as: {identity.get('Arn', 'Unknown')}")
            assert identity.get('Arn') is not None
        except Exception as e:
            print(f"Error using environment variables: {str(e)}")
            assert False, f"Error using environment variables: {str(e)}"

def test_aws_profiles():
    """Test AWS profiles."""
    # Get available profiles
    session = boto3.Session()
    available_profiles = session.available_profiles
    print(f"Available profiles: {available_profiles}")
    
    # Try each profile
    for profile in available_profiles:
        try:
            print(f"Testing profile: {profile}")
            # Create a session with the profile
            profile_session = boto3.Session(profile_name=profile)
            # Get the credentials
            credentials = profile_session.get_credentials()
            if credentials:
                print(f"  Found credentials for profile {profile}: access_key={credentials.access_key[:4]}...")
                # Try to use these credentials
                sts = profile_session.client('sts')
                identity = sts.get_caller_identity()
                print(f"  Successfully authenticated with profile {profile} as: {identity.get('Arn', 'Unknown')}")
                # If we get here, we found working credentials
                return
        except Exception as e:
            print(f"  Error using profile {profile}: {str(e)}")
    
    # If we get here, we didn't find any working profile
    assert False, "No working AWS profile found"

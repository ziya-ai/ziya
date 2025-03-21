"""
AWS utility functions for Ziya.
"""
import os
import sys
import boto3
from botocore.exceptions import ClientError
from app.utils.logging_utils import logger

class ThrottleSafeBedrock:
    """A wrapper for Bedrock client that handles throttling."""
    
    def __init__(self, client):
        self.client = client
        
    def invoke_model(self, *args, **kwargs):
        """Invoke a Bedrock model with throttling handling."""
        try:
            return self.client.invoke_model(*args, **kwargs)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ThrottlingException':
                logger.warning("Throttling detected, please try again later")
                raise ValueError("AWS Bedrock is currently throttling requests. Please try again later.")
            raise

def check_aws_credentials():
    """Check if AWS credentials are valid."""
    try:
        # Try to get caller identity
        sts = boto3.client('sts')
        sts.get_caller_identity()
        return True
    except Exception as e:
        logger.error(f"AWS credentials check failed: {e}")
        raise ValueError(f"AWS credentials check failed: {e}. Please check your AWS credentials and try again.")

def debug_aws_credentials():
    """Debug function to print AWS credential information."""
    import boto3
    import os
    
    logger.info("=== AWS CREDENTIAL DEBUG ===")
    
    # Check command line arguments for --profile
    profile_from_args = None
    for i, arg in enumerate(sys.argv):
        if arg == "--profile" and i + 1 < len(sys.argv):
            profile_from_args = sys.argv[i + 1]
            logger.info(f"Found --profile in command line args: {profile_from_args}")
            break
    
    # Check for ZIYA_AWS_PROFILE (set in main.py)
    ziya_aws_profile = os.environ.get("ZIYA_AWS_PROFILE")
    logger.info(f"ZIYA_AWS_PROFILE: {ziya_aws_profile}")
    
    # Check standard AWS environment variables
    logger.info(f"AWS_PROFILE: {os.environ.get('AWS_PROFILE')}")
    logger.info(f"AWS_DEFAULT_PROFILE: {os.environ.get('AWS_DEFAULT_PROFILE')}")
    logger.info(f"AWS_REGION: {os.environ.get('AWS_REGION')}")
    logger.info(f"AWS_ACCESS_KEY_ID: {'Set' if os.environ.get('AWS_ACCESS_KEY_ID') else 'Not set'}")
    logger.info(f"AWS_SECRET_ACCESS_KEY: {'Set' if os.environ.get('AWS_SECRET_ACCESS_KEY') else 'Not set'}")
    logger.info(f"AWS_SESSION_TOKEN: {'Set' if os.environ.get('AWS_SESSION_TOKEN') else 'Not set'}")
    
    # Check boto3 session credentials
    try:
        # Create session with no explicit profile
        default_session = boto3.Session()
        logger.info(f"Default boto3 session profile name: {default_session.profile_name}")
        
        # Create session with profile from args if available
        if profile_from_args:
            profile_session = boto3.Session(profile_name=profile_from_args)
            logger.info(f"Profile-specific boto3 session profile name: {profile_session.profile_name}")
            
            # Check if the profile exists in credentials file
            import botocore.session
            bs = botocore.session.Session()
            available_profiles = bs.available_profiles
            logger.info(f"Available profiles in credentials file: {available_profiles}")
            if profile_from_args in available_profiles:
                logger.info(f"Profile '{profile_from_args}' exists in credentials file")
            else:
                logger.info(f"Profile '{profile_from_args}' NOT FOUND in credentials file")
        
        # Check credentials from default session
        creds = default_session.get_credentials()
        if creds:
            logger.info(f"Boto3 session has credentials: {bool(creds)}")
            logger.info(f"Credential method: {creds.method}")
            # Don't log the actual credentials, just check if they exist
            logger.info(f"Access key ends with: {creds.access_key[-4:] if creds.access_key else 'None'}")
            logger.info(f"Has session token: {bool(creds.token)}")
            
            # Check expiration if available
            if hasattr(creds, 'expiry_time'):
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                expiry = creds.expiry_time
                if expiry:
                    logger.info(f"Credential expiration: {expiry}")
                    logger.info(f"Time until expiration: {expiry - now}")
                else:
                    logger.info("Credentials don't have expiration information")
        else:
            logger.info("No credentials found in boto3 session")
    except Exception as e:
        logger.error(f"Error checking boto3 credentials: {str(e)}")
    
    # Check if we can call STS
    try:
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        logger.info(f"STS identity: {identity.get('Arn')}")
    except Exception as e:
        logger.error(f"Error calling STS: {str(e)}")
    
    logger.info("=== END AWS CREDENTIAL DEBUG ===")

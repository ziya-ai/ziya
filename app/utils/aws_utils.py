"""
AWS utility functions for Ziya.
"""
import os
import sys
import importlib
import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError, NoCredentialsError
from app.utils.logging_utils import logger

def create_fresh_boto3_session(profile_name=None):
    """Create a fresh boto3 session by reloading the modules.
    
    Args:
        profile_name (str): Optional AWS profile name to use
        
    Returns:
        boto3.Session: A fresh boto3 session
    """
    # First, try to clean up existing modules
    modules_to_remove = []
    for module_name in list(sys.modules.keys()):
        if module_name.startswith('boto3.') or module_name.startswith('botocore.'):
            modules_to_remove.append(module_name)
    
    for module_name in modules_to_remove:
        try:
            del sys.modules[module_name]
        except KeyError:
            pass
    
    # Reload core modules
    try:
        import botocore
        import boto3
        importlib.reload(botocore)
        importlib.reload(boto3)
    except Exception as e:
        logger.warning(f"Error reloading boto3/botocore modules: {e}")
    
    # Create a fresh session
    try:
        # Get the current region from environment variables
        region = os.environ.get("AWS_REGION")
        
        if profile_name:
            return boto3.Session(profile_name=profile_name)
        else:
            return boto3.Session()
    except Exception as e:
        logger.error(f"Error creating fresh boto3 session: {e}")
        # Fall back to default session
        boto3.setup_default_session()
        return boto3._get_default_session()

def get_current_region():
    """Get the current AWS region from environment variables or boto3 session."""
    # First check environment variables
    region = os.environ.get("AWS_REGION")
    if region:
        return region
        
    # Then check boto3 session
    try:
        session = boto3.Session()
        region = session.region_name
        if region:
            return region
    except Exception as e:
        logger.warning(f"Error getting region from boto3 session: {e}")
        
    # Fall back to default region
    from app.config import DEFAULT_REGION
    logger.warning(f"Using default region: {DEFAULT_REGION}")
    return DEFAULT_REGION

class ThrottleSafeBedrock(BaseClient):
    """A wrapper for Bedrock client that handles throttling."""
    
    def __init__(self, client):
        self.client = client
        # Copy attributes from the original client to make this class behave like BaseClient
        self.__dict__.update(client.__dict__)
        
    def invoke_model(self, *args, **kwargs):
        """Invoke a Bedrock model with throttling handling."""
        try:
            return self.client.invoke_model(*args, **kwargs)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ThrottlingException':
                logger.warning("Throttling detected, please try again later")
                raise ValueError("AWS Bedrock is currently throttling requests. Please try again later.")
            raise
            
    def converse(self, *args, **kwargs):
        """Forward converse calls to the client."""
        return self.client.converse(*args, **kwargs)

def check_aws_credentials(is_server_startup=True, profile_name=None):
    """Check if AWS credentials are valid.
    
    Args:
        is_server_startup (bool): Whether this check is being performed during server startup
                                 or during a query in an already running server.
        profile_name (str): Optional AWS profile name to use
    """
    try:
        # Create a fresh session with the specified profile if provided
        if profile_name:
            session = create_fresh_boto3_session(profile_name=profile_name)
            sts = session.client('sts')
        else:
            session = create_fresh_boto3_session()
            sts = session.client('sts')
            
        # Try to get caller identity
        identity = sts.get_caller_identity()
        logger.debug(f"Successfully authenticated as: {identity.get('Arn', 'Unknown')}")
        return True, None
    except NoCredentialsError:
        error_msg = "⚠️ AWS CREDENTIALS ERROR: No AWS credentials found. Please set up your AWS credentials."
        logger.error(f"AWS credentials check failed: No credentials found")
        return False, error_msg
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'InvalidClientTokenId':
            error_msg = "⚠️ AWS CREDENTIALS ERROR: Invalid AWS credentials. Please check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
            logger.error(f"AWS credentials check failed: Invalid token")
            return False, error_msg
        else:
            error_msg = f"⚠️ AWS CREDENTIALS ERROR: {str(e)}"
            logger.error(f"AWS credentials check failed: {e}")
            return False, error_msg
    except Exception as e:
        logger.error(f"AWS credentials check failed: {e}")

        import os
        # First, check if we have any AWS credentials at all
        has_any_credentials = (
            os.environ.get("AWS_ACCESS_KEY_ID") or
            os.environ.get("AWS_SECRET_ACCESS_KEY") or
            os.environ.get("AWS_SESSION_TOKEN") or
            os.path.exists(os.path.expanduser("~/.aws/credentials")) or
            os.path.exists(os.path.expanduser("~/.aws/config"))
        )
        
        if not has_any_credentials:
            return False, """⚠️ AWS CREDENTIALS ERROR: No AWS credentials found.
 
Please set up your AWS credentials using one of these methods:
1. Run 'aws configure' to set up credentials
2. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables
3. For Amazon internal users, run 'mwinit' to get temporary credentials"""
        
        # Create a more user-friendly error message
        error_msg = str(e)
        
        # Log the full error message for debugging
        logger.debug(f"Full error message: {error_msg}")
        
        # For Amazon internal users, always try to get the mwinit message directly from AWS CLI
        # This is more reliable than parsing the boto3 error message
        try:
            import subprocess
            import os
            
            # Get the profile from environment or use default
            profile = os.environ.get("ZIYA_AWS_PROFILE", "default")
            
            # Run AWS CLI directly to get the full error message
            cmd = f"aws sts get-caller-identity --profile {profile} 2>&1"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            # If we got an error, use that message instead as it's more detailed
            if result.returncode != 0:
                cli_error = result.stdout + result.stderr
                logger.debug(f"Got more detailed error from AWS CLI: {cli_error}")
                
                # If the CLI error contains mwinit, use that message but format it nicely
                if "mwinit" in cli_error.lower():
                    # Create a simplified, user-friendly message with different text based on context
                    if is_server_startup:
                        return False, """⚠️ AWS CREDENTIALS ERROR: Your Amazon internal credentials have expired.

Please run the following command to refresh your credentials:

    mwinit

Then try running Ziya again."""
                    else:
                        return False, """⚠️ AWS CREDENTIALS ERROR: Your Amazon internal credentials have expired.

Please run the following command to refresh your credentials:

    mwinit

Then try your query again."""
        except Exception as cli_error:
            logger.debug(f"Failed to get detailed error from AWS CLI: {cli_error}")
        # If we couldn't get a better message from the CLI, use our standard error handling
        # For Amazon internal users, we should always suggest mwinit for credential errors
        # Check if this is an Amazon internal environment first
        is_amazon_internal = _is_amazon_internal_environment(error_msg)
            
        # If we're in an Amazon internal environment, suggest mwinit for credential errors
        if is_amazon_internal and ("InvalidClientTokenId" in error_msg or "ExpiredToken" in error_msg or 
                                  "AccessDenied" in error_msg or "NoCredentialProviders" in error_msg):
            if is_server_startup:
                return False, """⚠️ AWS CREDENTIALS ERROR: Your Amazon internal credentials have expired.


Please run the following command to refresh your credentials:

    mwinit

Then try running Ziya again."""
            else:
                return False, """⚠️ AWS CREDENTIALS ERROR: Your Amazon internal credentials have expired.

Please run the following command to refresh your credentials:

    mwinit

Then try your query again."""
            
        # Standard error detection for non-Amazon environments
        if any(pattern in error_msg.lower() for pattern in ["mwinit", "midway-auth", "iibs-midway", "authenticate by running"]):
            # Format the error message nicely but preserve the original content
            return False, f"⚠️ AWS CREDENTIALS ERROR: {error_msg}"
        elif "ExpiredToken" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Your AWS credentials have expired. Please refresh your credentials."
        elif "InvalidClientTokenId" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Invalid AWS credentials. Please check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        elif "AccessDenied" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Access denied. Your AWS credentials don't have sufficient permissions."
        elif "NoCredentialProviders" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: No AWS credentials found. Please set up your AWS credentials."
        else:
            # Generic error message for other cases
            return False, f"⚠️ AWS CREDENTIALS ERROR: {e}. Please check your AWS credentials and try again."
        
        # Standard error detection for non-Amazon environments
        if "ExpiredToken" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Your AWS credentials have expired. Please refresh your credentials."
        elif "InvalidClientTokenId" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Invalid AWS credentials. Please check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        elif "AccessDenied" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: Access denied. Your AWS credentials don't have sufficient permissions."
        elif "NoCredentialProviders" in error_msg:
            return False, "⚠️ AWS CREDENTIALS ERROR: No AWS credentials found. Please set up your AWS credentials."
        else:
            # Generic error message for other cases
            return False, f"⚠️ AWS CREDENTIALS ERROR: {e}. Please check your AWS credentials and try again."

def _is_amazon_internal_environment(error_message=""):
    """
    Determine if this is an Amazon internal environment.
    Uses more conservative detection to avoid false positives.
    """
    try:
        # Check for definitive Amazon internal indicators
        definitive_indicators = [
            os.path.exists('/apollo'),  # Apollo environment
            'midway' in error_message.lower(),  # Midway auth mentioned in error
            'mwinit' in error_message.lower(),  # mwinit mentioned in error
            'iibs-midway' in error_message.lower(),  # IIBS Midway auth
        ]
        
        # Check AWS profile/config for Amazon-specific patterns
        aws_profile_indicators = [
            'AWS_PROFILE' in os.environ and 'isengard' in os.environ.get('AWS_PROFILE', '').lower(),
            'AWS_CONFIG_FILE' in os.environ and 'midway' in os.environ.get('AWS_CONFIG_FILE', '').lower(),
        ]
        
        # Only consider it Amazon internal if we have definitive indicators
        # OR AWS profile indicators combined with credential-related errors
        return any(definitive_indicators) or (any(aws_profile_indicators) and 
                                            any(term in error_message for term in ["ExpiredToken", "InvalidClientTokenId"]))
    except:
        return False

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

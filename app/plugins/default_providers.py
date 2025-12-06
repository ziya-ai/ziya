"""Default providers for community edition."""

from typing import Tuple, Optional, Dict, Any
from .interfaces import AuthProvider, ConfigProvider
from app.utils.logging_utils import logger

class DefaultAuthProvider(AuthProvider):
    """Standard AWS SDK authentication (community edition)."""
    
    provider_id = "default"
    priority = 0  # Lowest priority (fallback)
    
    def detect_environment(self) -> bool:
        """Always active as fallback."""
        return True
    
    def check_credentials(
        self, 
        profile_name: Optional[str] = None,
        region: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Check AWS credentials using standard boto3."""
        try:
            import boto3
            session = boto3.Session(
                profile_name=profile_name,
                region_name=region
            )
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            arn = identity.get('Arn', 'unknown')
            return True, f"Authenticated as {arn}"
        except Exception as e:
            return False, f"AWS credentials invalid: {str(e)}"
    
    def get_credential_help_message(self, error_context: Optional[str] = None) -> str:
        """Return generic AWS credential help."""
        return (
            "AWS credentials are not configured or have expired.\n"
            "\n"
            "Please configure AWS credentials using one of these methods:\n"
            "  1. Environment variables:\n"
            "       export AWS_ACCESS_KEY_ID=<your-key>\n"
            "       export AWS_SECRET_ACCESS_KEY=<your-secret>\n"
            "\n"
            "  2. AWS credentials file (~/.aws/credentials):\n"
            "       [default]\n"
            "       aws_access_key_id = <your-key>\n"
            "       aws_secret_access_key = <your-secret>\n"
            "\n"
            "  3. IAM role (if running on EC2/ECS)\n"
        )
    
    def get_session(self, profile_name=None, region=None):
        """Return boto3 session."""
        import boto3
        return boto3.Session(profile_name=profile_name, region_name=region)


class DefaultConfigProvider(ConfigProvider):
    """Default configuration for community edition."""
    
    provider_id = "default"
    priority = 0
    
    def get_defaults(self) -> Dict[str, Any]:
        """Return default configuration."""
        return {
            "aws": {
                "region": "us-west-2"
            },
            "models": {
                "endpoint": "bedrock",
                "default_model": "sonnet4.0",
                "temperature": 0.3
            },
            "mcp": {
                "auto_load": False
            }
        }
    
    def should_apply(self) -> bool:
        """Always apply default config."""
        return True

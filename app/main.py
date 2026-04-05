# CRITICAL: Set execution mode FIRST before any other imports
# This must be the very first thing to ensure logging is configured correctly
import sys
if any(cmd in sys.argv for cmd in ['chat', 'ask', 'review', 'explain', 'task']):
    import os
    os.environ["ZIYA_MODE"] = "chat"
    os.environ.setdefault("ZIYA_LOG_LEVEL", "WARNING")

# Suppress transformers warning about PyTorch/TensorFlow not being installed
# We only use transformers for tokenization, not ML models
import os
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
import argparse
import os
import os.path
import sys
import subprocess
import warnings
from typing import Optional

from app.utils.logging_utils import logger
from app.utils.version_util import get_current_version, get_latest_version

# Import configuration instead of individual constants
import app.config.models_config as config
from app.config.app_config import DEFAULT_PORT

# Shared environment setup (common to server + CLI)
from app.config.environment import setup_environment as _shared_setup_environment
# Re-export for backward compatibility (used by tests and possibly plugins)
from app.config.environment import find_endpoint_for_model, validate_model_and_endpoint  # noqa: F401


def get_available_models(endpoint=None):
    """Get list of available models for an endpoint or all endpoints."""
    if endpoint:
        if endpoint not in config.MODEL_CONFIGS:
            return []
        return list(config.MODEL_CONFIGS[endpoint].keys())
    
    # If no endpoint specified, get all models
    all_models = []
    for endpoint_models in config.MODEL_CONFIGS.values():
        all_models.extend(endpoint_models.keys())
    return all_models


def parse_arguments():
    from app.config.common_args import add_common_arguments
    
    parser = argparse.ArgumentParser(
        description="Run with custom options",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Add all common arguments (shared with CLI subcommands)
    add_common_arguments(parser)
    
    # Server-specific arguments (not used by CLI subcommands)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=(f"Port number to run Ziya frontend on "
                              f"(default: {DEFAULT_PORT}, e.g., --port 8080)"))
    parser.add_argument("--max-depth", type=int, default=15,
                        help="Maximum depth for folder structure traversal (e.g., --max-depth 20)")
    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    parser.add_argument("--check-auth", action="store_true",
                        help="Check authentication setup without starting the server")
    parser.add_argument("--info", action="store_true",
                        help="Display system information and configuration for debugging")
    parser.add_argument("--list-models", action="store_true",
                        help="List all supported endpoints and their available models")
    parser.add_argument("--ast", action="store_true",
                        help="Enable AST-based code understanding capabilities (disabled by default)")
    parser.add_argument("--ast-resolution", choices=['disabled', 'minimal', 'medium', 'detailed', 'comprehensive'], 
                       default='medium', help="AST context resolution level (default: medium)")
    parser.add_argument("--mcp", action="store_true", default=True,
                       help="Enable MCP (Model Context Protocol) server integration (enabled by default)")
    parser.add_argument("--no-mcp", action="store_false", dest="mcp",
                       help="Disable MCP (Model Context Protocol) server integration")
    parser.add_argument("--ephemeral", action="store_true",
                       help="Don't persist conversations or data to database beyond current session")
    return parser.parse_args()


def setup_environment(args):
    """Server entry-point environment setup.

    Delegates common settings to the shared setup_environment() in
    app.config.environment, then applies server-only extras.
    """
    _shared_setup_environment(args)

    # -- Server-only settings -----------------------------------------------
    os.environ["ZIYA_MAX_DEPTH"] = str(args.max_depth)

    if args.ast:
        os.environ["ZIYA_ENABLE_AST"] = "true"
        os.environ["ZIYA_AST_RESOLUTION"] = args.ast_resolution
        logger.info(f"AST-based code understanding enabled (resolution: {args.ast_resolution})")

    if args.ephemeral:
        os.environ["ZIYA_EPHEMERAL_MODE"] = "true"
        logger.info("Ephemeral mode enabled - conversations will not be persisted")

    if args.mcp:
        os.environ["ZIYA_ENABLE_MCP"] = "true"
        logger.info("MCP (Model Context Protocol) integration enabled")
    
def check_version_and_upgrade():
    # Skip auto-update if disabled (e.g., for internal edition)
    if os.environ.get('ZIYA_DISABLE_AUTO_UPDATE') == '1':
        logger.debug("Auto-update disabled via ZIYA_DISABLE_AUTO_UPDATE")
        return
    
    current_version = get_current_version()
    latest_version = get_latest_version()

    if latest_version and current_version != latest_version:
        update_package(current_version, latest_version)
    else:
        logger.info(f"Ziya version {current_version} is up to date.")


def is_package_installed_with_pip(package_name: str) -> bool:
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'show', package_name], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


def is_package_installed_with_pipx(package_name: str) -> bool:
    try:
        subprocess.check_call(['pipx', 'list'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result = subprocess.run(['pipx', 'list'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return package_name in result.stdout.decode()
    except subprocess.CalledProcessError:
        return False


def update_package(current_version: str, latest_version: Optional[str]) -> None:
    try:
        logger.info(f"Updating ziya from {current_version} to {latest_version}")

        # Use public PyPI if pip is pointed at a corporate mirror
        pip_index = os.environ.get('PIP_INDEX_URL', '')
        extra_args = []
        if not pip_index or 'pypi.org' not in pip_index:
            extra_args = ['--index-url', 'https://pypi.org/simple/']

        if is_package_installed_with_pip('ziya'):
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade'] + extra_args + ['ziya'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif is_package_installed_with_pipx('ziya'):
            subprocess.check_call(['pipx', 'upgrade', 'ziya'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            logger.info("ziya is not installed with pip or pipx.")

        logger.info("Update completed. Next time you run ziya it will be with the latest version.")
    except Exception as e:
        logger.info(f"Unexpected error upgrading ziya: {e}")


def print_version():
    """Print version information quickly."""
    import os
    current_version = get_current_version()
    
    # Fast edition detection - use edition set by wrapper or default to community
    edition = os.environ.get('ZIYA_EDITION', 'Community Edition')
    
    print(f"Ziya version {current_version} - {edition}")


def print_info(args):
    """Display system information and configuration for debugging."""
    import platform
    import boto3
    from dotenv import load_dotenv, find_dotenv
    
    # Load .env file like models.py does
    dotenv_path = find_dotenv()
    if dotenv_path:
        load_dotenv(dotenv_path, override=True)
    
    print("\n" + "=" * 60)
    print("Ziya System Information")
    print("=" * 60 + "\n")
    
    # Edition from plugins
    from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
    edition = "Community Edition"
    try:
        for provider in _config_providers:
            if hasattr(provider, 'get_defaults'):
                config = provider.get_defaults()
                if 'branding' in config and 'edition' in config['branding']:
                    edition = config['branding']['edition']
                    break
    except Exception:
        pass
    
    # Version info
    current_version = get_current_version()
    print(f"Edition: {edition}")
    print(f"Ziya Version: {current_version}")
    print(f"Python Version: {sys.version.split()[0]}")
    print(f"Python Executable: {sys.executable}")
    print(f"Platform: {platform.platform()}")
    print()
    
    # Plugin information (only if initialized)
    if _initialized:
        print("Plugins:")
        print(f"  Auth Providers: {len(_auth_providers)}")
        for p in _auth_providers:
            provider_id = getattr(p, 'provider_id', 'unknown')
            is_active = p == get_active_auth_provider()
            print(f"    - {provider_id}{' (active)' if is_active else ''}")
        print(f"  Config Providers: {len(_config_providers)}")
        for p in _config_providers:
            print(f"    - {getattr(p, 'provider_id', 'unknown')}")
        print(f"  Registry Providers: {len(_registry_providers)}")
        for p in _registry_providers:
            print(f"    - {getattr(p, 'identifier', 'unknown')}")
        
        # Check for any enterprise formatter files
        import glob
        static_dir = os.path.join(os.path.dirname(__file__), 'templates', 'static', 'js')
        formatter_files = glob.glob(os.path.join(static_dir, '*[Ff]ormatter*.js')) if os.path.exists(static_dir) else []
        if formatter_files:
            print(f"  Enterprise Output Formatters: {len(formatter_files)}")
        print()
    
    # Endpoint and model configuration
    from app.config.models_config import DEFAULT_MODELS
    print(f"Endpoint: {args.endpoint}")
    print(f"Model: {args.model or DEFAULT_MODELS.get(args.endpoint, 'N/A')}")
    if args.model_id:
        print(f"Model ID Override: {args.model_id}")
    print()
    
    # AWS configuration (if using Bedrock)
    if args.endpoint == "bedrock":
        print("AWS Configuration:")
        print(f"  Profile: {args.profile or os.environ.get('AWS_PROFILE', 'default')}")
        print(f"  Region: {args.region or os.environ.get('AWS_REGION', 'us-west-2')}")
        
        try:
            session = boto3.Session(profile_name=args.profile, region_name=args.region)
            credentials = session.get_credentials()
            if credentials:
                try:
                    sts = session.client('sts', region_name=args.region)
                    identity = sts.get_caller_identity()
                    print(f"  Account ID: {identity['Account']}")
                    print(f"  Access Key: {credentials.access_key[:8]}...")
                    print(f"  Status: Valid")
                except Exception as sts_error:
                    error_msg = str(sts_error)
                    if 'ExpiredToken' in error_msg:
                        print(f"  Access Key: {credentials.access_key[:8]}...")
                        # Get credential help from active auth provider
                        from app.plugins import get_active_auth_provider
                        auth_provider = get_active_auth_provider()
                        if auth_provider:
                            print(f"  Status: Expired")
                            print(f"  {auth_provider.get_credential_help_message()}")
                        else:
                            print(f"  Status: Expired (please refresh your credentials)")
                    elif 'InvalidClientTokenId' in error_msg:
                        print(f"  Status: Invalid credentials")
                    else:
                        print(f"  Status: Error - {error_msg[:80]}")
            else:
                print("  Status: No credentials found")
        except Exception as e:
            print(f"  Status: Error - {str(e)[:80]}")
        print()
    
    # Google configuration (if using Google)
    elif args.endpoint == "google":
        print("Google Configuration:")
        api_key = os.environ.get('GOOGLE_API_KEY')
        print(f"  API Key: {'Set' if api_key else 'Not set'}")
        if api_key:
            print(f"  API Key (masked): {api_key[:8]}...")
        print()
    
    # Feature flags
    print("Features:")
    print(f"  AST: {'Enabled' if args.ast else 'Disabled'}")
    if args.ast:
        print(f"  AST Resolution: {args.ast_resolution}")
        print(f"  MCP: {'Enabled' if args.mcp else 'Disabled'}")
        # Check for mcp-registry
        try:
            result = subprocess.run(['which', 'mcp-registry'], capture_output=True, text=True)
            mcp_registry_installed = result.returncode == 0
            print(f"  MCP Registry: {'Installed' if mcp_registry_installed else 'Not found'}")
        except Exception as e:
            logger.debug(f"Error checking for mcp-registry: {e}")
            print(f"  MCP Registry: Not found")
        
        # Check Amazon MCP Registry API access
        print(f"  Amazon MCP Registry API: ", end='')
        try:
            session = boto3.Session(profile_name=args.profile, region_name=args.region or 'us-west-2')
            credentials = session.get_credentials()
            if credentials:
                from botocore.auth import SigV4Auth
                from botocore.awsrequest import AWSRequest
                import httpx
                import json
                
                # Try a minimal API call
                payload = {'maxResults': 1}
                request = AWSRequest(
                    method='POST',
                    url='https://api.registry.mcp.aws.dev/',
                    data=json.dumps(payload),
                    headers={
                        'Content-Type': 'application/x-amz-json-1.0',
                        'X-Amz-Target': 'MCPRegistryService.ListServices'
                    }
                )
                signer = SigV4Auth(credentials, 'mcp-registry-service', args.region or 'us-west-2')
                signer.add_auth(request)
                
                client = httpx.Client(timeout=5.0)
                response = client.post(
                    'https://api.registry.mcp.aws.dev/',
                    headers=dict(request.headers),
                    content=request.body
                )
                
                if response.status_code == 200:
                    print('Accessible')
                elif 'NotAuthorizedException' in response.text or 'Not Authorized' in response.text:
                    print('No permissions (requires access to DEFAULT registry)')
                else:
                    print(f'Error ({response.status_code})')
            else:
                print('No credentials')
        except Exception as e:
            print(f'Check failed ({str(e)[:50]})')
    
    print()
    
    # ZIYA_* environment variables
    ziya_vars = {k: v for k, v in os.environ.items() if k.startswith('ZIYA_')}
    if ziya_vars:
        print("ZIYA Environment Variables:")
        for key, value in sorted(ziya_vars.items()):
            # Mask sensitive values
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                print(f"  {key}: {value[:8]}..." if len(value) > 8 else f"  {key}: ***")
            else:
                print(f"  {key}: {value}")
        print()
    
    print("=" * 60 + "\n")


def print_models():
    """Pretty-print all supported endpoints and their available models."""
    print("\nSupported Endpoints and Models:")
    print("==============================\n")
    
    for endpoint, models in config.MODEL_CONFIGS.items():
        print(f"Endpoint: {endpoint}")
        print("-" * (len(endpoint) + 10))
        
        # Get default model for this endpoint if available
        default_model = config.DEFAULT_MODELS.get(endpoint, "")
        
        # Print each model with its details
        for model_name, model_config in models.items():
            default_marker = " (default)" if model_name == default_model else ""
            print(f"  • {model_name}{default_marker}")
            
            # Print model ID if available
            if "model_id" in model_config:
                print(f"    - Model ID: {model_config['model_id']}")
            
            # Print token limits if available
            if "token_limit" in model_config:
                print(f"    - Token limit: {model_config['token_limit']:,}")
            elif endpoint in config.ENDPOINT_DEFAULTS and "token_limit" in config.ENDPOINT_DEFAULTS[endpoint]:
                print(f"    - Token limit: {config.ENDPOINT_DEFAULTS[endpoint]['token_limit']:,}")
            
            # Print max output tokens if available
            if "max_output_tokens" in model_config:
                print(f"    - Max output tokens: {model_config['max_output_tokens']:,}")
            elif endpoint in config.ENDPOINT_DEFAULTS and "max_output_tokens" in config.ENDPOINT_DEFAULTS[endpoint]:
                print(f"    - Max output tokens: {config.ENDPOINT_DEFAULTS[endpoint]['max_output_tokens']:,}")
            
            # Add a blank line between models for readability
            print()
        
        # Add a blank line between endpoints
        print()


def start_server(args):
    # Dynamically import these only when needed
    from app.utils.langchain_validation_util import validate_langchain_vars
    
    validate_langchain_vars()
    
    # Store the original working directory before any imports that might change it
    original_cwd = os.getcwd()
    logger.info(f"Preserving original working directory: {original_cwd}")
    # Override the default server location from 127.0.0.1 to 0.0.0.0
    # This allows the server to be accessible from other machines on the network
    try:
        # Pre-initialize the model to catch any credential issues before starting the server
        logger.info("Performing initial authentication check...")
        try:
            # Import KnownCredentialException at the top level to avoid UnboundLocalError
            from app.utils.custom_exceptions import KnownCredentialException
            
            # Only check AWS credentials if using Bedrock endpoint
            if args.endpoint == "bedrock":
                # Check AWS credentials first - specify this is server startup
                from app.utils.aws_utils import check_aws_credentials
                
                # Pass the profile from command line args if provided
                valid, message = check_aws_credentials(is_server_startup=True, profile_name=args.profile)
                
                if not valid:
                    # Print clear error message and exit immediately
                    print("\n" + "=" * 80)
                    if "NETWORK ERROR" in message:
                        print(f"⚠️  NETWORK ERROR")
                    else:
                        print(f"⚠️  AUTHENTICATION ERROR")
                    print("=" * 80)
                    print(f"\n{message}\n")
                    if "NETWORK ERROR" not in message:
                        print("Please fix your AWS credentials and try again.")
                    print("=" * 80 + "\n")
                    logger.error(f"AWS credentials check failed: {message}")
                    sys.exit(1)
            
            # Set an environment variable to indicate we've already checked auth
            # This will be used by ModelManager to avoid duplicate initialization
            os.environ["ZIYA_AUTH_CHECKED"] = "true"
            os.environ["ZIYA_PARENT_AUTH_COMPLETE"] = "true"
            
            logger.info("=== STARTUP PHASE 1: Authentication Complete ===")
            # Skip model initialization completely - we'll initialize on demand
            # This avoids the double initialization issue
            os.environ["ZIYA_SKIP_INIT"] = "true"
            
            logger.info("=== STARTUP PHASE 2: Server Initialization ===")
            # Import here to avoid circular imports
            import uvicorn
            from app.server import app, invalidate_folder_cache
            
            # Restore the original working directory before starting the server
            os.chdir(original_cwd)
            
            # Initialize file watcher with cache invalidation (fast - just sets up observer)
            from app.utils.file_watcher import initialize_file_watcher
            from app.utils.file_state_manager import FileStateManager
            file_state_manager = FileStateManager()
            initialize_file_watcher(file_state_manager, os.getcwd(), invalidate_folder_cache)
            logger.info("File watcher initialized")
            
            # NOTE: Folder cache warming now happens in background via server lifespan
            # The server will start immediately and scanning happens asynchronously
            
            logger.info("=" * 80)
            logger.info("🚀 Starting Ziya server...")
            logger.info("=" * 80)
            
            logger.info("=== STARTUP PHASE 3: Starting Server ===")
            # Use uvicorn directly instead of langchain_cli.serve()
            # Set the terminal window/tab title to "Ziya:<port>"
            print(f"\033]0;Ziya:{args.port}\007", end="", flush=True)
            uvicorn.run(app, host="0.0.0.0", port=args.port)
            
        except ValueError as e:
            # Use a class variable to track if we've already displayed an error
            if not hasattr(start_server, "_error_displayed"):
                print("\n" + "=" * 80)
                print(f"⚠️ ERROR: {str(e)}")
                print("=" * 80 + "\n")
                start_server._error_displayed = True
            
            logger.error("Server startup aborted due to configuration error.")
            sys.exit(1)
    except ValueError as e:
        # Use a class variable to track if we've already displayed an error
        if not getattr(ValueError, "_error_displayed", False):
            print("\n" + "=" * 80)
            print(f"⚠️ ERROR: {str(e)}")
            print("=" * 80 + "\n")
            setattr(ValueError, "_error_displayed", True)
            
        logger.error("Server startup aborted due to configuration error.")
        sys.exit(1)

def check_auth(args):
    """Check authentication setup without starting the server."""
    # Set up environment variables first
    setup_environment(args)
    
    try:
        # Only check AWS credentials if using Bedrock endpoint
        if args.endpoint == "bedrock":
            # Import the check_aws_credentials function
            from app.utils.aws_utils import check_aws_credentials
            
            # Check credentials and get status and message
            valid, message = check_aws_credentials()
            
            if valid:
                print("\n✅ AWS credentials are valid.")
                return True
            else:
                print(f"\n{message}")
                return False
        elif args.endpoint == "google":
            # Check Google API key
            import os
            google_api_key = os.environ.get("GOOGLE_API_KEY")
            if google_api_key:
                print("\n✅ Google API key is configured.")
                return True
            else:
                print("\n⚠️ ERROR: GOOGLE_API_KEY environment variable is not set.")
                print("Please set your Google API key: export GOOGLE_API_KEY=<your-key>")
                return False
        else:
            print(f"\n⚠️ ERROR: Unknown endpoint '{args.endpoint}'")
            return False
    except ImportError:
        print("\n⚠️ ERROR: Could not import required utilities to check authentication.")
        return False
    except Exception as e:
        print(f"\n⚠️ ERROR: Authentication check failed: {str(e)}")
        return False


def main():
    # Check if running as CLI subcommand (ziya chat, ziya ask, etc.)
    cli_commands = {'chat', 'ask', 'review', 'explain', 'task'}
    
    # Check if any argument is a CLI command (handles both "ziya chat" and "ziya --profile x chat")
    if any(arg in cli_commands for arg in sys.argv[1:]):
        # Hand off to CLI module
        # CRITICAL: Set chat mode BEFORE importing CLI module
        # This ensures all loggers created during import respect chat mode
        os.environ["ZIYA_MODE"] = "chat"
        os.environ.setdefault("ZIYA_LOG_LEVEL", "WARNING")
        
        # DON'T initialize plugins here - let CLI module do it after parsing args
        # This ensures --profile and other flags are set in environment first
        
        try:
            from app.cli import main as cli_main
            cli_main()
        except ImportError as e:
            print(f"Error loading CLI module: {e}", file=sys.stderr)
            sys.exit(1)
        return
    
    # Legacy --cli flag support
    if "--cli" in sys.argv:
        from app.cli import main as cli_main
        cli_main()
        return
    
    # Check for version flag FIRST to avoid any initialization
    if "--version" in sys.argv:
        print_version()
        return
    
    # Check for list-models flag to avoid unnecessary imports
    if "--list-models" in sys.argv:
        print_models()
        return
        
    # Check for fbuild command to avoid unnecessary imports
    command_name = sys.argv[0].split('/')[-1] if '/' in sys.argv[0] else sys.argv[0]
    if command_name == 'fbuild':
        return
    
    args = parse_arguments()

    # Handle version flag - just print version and exit immediately
    if args.version:
        print_version()
        return
    
    # Handle list-models flag - print models and exit immediately
    if args.list_models:
        print_models()
        return
    
    # Set up environment variables BEFORE plugin initialization
    # This ensures ZIYA_MODEL is set before ModelManager is initialized
    setup_environment(args)
    
    # Initialize plugin system AFTER environment is set up
    # This allows plugins to read the correct model from environment
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    logger.info("Plugin system initialized")

    # Enforce enterprise endpoint policy (after plugins are loaded)
    if not os.environ.get("ZIYA_ALLOW_ALL_ENDPOINTS"):
        from app.plugins import get_allowed_endpoints
        allowed = get_allowed_endpoints()
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        if allowed is not None and endpoint not in allowed:
            print(f"\n❌ Endpoint '{endpoint}' is restricted by your enterprise policy.")
            print(f"   Allowed endpoints: {', '.join(allowed)}\n")
            sys.exit(1)
    
    # Handle info flag - print system info and exit immediately
    if args.info:
        print_info(args)
        return
    
    # Handle check_auth command
    if args.check_auth:
        success = check_auth(args)
        sys.exit(0 if success else 1)
        return

    try:
        # Check for updates and upgrade if available
        check_version_and_upgrade()
    except Exception as e:
        logger.error(f"Error checking version: {e}")
        logger.warning("Continuing with current version...")
    
    start_server(args)


if __name__ == "__main__":
    main()

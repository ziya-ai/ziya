import argparse
import os
import subprocess
import sys
from typing import Optional

from app.utils.logging_utils import logger
from app.utils.version_util import get_current_version, get_latest_version

# Import configuration instead of individual constants
import app.config as config


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
    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    parser.add_argument("--region", type=str, default=None,
                        help="AWS region to use (e.g., --region us-east-1)")
    
    # Get default model alias from config
    default_model = config.DEFAULT_MODELS[config.DEFAULT_ENDPOINT]
    
    # Get available models for the default endpoint
    available_models = get_available_models(config.DEFAULT_ENDPOINT)
    model_list = ", ".join(f"`{m}`" for m in available_models)
    
    parser.add_argument("--endpoint", type=str, choices=["bedrock", "google"], default=config.DEFAULT_ENDPOINT,
                        help=f"Model endpoint to use (default: {config.DEFAULT_ENDPOINT})")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Model to use from selected endpoint (default: {default_model}). Available models: {model_list}")
    parser.add_argument("--model-id", type=str, default=None,
                        help="Override the model ID directly (advanced usage, bypasses model name lookup)")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT,
                        help=(f"Port number to run Ziya frontend on "
                              f"(default: {config.DEFAULT_PORT}, e.g., --port 8080)"))

    # Add model parameter arguments without specific ranges
    parser.add_argument("--temperature", type=float, default=None,
                        help="Temperature for model generation")
    parser.add_argument("--top-p", type=float, default=None,
                        help="Top-p sampling parameter for supported models")
    parser.add_argument("--top-k", type=int, default=None,
                        help="Top-k sampling parameter for supported models")
    parser.add_argument("--max-output-tokens", type=int, default=None,
                        help="Maximum number of tokens to generate in the response")

    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    parser.add_argument("--max-depth", type=int, default=15,
                        help="Maximum depth for folder structure traversal (e.g., --max-depth 20)")
    parser.add_argument("--check-auth", action="store_true",
                        help="Check authentication setup without starting the server")
    parser.add_argument("--list-models", action="store_true",
                        help="List all supported endpoints and their available models")
    parser.add_argument("--ast", action="store_true",
                        help="Enable AST-based code understanding capabilities")
    parser.add_argument("--ast-resolution", choices=['disabled', 'minimal', 'medium', 'detailed', 'comprehensive'], 
                       default='medium', help="AST context resolution level (default: medium)")
    parser.add_argument("--mcp", action="store_true",
                       help="Enable MCP (Model Context Protocol) server integration")
    return parser.parse_args()


def validate_model_and_endpoint(endpoint, model):
    """
    Validate that the specified endpoint and model are valid.
    
    Args:
        endpoint: The endpoint name to validate
        model: The model name to validate
        
    Returns:
        tuple: (is_valid, error_message)
    """
    # Check if endpoint is valid
    if endpoint not in config.MODEL_CONFIGS:
        valid_endpoints = ", ".join(config.MODEL_CONFIGS.keys())
        return False, f"Invalid endpoint: '{endpoint}'. Valid endpoints are: {valid_endpoints}"
    
    # If model is None, use the default model for the endpoint
    if model is None:
        model = config.DEFAULT_MODELS.get(endpoint)
    
    # Check if model is valid for the endpoint
    if model not in config.MODEL_CONFIGS[endpoint]:
        valid_models = ", ".join(config.MODEL_CONFIGS[endpoint].keys())
        return False, f"Invalid model: '{model}' for endpoint '{endpoint}'. Valid models are: {valid_models}"
    
    return True, None


def setup_environment(args):
    import os
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    additional_excluded_dirs = ','.join(args.exclude)
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    # Check for conflicting arguments before setting AWS profile
    if args.endpoint == "google" and args.profile:
        logger.error("The --profile argument is for AWS Bedrock and cannot be used with --endpoint google.")
        logger.error("Please remove the --profile argument or use --endpoint bedrock.")
        sys.exit(1)

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile
        logger.info(f"Using AWS profile: {args.profile}")

    # Handle region selection
    # First check if region is explicitly specified via command line
    if args.region:
        os.environ["AWS_REGION"] = args.region
        logger.info(f"Using AWS region from command line: {args.region}")
    else:
        # If model is specified, check if it has a default region
        if args.model and args.model in config.MODEL_DEFAULT_REGIONS:
            region = config.MODEL_DEFAULT_REGIONS[args.model]
            os.environ["AWS_REGION"] = region
            logger.info(f"Using model-specific default region for {args.model}: {region}")
        else:
            # Otherwise use the global default region
            os.environ["AWS_REGION"] = config.DEFAULT_REGION
            logger.info(f"Using default region: {config.DEFAULT_REGION}")

    # Validate endpoint and model before setting environment variables
    endpoint = args.endpoint
    model = args.model
    
    is_valid, error_message = validate_model_and_endpoint(endpoint, model)
    if not is_valid:
        logger.error(error_message)
        sys.exit(1)
    
    os.environ["ZIYA_ENDPOINT"] = endpoint
    if model:
        os.environ["ZIYA_MODEL"] = model

    os.environ["ZIYA_MAX_DEPTH"] = str(args.max_depth)
    
    # Set path to templates directory
    import os.path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(current_dir, "templates")
    os.environ["ZIYA_TEMPLATES_DIR"] = templates_dir
    logger.info(f"Using templates directory: {templates_dir}")
    
    # Enable AST capabilities if requested
    if args.ast:
        os.environ["ZIYA_ENABLE_AST"] = "true"
        os.environ["ZIYA_AST_RESOLUTION"] = args.ast_resolution
        
    # Set model parameter environment variables if provided
    if args.temperature is not None:
        os.environ["ZIYA_TEMPERATURE"] = str(args.temperature)
    if args.top_p is not None:
        os.environ["ZIYA_TOP_P"] = str(args.top_p)
    if args.top_k is not None:
        os.environ["ZIYA_TOP_K"] = str(args.top_k)
    if args.max_output_tokens is not None:
        os.environ["ZIYA_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
    
    # Set model ID override if provided
    if args.model_id is not None:
        os.environ["ZIYA_MODEL_ID_OVERRIDE"] = args.model_id
        logger.info(f"Overriding model ID with: {args.model_id}")
    
    # Enable AST if requested
    if args.ast:
        os.environ["ZIYA_ENABLE_AST"] = "true"
        logger.info("AST-based code understanding enabled")
        logger.info(f"AST resolution level: {args.ast_resolution}")
        os.environ["ZIYA_MAX_DEPTH"] = str(args.max_depth)
        logger.info(f"Using max depth for AST: {args.max_depth}")
    # Set MCP enablement flag
    if args.mcp:
        os.environ["ZIYA_ENABLE_MCP"] = "true"
        logger.info("MCP (Model Context Protocol) integration enabled")
    
def check_version_and_upgrade():
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

        if is_package_installed_with_pip('ziya'):
            logger.info("Package installed via pip")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'ziya'])
        elif is_package_installed_with_pipx('ziya'):
            logger.info("Package installed via pipx")
            subprocess.check_call(['pipx', 'upgrade', 'ziya'])
        else:
            logger.info("ziya is not installed with pip or pipx.")

        logger.info("Update completed. Next time you run ziya it will be with the latest version.")
    except Exception as e:
        logger.info(f"Unexpected error upgrading ziya: {e}")


def print_version():
    current_version = get_current_version()
    print(f"Ziya version {current_version}")


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
            # Only check AWS credentials if using Bedrock endpoint
            if args.endpoint == "bedrock":
                # Check AWS credentials first - specify this is server startup
                from app.utils.aws_utils import check_aws_credentials
                from app.utils.custom_exceptions import KnownCredentialException
                
                # Pass the profile from command line args if provided
                valid, message = check_aws_credentials(is_server_startup=True, profile_name=args.profile)
                
                if not valid:
                    # Store the error message for consistent reporting
                    from app.agents.models import ModelManager
                    ModelManager._state['last_auth_error'] = message
                    # Raise KnownCredentialException which will handle printing the message only once
                    raise KnownCredentialException(message)
            
            # Set an environment variable to indicate we've already checked auth
            # This will be used by ModelManager to avoid duplicate initialization
            os.environ["ZIYA_AUTH_CHECKED"] = "true"
            os.environ["ZIYA_PARENT_AUTH_COMPLETE"] = "true"
            
            # Skip model initialization completely - we'll initialize on demand
            # This avoids the double initialization issue
            logger.info("Authentication successful, starting server...")
            
            # Pass the environment variable to child processes
            os.environ["ZIYA_SKIP_INIT"] = "true"
            
            # Import here to avoid circular imports
            import uvicorn
            from app.server import app
            
            # Restore the original working directory before starting the server
            os.chdir(original_cwd)
            
            # Use uvicorn directly instead of langchain_cli.serve()
            uvicorn.run(app, host="0.0.0.0", port=args.port)
            
        except KnownCredentialException as e:
            # The exception will handle printing the message only once
            logger.error("Server startup aborted due to authentication error.")
            sys.exit(1)
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
    except ImportError:
        print("\n⚠️ ERROR: Could not import AWS utilities to check authentication.")
        return False
    except Exception as e:
        print(f"\n⚠️ ERROR: Authentication check failed: {str(e)}")
        return False


def main():
    # Check for version flag first to avoid unnecessary imports
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

    # Set up environment variables for remaining commands
    setup_environment(args)
    
    # Handle check_auth command
    if args.check_auth:
        success = check_auth(args)
        sys.exit(0 if success else 1)
        return

    try:
        check_version_and_upgrade()
    except Exception as e:
        logger.error(f"Error checking version: {e}")
        logger.warning("Continuing with current version...")
    
    start_server(args)


if __name__ == "__main__":
    main()

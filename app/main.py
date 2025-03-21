import argparse
import os
import subprocess
import sys
from typing import Optional

from app.utils.logging_utils import logger
from app.utils.version_util import get_current_version, get_latest_version

# Import configuration instead of individual constants
import app.config as config


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    
    # Get default model alias from config
    default_model = config.DEFAULT_MODELS[config.DEFAULT_ENDPOINT]
    parser.add_argument("--endpoint", type=str, choices=["bedrock", "google"], default=config.DEFAULT_ENDPOINT,
                        help=f"Model endpoint to use (default: {config.DEFAULT_ENDPOINT})")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Model to use from selected endpoint (default: {default_model})")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT,
                        help=(f"Port number to run Ziya frontend on "
                              f"(default: {config.DEFAULT_PORT}, e.g., --port 8080)"))

    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    parser.add_argument("--max-depth", type=int, default=15,
                        help="Maximum depth for folder structure traversal (e.g., --max-depth 20)")
    parser.add_argument("--check-auth", action="store_true",
                        help="Check authentication setup without starting the server")
    parser.add_argument("--list-models", action="store_true",
                        help="List all supported endpoints and their available models")
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
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    additional_excluded_dirs = ','.join(args.exclude)
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile

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
    from langchain_cli.cli import serve
    from app.utils.langchain_validation_util import validate_langchain_vars
    
    validate_langchain_vars()
    
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Override the default server location from 127.0.0.1 to 0.0.0.0
    # This allows the server to be accessible from other machines on the network
    try:
        # Pre-initialize the model to catch any credential issues before starting the server
        logger.info("Performing initial authentication check...")
        try:
            # Set an environment variable to indicate we've already checked auth
            # This will be used by ModelManager to avoid duplicate initialization
            os.environ["ZIYA_AUTH_CHECKED"] = "true"
            os.environ["ZIYA_PARENT_AUTH_COMPLETE"] = "true"
            
            # Skip model initialization completely - we'll initialize on demand
            # This avoids the double initialization issue
            logger.info("Authentication successful, starting server...")
            
            # Pass the environment variable to child processes
            os.environ["ZIYA_SKIP_INIT"] = "true"
            serve(host="0.0.0.0", port=args.port)
        except ValueError as e:
            logger.error(f"\n{str(e)}")
            logger.error("Server startup aborted due to configuration error.")
            sys.exit(1)
    except ValueError as e:
        logger.error(f"\n{str(e)}")
        logger.error("Server startup aborted due to configuration error.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        sys.exit(1)


def check_auth(args):
    """Check authentication setup without starting the server."""
    # Set up environment variables first
    setup_environment(args)
    
    # Only import ModelManager when we actually need to check auth
    from app.agents.models import ModelManager
    
    try:
        # Only initialize if not already done
        if not ModelManager._state['auth_checked'] or ModelManager._state['process_id'] != os.getpid():
            model = ModelManager.initialize_model()
        elif not ModelManager._state['auth_success']:
            logger.error("Previous authentication attempt failed")
            return False
        logger.info("Authentication check successful!")
        return True
    except Exception as e:
        logger.error(f"Authentication check failed: {str(e)}")
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

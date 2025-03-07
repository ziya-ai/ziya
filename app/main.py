import argparse
import os
import subprocess
import sys
from typing import Optional

from langchain_cli.cli import serve

from app.utils.logging_utils import logger
from app.utils.langchain_validation_util import validate_langchain_vars
from app.utils.version_util import get_current_version, get_latest_version
from app.server import DEFAULT_PORT
from app.agents.models import ModelManager


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    
    # Get default model alias from ModelManager based on default endpoint
    default_model = ModelManager.DEFAULT_MODELS[ModelManager.DEFAULT_ENDPOINT]
    parser.add_argument("--endpoint", type=str, choices=["bedrock", "google"], default=ModelManager.DEFAULT_ENDPOINT,
                        help=f"Model endpoint to use (default: {ModelManager.DEFAULT_ENDPOINT})")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Model to use from selected endpoint (default: {default_model})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=(f"Port number to run Ziya frontend on "
                              f"(default: {DEFAULT_PORT}, e.g., --port 8080)"))

    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    parser.add_argument("--max-depth", type=int, default=15,
                        help="Maximum depth for folder structure traversal (e.g., --max-depth 20)")
    parser.add_argument("--check-auth", action="store_true",
                        help="Check authentication setup without starting the server")
    return parser.parse_args()


def setup_environment(args):
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    additional_excluded_dirs = ','.join(args.exclude)
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile

    os.environ["ZIYA_ENDPOINT"] = args.endpoint
    if args.model:
        os.environ["ZIYA_MODEL"] = args.model

    # If using Google endpoint, ensure credentials are available
    if args.endpoint == "google" and not ModelManager._load_credentials():
        logger.error(
            "\nGOOGLE_API_KEY environment variable is required for google endpoint.\n"
            "You can set it in your environment or create a .env file in either:\n"
                    "  - Your current directory\n"
                    "  - ~/.ziya/.env\n")
        sys.exit(1)
            
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


def start_server(args):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Override the default server location from 127.0.0.1 to 0.0.0.0
    # This allows the server to be accessible from other machines on the network
    try:
        # Pre-initialize the model to catch any credential issues before starting the server
        logger.info("Performing initial authentication check...")
        try:
            # Try to initialize the model before starting the server
            ModelManager.initialize_model()
            logger.info("Authentication successful, starting server...")
            serve(host="0.0.0.0", port=args.port)
        except ValueError as e:
            logger.error(f"\n{str(e)}")
            logger.error("Server startup aborted due to configuration error.")
            sys.exit(1)
    except ValueError as e:
        logger.error(f"\n{str(e)}")
        logger.error("Server startup aborted due to configuration error.")
        sys.exit(1)

def check_auth(args):
    """Check authentication setup without starting the server."""
    try:
        setup_environment(args)
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
    args = parse_arguments()

    if args.version:
        print_version()
        return

    if args.check_auth:
        success = check_auth(args)
        sys.exit(0 if success else 1)
        return

    try:
        check_version_and_upgrade()
    except Exception as e:
        logger.error(f"Error checking version: {e}")
        logger.warning("Continuing with current version...")
    validate_langchain_vars()
    setup_environment(args)
    start_server(args)


if __name__ == "__main__":
    main()

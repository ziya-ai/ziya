import argparse
import os
import subprocess
import sys
from typing import Optional

from langchain_cli.cli import serve

from app.utils.logging_utils import logger
from app.utils.langchain_validation_util import validate_langchain_vars
from app.utils.version_util import get_current_version, get_latest_version


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--include", default=[], type=lambda x: x.split(','),
                        help="List of directories to include (e.g., --include 'src,static'). Only directories for now")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    parser.add_argument("--model", type=str, choices=["sonnet", "haiku", "opus"], default="sonnet",
                        help="AWS Bedrock Model to use (e.g., --model sonnet)")
    parser.add_argument("--port", type=int, default=6969,
                        help="Port number to run Ziya frontend on (e.g., --port 8080)")
    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    return parser.parse_args()


def setup_environment(args):
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    additional_excluded_dirs = ','.join(args.exclude)
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    additional_included_dirs = ','.join(args.include)
    os.environ["ZIYA_ADDITIONAL_INCLUDE_DIRS"] = additional_included_dirs

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile
    if args.model:
        os.environ["ZIYA_AWS_MODEL"] = args.model


def check_version_and_upgrade():
    current_version = get_current_version()
    latest_version = get_latest_version()

    if latest_version and current_version != latest_version:
        update_package(current_version, latest_version)
    else:
        logger.info(f"Ziya version {current_version} is up to date.")


def update_package(current_version: str, latest_version: Optional[str]) -> None:
    try:
        logger.info(f"Updating ziya from {current_version} to {latest_version}")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'ziya'])
        logger.info("Update completed. Next time you run ziya it will be with the latest version.")

    except Exception as e:
        logger.info(f"Unexpected error upgrading ziya: {e}")


def print_version():
    current_version = get_current_version()
    print(f"Ziya version {current_version}")


def start_server(args):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    serve(port=args.port)


def main():
    args = parse_arguments()

    if args.version:
        print_version()
        return

    check_version_and_upgrade()
    validate_langchain_vars()
    setup_environment(args)
    start_server(args)


if __name__ == "__main__":
    main()

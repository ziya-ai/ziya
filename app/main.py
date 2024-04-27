import os
from langchain_cli.cli import serve
import argparse

def main():
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--include", default=[], type=lambda x: x.split(','),
                        help="List of directories to include (e.g., --include 'src,static'). Only directories for now")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    parser.add_argument("--model", type=str, choices=["sonnet", "haiku", "opus"], default="sonnet",
                        help="AWS Bedrock Model to use  (e.g., --model sonnet)")
    parser.add_argument("--port", type=int, default=6969,
                        help="Port number to run Ziya frontend on (e.g., --port 8080)")
    parser.add_argument("--version", action="store_true",
                        help="Prints the version of Ziya")
    args = parser.parse_args()

    if args.version:
        from importlib.metadata import version
        print(f"Ziya version {version('ziya')}")
        return

    additional_excluded_dirs = ','.join([item for item in args.exclude])
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    additional_included_dirs = ','.join([item for item in args.include])
    os.environ["ZIYA_ADDITIONAL_INCLUDE_DIRS"] = additional_included_dirs

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile
    if args.model:
        os.environ["ZIYA_AWS_MODEL"] = args.model

    langchain_serve_directory = os.path.dirname(os.path.abspath(__file__))
    os.chdir(langchain_serve_directory)
    serve(port=args.port)


if __name__ == "__main__":
    main()
import os
from langchain_cli.cli import serve
import argparse

def main():
    os.environ["ZIYA_USER_CODEBASE_DIR"] = os.getcwd()

    parser = argparse.ArgumentParser(description="Run with custom options")
    parser.add_argument("--exclude", default=[], type=lambda x: x.split(','),
                        help="List of files or directories to exclude (e.g., --exclude 'tst,build,*.py')")
    parser.add_argument("--profile", type=str, default=None,
                        help="AWS profile to use (e.g., --profile ziya)")
    parser.add_argument("--model", type=str, choices=["sonnet", "haiku", "opus"], default="haiku",
                        help="AWS Bedrock Model to use  (e.g., --model sonnet)")
    parser.add_argument("--port", type=int, default=6969,
                        help="Port number to run Ziya frontend on (e.g., --port 8080)")
    args = parser.parse_args()

    additional_excluded_dirs = ','.join([item for item in args.exclude])
    os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = additional_excluded_dirs

    if args.profile:
        os.environ["ZIYA_AWS_PROFILE"] = args.profile
    if args.model:
        os.environ["ZIYA_AWS_MODEL"] = args.model

    langchain_serve_directory = os.path.dirname(os.path.abspath(__file__))
    os.chdir(langchain_serve_directory)
    serve(port=args.port)


if __name__ == "__main__":
    main()

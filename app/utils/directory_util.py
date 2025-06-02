import glob
import os
import time
from typing import List, Tuple, Dict
from app.utils.logging_utils import logger

from app.utils.file_utils import is_binary_file
from app.utils.logging_utils import logger


def get_ignored_patterns(directory: str) -> List[Tuple[str, str]]:
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", directory)
    ignored_patterns: List[Tuple[str, str]] = [
        ("poetry.lock", user_codebase_dir),
        ("package-lock.json", user_codebase_dir),
        (".DS_Store", user_codebase_dir),
        (".git", user_codebase_dir),
        ("node_modules", user_codebase_dir),
        ("build", user_codebase_dir),
        ("dist", user_codebase_dir),
        ("__pycache__", user_codebase_dir),
        ("*.pyc", user_codebase_dir),
        (".venv", user_codebase_dir), # Common virtual environment folder
        ("venv", user_codebase_dir),  # Common virtual environment folder
        (".vscode", user_codebase_dir), # VSCode settings
        (".idea", user_codebase_dir),   # JetBrains IDE settings
        *[(pattern, os.environ["ZIYA_USER_CODEBASE_DIR"])
          for pattern in os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"].split(',')
          if pattern]
    ]

    def read_gitignore(path: str) -> List[Tuple[str, str]]:
        gitignore_patterns: List[Tuple[str, str]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        gitignore_patterns.append((line, os.path.dirname(path)))
        except FileNotFoundError:
            logger.debug(f".gitignore not found at {path}")
        except Exception as e:
            logger.warning(f"Error reading .gitignore at {path}: {e}")
        return gitignore_patterns

    def get_patterns_recursive(path: str) -> List[Tuple[str, str]]:
        patterns: List[Tuple[str, str]] = []
        gitignore_path = os.path.join(path, ".gitignore")

        if os.path.exists(gitignore_path):
            patterns.extend(read_gitignore(gitignore_path))

        for subdir in glob.glob(os.path.join(path, "*/")):
            patterns.extend(get_patterns_recursive(subdir))

        return patterns

    root_gitignore_path = os.path.join(user_codebase_dir, ".gitignore")
    if os.path.exists(root_gitignore_path) and os.path.isfile(root_gitignore_path):
        ignored_patterns.extend(read_gitignore(root_gitignore_path))

    ignored_patterns.extend(get_patterns_recursive(directory))
    return ignored_patterns


def get_complete_file_list(user_codebase_dir: str, ignored_patterns: List[str], included_relative_dirs: List[str]) -> Dict[str, Dict]:
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    file_dict: Dict[str, Dict] = {}
    for pattern in included_relative_dirs:
        for root, dirs, files in os.walk(os.path.normpath(os.path.join(user_codebase_dir, pattern))):
            # Filter out ignored directories and hidden directories
            dirs[:] = [d for d in dirs if not should_ignore_fn(os.path.join(root, d)) and not d.startswith('.')]

            for file in files:
                file_path = os.path.join(root, file)
                if not should_ignore_fn(file_path) and not is_binary_file(file_path) and not file.startswith('.'):
                    file_dict[file_path] = {}

    return file_dict

def is_image_file(file_path: str) -> bool:
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.ico']
    return any(file_path.lower().endswith(ext) for ext in image_extensions)

import glob
import os
from typing import List, Set, Tuple, Dict

from app.utils.gitignore_parser import parse_gitignore_patterns


def get_ignored_patterns(directory: str) -> List[Tuple[str, str]]:
    ignored_patterns: List[Tuple[str, str]] = [
        ("poetry.lock", os.environ["ZIYA_USER_CODEBASE_DIR"]),
        ("package-lock.json", os.environ["ZIYA_USER_CODEBASE_DIR"]),
        (".DS_Store", os.environ["ZIYA_USER_CODEBASE_DIR"]),
        (".git", os.environ["ZIYA_USER_CODEBASE_DIR"]),
        *[(pattern, os.environ["ZIYA_USER_CODEBASE_DIR"])
          for pattern in os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"].split(',')
          if pattern]
    ]

    def read_gitignore(path: str) -> List[Tuple[str, str]]:
        gitignore_patterns: List[Tuple[str, str]] = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    gitignore_patterns.append((line, os.path.dirname(path)))
        return gitignore_patterns

    def get_patterns_recursive(path: str) -> List[Tuple[str, str]]:
        patterns: List[Tuple[str, str]] = []
        gitignore_path = os.path.join(path, ".gitignore")

        if os.path.exists(gitignore_path):
            patterns.extend(read_gitignore(gitignore_path))

        for subdir in glob.glob(os.path.join(path, "*/")):
            patterns.extend(get_patterns_recursive(subdir))

        return patterns

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
                if not should_ignore_fn(file_path) and not is_image_file(file_path) and not file.startswith('.'):
                    file_dict[file_path] = {}

            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if not should_ignore_fn(dir_path):
                    file_dict[dir_path] = {}

    return file_dict

def is_image_file(file_path: str) -> bool:
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.ico']
    return any(file_path.lower().endswith(ext) for ext in image_extensions)

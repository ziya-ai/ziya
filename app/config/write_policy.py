"""
General-purpose write policy service.

Determines whether a given file path is approved for writes.
Used by shell server, filesystem tools, or any tool that writes files.

Config cascade (merged in order, later entries extend earlier):
  1. Defaults (safe_write_paths: .ziya/, /tmp/; no project patterns)
  2. Global user overrides (~/.ziya/write_policy.json)
  3. Per-project overrides (~/.ziya/projects/<id>/project.json -> settings.writePolicy)
"""

import copy
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_WRITE_POLICY = {
    "safe_write_paths": [
        ".ziya/",
        "/tmp/",
        "/var/tmp/",
        "/dev/null",
    ],
    "allowed_write_patterns": [],
    "inplace_edit_flags": {
        "sed": ["-i", "--in-place"],
        "awk": ["-i"],
        "perl": ["-i", "-pi"],
    },
    "destructive_commands": [
        "rm", "rmdir", "mv", "cp", "mkdir",
        "chmod", "chown", "chgrp", "ln",
    ],
    "always_blocked": [
        "sudo", "su", "systemctl", "service",
        "nano", "vim", "vi", "emacs",
    ],
    "allowed_interpreters": ["python3", "python", "node", "ruby"],
    "interpreter_safe_patterns": [
        r"^python3?\s+-m\s+pytest",
        r"^python3?\s+-m\s+unittest",
        r"^python3?\s+-m\s+doctest",
        r"^python3?\s+-m\s+json\.tool",
        r"^python3?\s+-m\s+py_compile",
        r"^python3?\s+-m\s+compileall",
        r"^python3?\s+-m\s+timeit",
        r"^python3?\s+-c\s+",
    ],
    "script_write_indicators": [
        r"open\s*\([^)]*['\"][wa][+]?['\"]",
        r"\.write\s*\(",
        r"shutil\.\s*(copy|move|rmtree|copytree)",
        r"os\.\s*(rename|remove|unlink|makedirs|mkdir|rmdir)",
        r"pathlib.*\.\s*(write_text|write_bytes|unlink|mkdir|rename|rmdir)",
        r"subprocess\.\s*(run|call|Popen).*\b(rm|mv|cp|sed\s+-i)\b",
    ],
}


class WritePolicyManager:
    """
    Manages merged write policy. Any tool that writes files should call:
        get_write_policy_manager().is_write_allowed(path)
    """

    def __init__(self):
        self._policy: Dict[str, Any] = copy.deepcopy(DEFAULT_WRITE_POLICY)
        self._project_id: Optional[str] = None
        self._project_root: Optional[str] = None

    @property
    def policy(self) -> Dict[str, Any]:
        return self._policy

    def load_for_project(self, project_id: str, project_root: str) -> None:
        """Load and merge config cascade for a project."""
        self._project_id = project_id
        self._project_root = project_root
        self._policy = copy.deepcopy(DEFAULT_WRITE_POLICY)

        # Global user overrides
        global_path = Path.home() / ".ziya" / "write_policy.json"
        if global_path.exists():
            try:
                with open(global_path) as f:
                    self._merge(json.load(f))
            except Exception:
                pass

        # Per-project overrides
        if project_id:
            pf = Path.home() / ".ziya" / "projects" / project_id / "project.json"
            if pf.exists():
                try:
                    with open(pf) as f:
                        wp = json.load(f).get("settings", {}).get("writePolicy", {})
                    if wp:
                        self._merge(wp)
                except Exception:
                    pass

    def merge_env_overrides(self, env_map: Dict[str, str]) -> None:
        """Merge overrides from environment variables (used by shell subprocess)."""
        overrides = {}
        for env_key, policy_key in [
            ('SAFE_WRITE_PATHS', 'safe_write_paths'),
            ('ALLOWED_WRITE_PATTERNS', 'allowed_write_patterns'),
            ('ALLOWED_INTERPRETERS', 'allowed_interpreters'),
            ('ALWAYS_BLOCKED_COMMANDS', 'always_blocked'),
        ]:
            val = env_map.get(env_key, '').strip()
            if val:
                overrides[policy_key] = [p.strip() for p in val.split(',') if p.strip()]
        if overrides:
            self._merge(overrides)

    def _merge(self, overrides: Dict[str, Any]) -> None:
        for key, value in overrides.items():
            if key not in self._policy:
                self._policy[key] = value
            elif isinstance(self._policy[key], list) and isinstance(value, list):
                for item in value:
                    if item not in self._policy[key]:
                        self._policy[key].append(item)
            elif isinstance(self._policy[key], dict) and isinstance(value, dict):
                self._policy[key].update(value)
            else:
                self._policy[key] = value

    # -- Public API (usable by any tool) ---------------------------------

    def _ensure_loaded_for_root(self, project_root: str) -> None:
        """
        Lazily load project-specific policy when the project root is known
        but ``load_for_project`` has not been called yet (or was called for
        a different project).  Resolves project_id by scanning
        ``~/.ziya/projects/*/project.json`` for a matching ``path`` field.
        """
        if not project_root or self._project_root == project_root:
            return  # Already loaded for this root (or no root provided)

        import json as _json
        projects_dir = Path.home() / ".ziya" / "projects"
        if not projects_dir.is_dir():
            return
        for candidate in projects_dir.iterdir():
            pf = candidate / "project.json"
            if pf.is_file():
                try:
                    with open(pf) as f:
                        data = _json.load(f)
                    if data.get("path") == project_root:
                        self.load_for_project(data["id"], project_root)
                        return
                except Exception:
                    continue

    def is_write_allowed(self, target_path: str, project_root: str = "") -> bool:
        root = project_root or self._project_root or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        self._ensure_loaded_for_root(root)
        return self._check_path(target_path, root)

    def check_write(self, target_path: str, project_root: str = "") -> Tuple[bool, str]:
        root = project_root or self._project_root or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        self._ensure_loaded_for_root(root)
        if self.is_write_allowed(target_path, project_root):
            return True, ""
        patterns = self._policy.get('allowed_write_patterns', [])
        return False, (
            f"Write to '{target_path}' blocked. Approved: "
            f"{', '.join(self._policy.get('safe_write_paths', []))}"
            + (f" | patterns: {', '.join(patterns)}" if patterns else "")
        )

    def get_effective_policy(self) -> Dict[str, Any]:
        return copy.deepcopy(self._policy)

    def update_project_policy(self, project_id: str, overrides: Dict[str, Any]) -> None:
        pf = Path.home() / ".ziya" / "projects" / project_id / "project.json"
        if not pf.exists():
            return
        with open(pf) as f:
            data = json.load(f)
        data.setdefault("settings", {})["writePolicy"] = overrides
        with open(pf, 'w') as f:
            json.dump(data, f, indent=2)
        if self._project_id == project_id:
            self.load_for_project(project_id, self._project_root or "")

    # -- Internal --------------------------------------------------------

    def _check_path(self, target_path: str, project_root: str) -> bool:
        if not target_path:
            return False
        raw = target_path.strip().strip("'\"")
        expanded = os.path.expanduser(raw)
        resolved = os.path.join(project_root, expanded) if (project_root and not os.path.isabs(expanded)) else expanded

        for safe in self._policy.get('safe_write_paths', []):
            if safe.startswith('/'):
                if resolved.startswith(safe) or resolved == safe.rstrip('/'):
                    return True
            else:
                if project_root:
                    abs_safe = os.path.join(project_root, safe)
                    if resolved.startswith(abs_safe) or resolved == abs_safe.rstrip('/'):
                        return True
                if raw.startswith(safe) or raw == safe.rstrip('/'):
                    return True

        rel = resolved[len(project_root):].lstrip(os.sep) if (project_root and resolved.startswith(project_root)) else raw
        for raw_pattern in self._policy.get('allowed_write_patterns', []):
            # Handle comma-separated patterns that were stored as a single
            # entry (e.g. "*.txt,*.md") by the frontend input field.
            for pattern in raw_pattern.split(','):
                pattern = pattern.strip()
                if pattern and (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern)):
                    return True
        return False


_manager: Optional[WritePolicyManager] = None

def get_write_policy_manager() -> WritePolicyManager:
    global _manager
    if _manager is None:
        _manager = WritePolicyManager()
    return _manager

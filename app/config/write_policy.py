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
import logging
import os
import threading
from app.config.env_registry import ziya_env
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Install-relative extension directory. Every ``*.py`` dropped here is executed
# at startup by PromptExtensionManager.load_extensions_from_directory
# (spec.loader.exec_module), so a write into it is *persistent* code execution
# on the next launch — not a one-shot (PenPal #169, CWE-434). This is a HARD
# floor: refused regardless of write policy, direct_write_mode, YOLO, or a
# task-scope grant. Every write sink consults is_install_extension_path()
# BEFORE any allow decision. write_policy.py lives at app/config/, so the
# install ``app/`` root is two parents up.
_INSTALL_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INSTALL_EXTENSIONS_DIR = os.path.realpath(os.path.join(_INSTALL_APP_ROOT, "extensions"))


DEFAULT_WRITE_POLICY = {
    # Controls whether file_write can operate beyond safe paths + patterns.
    # "none" = only safe paths and patterns (default)
    # "new_files" = also allow creating new files anywhere in project
    # "all_files" = allow writing any file within the project
    "direct_write_mode": "none",
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
    # Process-spawning indicators inside an allowlisted interpreter's inline
    # code (``python3 -c ...``). These are checked independently of
    # script_write_indicators: a one-liner that writes no files but spawns
    # an arbitrary process (os.system, subprocess with shell=True, eval/exec)
    # bypasses the shell command allowlist entirely, since the allowlist only
    # gates the outer ``python3`` invocation, not what the interpreted code
    # itself executes [PenPal #157, CWE-94].
    "script_process_indicators": [
        r"os\.\s*system\s*\(",
        r"os\.\s*popen\s*\(",
        r"os\.\s*exec[lv]?p?e?\s*\(",
        r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True",
        r"pty\.\s*spawn\s*\(",
        r"\bexec\s*\(",
        r"\beval\s*\(",
        r"__import__\s*\(\s*['\"]os['\"]\s*\)",
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
        # Roots for which the projects/* scan found no matching project.
        # Without this the scan (and a decrypt attempt per project file) is
        # repeated on every policy check, since _project_root is only set on
        # a successful match.
        self._unresolved_roots: set = set()

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
        self._safe_merge_json(global_path)

        # Per-project overrides
        if project_id:
            pf = Path.home() / ".ziya" / "projects" / project_id / "project.json"
            data = self._read_json_ale_aware(pf)
            if data:
                wp = data.get("settings", {}).get("writePolicy", {})
                if wp:
                    self._merge(wp)

    # Paths already reported as unreadable.  The read helper is a
    # staticmethod invoked once per candidate project file on every policy
    # check, so an undecryptable file would otherwise emit an identical
    # warning on each call with no new information.
    _warned_unreadable: set = set()

    @staticmethod
    def _read_json_ale_aware(filepath: Path) -> Optional[Dict[str, Any]]:
        """Read a JSON file, transparently decrypting ALE envelopes."""
        if not filepath.exists():
            return None
        try:
            raw = filepath.read_bytes()
            if not raw:
                return None
            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                plaintext = get_encryptor().decrypt(raw)
                return json.loads(plaintext)
            return json.loads(raw)
        except (ImportError, json.JSONDecodeError):
            return None  # Module missing or file not valid JSON — treat as absent
        except Exception as e:
            key = str(filepath)
            if key not in WritePolicyManager._warned_unreadable:
                WritePolicyManager._warned_unreadable.add(key)
                # Log the full path, not just the basename: every candidate
                # is named "project.json", so the basename alone makes the
                # message unattributable to a specific project.
                logging.getLogger(__name__).warning(
                    "Failed to read policy file %s: %s — treating as absent", key, e,
                )
            return None

    def _safe_merge_json(self, filepath: Path) -> None:
        """Read a JSON file (ALE-aware) and merge it into current policy."""
        data = self._read_json_ale_aware(filepath)
        if data:
            self._merge(data)

    @staticmethod
    def _write_json_ale_aware(filepath: Path, data: Dict[str, Any]) -> None:
        """Write a JSON file, applying ALE encryption when enabled.

        Mirrors the pattern in ``BaseStorage._write_json``.
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(data, indent=2).encode("utf-8")
        try:
            from app.utils.encryption import get_encryptor
            encryptor = get_encryptor()
            if encryptor.is_enabled("session_data"):
                filepath.write_bytes(encryptor.encrypt(plaintext, "session_data"))
                return
        except ImportError:
            pass  # Encryption module not available — write plaintext
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Encryption enabled but encrypt() failed for %s: %s — writing plaintext",
                filepath.name, e,
            )
        filepath.write_bytes(plaintext)

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
        # Parent-authoritative project write policy (ZIYA_PROJECT_WRITE_PATHS):
        # the unsigned project writePolicy set, injected by the manager from the
        # decrypted project.json. Unlike SAFE_WRITE_PATHS this is NOT gated by a
        # root signature — it grants the shell exactly what file_write already
        # gets from the same policy in the trusted parent. Directory/prefix
        # entries feed safe_write_paths; glob entries (containing * ? [) feed
        # allowed_write_patterns, mirroring how the base policy stores each.
        from app.config.scope_canonical import PROJECT_WRITE_PATHS_ENV_KEY
        proj_raw = env_map.get(PROJECT_WRITE_PATHS_ENV_KEY, '').strip()
        if proj_raw:
            proj_paths, proj_patterns = [], []
            for entry in (p.strip() for p in proj_raw.split(',') if p.strip()):
                if any(ch in entry for ch in '*?['):
                    proj_patterns.append(entry)
                else:
                    proj_paths.append(entry)
            if proj_paths:
                overrides.setdefault('safe_write_paths', []).extend(proj_paths)
            if proj_patterns:
                overrides.setdefault('allowed_write_patterns', []).extend(proj_patterns)
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
        if project_root in self._unresolved_roots:
            return  # Scanned before with no match — don't re-walk projects/

        projects_dir = Path.home() / ".ziya" / "projects"
        if not projects_dir.is_dir():
            self._unresolved_roots.add(project_root)
            return
        for candidate in projects_dir.iterdir():
            pf = candidate / "project.json"
            data = self._read_json_ale_aware(pf)
            if data and data.get("path") == project_root:
                self.load_for_project(data["id"], project_root)
                return
        # No project claims this root.  Remember that so subsequent checks
        # skip the scan entirely.
        self._unresolved_roots.add(project_root)

    @staticmethod
    def is_install_extension_path(target_path: str, project_root: str = "") -> bool:
        """True if *target_path* resolves inside the install extensions tree.

        realpath both sides so a symlink into the tree is caught, and resolve
        a relative target against *project_root* first (the shape file_write
        and the shell both hand in). Callers MUST consult this before any
        allow decision — including YOLO and task-scope grants — because the
        whole point of PenPal #169 is that no policy setting can re-open the
        auto-exec directory as a write target.
        """
        if not target_path:
            return False
        raw = os.path.expanduser(str(target_path).strip().strip("'\""))
        candidate = (
            os.path.join(project_root, raw)
            if (project_root and not os.path.isabs(raw)) else raw
        )
        resolved = os.path.realpath(candidate)
        ext = _INSTALL_EXTENSIONS_DIR
        return resolved == ext or resolved.startswith(ext + os.sep)

    def is_write_allowed(self, target_path: str, project_root: str = "") -> bool:
        root = project_root or self._project_root or ziya_env("ZIYA_USER_CODEBASE_DIR") or ""
        self._ensure_loaded_for_root(root)
        if self.is_install_extension_path(target_path, root):
            return False
        return self._check_path(target_path, root)

    def is_direct_write_allowed(
        self,
        target_path: str,
        project_root: str = "",
        file_exists: bool = True,
    ) -> Tuple[bool, str]:
        """Check if a direct file_write is allowed, considering direct_write_mode.

        Unlike ``is_write_allowed`` (used by the shell server), this method
        also honours the ``direct_write_mode`` setting so the file_write
        tool can create/overwrite files within the project when the user
        has opted in.

        Returns (allowed, reason) — *reason* is empty when allowed.
        """
        root = (
            project_root
            or self._project_root
            or ziya_env("ZIYA_USER_CODEBASE_DIR") or ""
        )
        self._ensure_loaded_for_root(root)

        # Hard floor, checked before the base-policy allow and before any
        # direct_write_mode widening below (PenPal #169).
        if self.is_install_extension_path(target_path, root):
            return False, "Writes into the install extension directory are refused (auto-executed at startup)."

        # Always allow if the base policy already permits it
        if self._check_path(target_path, root):
            return True, ""

        mode = self._policy.get("direct_write_mode", "none")
        if mode == "none":
            return self.check_write(target_path, root)

        # Resolve and ensure the target is inside the project root
        if root and self._is_within_project(target_path, root):
            if mode == "all_files":
                return True, ""
            if mode == "new_files" and not file_exists:
                return True, ""

        # Fall back to the standard policy check
        return self.check_write(target_path, root)

    def check_write(self, target_path: str, project_root: str = "") -> Tuple[bool, str]:
        root = project_root or self._project_root or ziya_env("ZIYA_USER_CODEBASE_DIR") or ""
        self._ensure_loaded_for_root(root)
        if self.is_write_allowed(target_path, project_root):
            return True, ""
        patterns = self._policy.get('allowed_write_patterns', [])
        return False, (
            f"Write to '{target_path}' blocked. Approved: "
            f"{', '.join(self._policy.get('safe_write_paths', []))}"
            + (f" | patterns: {', '.join(patterns)}" if patterns else "")
        )

    def get_effective_policy(self, project_root: str = "") -> Dict[str, Any]:
        """Return the merged policy, lazily loading the project's overrides.

        The ``_ensure_loaded_for_root`` call is what keeps this method
        consistent with the enforcement accessors (``is_write_allowed``,
        ``is_direct_write_allowed``, ``check_write``), all of which already
        make it.  Without it this method returned DEFAULT_WRITE_POLICY
        whenever ``load_for_project`` had not run — which is the common case,
        since the only caller is the ``GET /write-policy/{project_id}``
        route.  Prompt builders read the policy through this method, so the
        model was told ``.ziya/`` and ``/tmp/`` were the only writable paths
        while ``file_write`` happily accepted the project's configured
        patterns.  Whether the prompt was accurate depended on whether an
        enforcement call had already populated ``_project_root``.
        """
        root = project_root or self._project_root or ziya_env("ZIYA_USER_CODEBASE_DIR") or ""
        self._ensure_loaded_for_root(root)
        return copy.deepcopy(self._policy)

    def update_project_policy(self, project_id: str, overrides: Dict[str, Any]) -> None:
        pf = Path.home() / ".ziya" / "projects" / project_id / "project.json"
        if not pf.exists():
            # Create the directory and a minimal project file
            pf.parent.mkdir(parents=True, exist_ok=True)
            data = {"id": project_id, "settings": {}}
        else:
            data = self._read_json_ale_aware(pf)
            if data is None:
                data = {"id": project_id, "settings": {}}
        if not isinstance(data.get("settings"), dict):
            data["settings"] = {}
        if not isinstance(data["settings"].get("writePolicy"), dict):
            data["settings"]["writePolicy"] = {}
        data["settings"]["writePolicy"].update(overrides)
        self._write_json_ale_aware(pf, data)
        # The prompt-facing per-root cache holds a snapshot of the merged
        # policy, so an edit here would otherwise stay invisible to the
        # Session Context block until the process restarted -- the same
        # prompt/enforcement divergence that cache exists to fix.
        invalidate_policy_cache()
        if self._project_id == project_id:
            self.load_for_project(project_id, self._project_root or "")

    # -- Internal --------------------------------------------------------

    def _is_within_project(self, target_path: str, project_root: str) -> bool:
        """Return True if *target_path* resolves to somewhere inside *project_root*."""
        if not project_root:
            return False
        raw = target_path.strip().strip("'\"")
        expanded = os.path.expanduser(raw)
        resolved = (
            os.path.join(project_root, expanded)
            if not os.path.isabs(expanded)
            else expanded
        )
        # Require an os.sep boundary so a sibling dir whose name is a prefix of
        # the root (e.g. "/proj-backup" vs root "/proj") is not treated as
        # inside it. normpath collapses any ".." before the comparison.
        resolved_norm = os.path.normpath(resolved)
        root_norm = os.path.normpath(project_root)
        return resolved_norm == root_norm or resolved_norm.startswith(root_norm + os.sep)

    def _check_path(self, target_path: str, project_root: str) -> bool:
        if not target_path:
            return False
        raw = target_path.strip().strip("'\"")
        expanded = os.path.expanduser(raw)
        # normpath collapses ".." BEFORE any containment check. Without this a
        # target like ".ziya/../../../etc/cron.d/x" string-prefix-matches an
        # allowed ".ziya/" path yet resolves outside project_root at execution
        # time (the shell/open() later resolves the "..") — CWE-22 sandbox
        # escape. The comparisons below all use this normalized form.
        resolved = os.path.normpath(
            os.path.join(project_root, expanded) if (project_root and not os.path.isabs(expanded)) else expanded
        )

        for safe in self._policy.get('safe_write_paths', []):
            if safe.startswith('/'):
                # Resolve the safe path too so symlinks like /tmp -> /private/tmp
                # are handled correctly on macOS.
                safe_resolved = os.path.realpath(safe.rstrip('/'))
                if resolved.startswith(safe_resolved + os.sep) or resolved == safe_resolved:
                    return True
                # Also check the literal (non-resolved) form for non-symlink cases
                _safe_lit = os.path.normpath(safe.rstrip('/'))
                if resolved == _safe_lit or resolved.startswith(_safe_lit + os.sep):
                    return True
            else:
                if project_root:
                    abs_safe = os.path.normpath(os.path.join(project_root, safe.rstrip('/')))
                    if resolved == abs_safe or resolved.startswith(abs_safe + os.sep):
                        return True
                # The former "raw.startswith(safe)" fallback is removed: it
                # compared the un-normalized raw string, so ".ziya/../../etc"
                # matched a ".ziya" safe entry. All callers supply project_root,
                # so the normalized project-relative check above is sufficient.

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


# -------------------------------------------------------------------------
# Prompt-facing, root-addressed policy reads
# -------------------------------------------------------------------------
#
# Enforcement is root-addressed: ``fileio.file_write`` passes the
# request-scoped project root into ``is_direct_write_allowed``, so
# ``_ensure_loaded_for_root`` reloads for whichever project is being
# written to.  Every PROMPT reader instead called
# ``get_effective_policy()`` with no root, which resolves to
# ``self._project_root`` -- whatever project last touched the singleton --
# and ``_ensure_loaded_for_root`` then early-returns because the roots
# "match".  With two projects open, project B's prompt therefore rendered
# project A's policy while B's writes were checked against B's: the model
# was told a path needed a diff, tried file_write anyway, and found it
# accepted.  Measured with A=``docs/*`` and B=``tests/``+``app/``: B's
# prompt listed ``docs/*`` while enforcement allowed ``app/utils/x.py``.
#
# Deliberately does NOT reuse the shared singleton.  Passing the root to
# it would fix the read but make every prompt render re-pin
# ``_project_root``, so concurrent windows on different projects would
# thrash the loaded policy -- the hazard
# ``MCPManager._apply_project_write_paths`` already avoids with a
# throwaway instance, for this same reason.
#
# Cached per root because the load walks
# ``~/.ziya/projects/*/project.json`` and ALE-decrypts each candidate
# until one claims the root; a user with dozens of registered projects
# would otherwise pay that scan on every request.
_root_policy_cache: Dict[str, Dict[str, Any]] = {}
_root_policy_lock = threading.Lock()


def effective_policy_for_root(project_root: str) -> Dict[str, Any]:
    """Merged write policy for a SPECIFIC project root.

    Use this for anything that DESCRIBES the policy (prompt blocks, UI
    summaries).  Enforcement keeps using the singleton's
    ``is_write_allowed`` / ``is_direct_write_allowed``, which take the
    root as an argument and remain the authority.

    Falls back to the singleton when no root is known -- the
    single-project and CLI case, and the previous behaviour.
    """
    if not project_root:
        return get_write_policy_manager().get_effective_policy()
    with _root_policy_lock:
        cached = _root_policy_cache.get(project_root)
    if cached is not None:
        return copy.deepcopy(cached)
    pm = WritePolicyManager()
    pm._ensure_loaded_for_root(project_root)
    if pm._project_root != project_root:
        # No registered project claims this root, so ``load_for_project``
        # never ran and the global ~/.ziya/write_policy.json was never
        # merged.  Merge it here rather than reporting bare defaults.
        pm._safe_merge_json(Path.home() / ".ziya" / "write_policy.json")
    policy = copy.deepcopy(pm.policy)
    with _root_policy_lock:
        _root_policy_cache[project_root] = policy
    return copy.deepcopy(policy)


def invalidate_policy_cache(project_root: Optional[str] = None) -> None:
    """Drop cached per-root policies; all roots when none is given.

    Clearing everything on an unscoped call is deliberate: the caller
    (``update_project_policy``) knows a project id, not a root, and a
    needless reload costs one directory scan.
    """
    with _root_policy_lock:
        if project_root:
            _root_policy_cache.pop(project_root, None)
        else:
            _root_policy_cache.clear()
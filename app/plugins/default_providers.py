"""Default providers for community edition."""

import os
import fnmatch
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List
from .interfaces import AuthProvider, ConfigProvider, DirectoryScanProvider, ScanCustomization
from app.utils.logging_utils import logger

class DefaultAuthProvider(AuthProvider):
    """Standard AWS SDK authentication (community edition)."""
    
    provider_id = "default"
    priority = 0  # Lowest priority (fallback)
    
    def detect_environment(self) -> bool:
        """Always active as fallback."""
        return True
    
    def check_credentials(
        self, 
        profile_name: Optional[str] = None,
        region: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Check AWS credentials using standard boto3."""
        try:
            import boto3
            session = boto3.Session(
                profile_name=profile_name,
                region_name=region
            )
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            arn = identity.get('Arn', 'unknown')
            return True, f"Authenticated as {arn}"
        except Exception as e:
            return False, f"AWS credentials invalid: {str(e)}"
    
    def get_credential_help_message(self, error_context: Optional[str] = None) -> str:
        """Return generic AWS credential help."""
        return (
            "AWS credentials are not configured or have expired.\n"
            "\n"
            "Please configure AWS credentials using one of these methods:\n"
            "  1. Environment variables:\n"
            "       export AWS_ACCESS_KEY_ID=<your-key>\n"
            "       export AWS_SECRET_ACCESS_KEY=<your-secret>\n"
            "\n"
            "  2. AWS credentials file (~/.aws/credentials):\n"
            "       [default]\n"
            "       aws_access_key_id = <your-key>\n"
            "       aws_secret_access_key = <your-secret>\n"
            "\n"
            "  3. IAM role (if running on EC2/ECS)\n"
        )
    
    def get_session(self, profile_name=None, region=None):
        """Return boto3 session."""
        import boto3
        return boto3.Session(profile_name=profile_name, region_name=region)


class DefaultConfigProvider(ConfigProvider):
    """Default configuration for community edition."""
    
    provider_id = "default"
    priority = 0
    
    def get_defaults(self) -> Dict[str, Any]:
        """Return default configuration."""
        return {
            "aws": {
                "region": "us-west-2"
            },
            "models": {
                "endpoint": "bedrock",
                "default_model": "sonnet4.0",
                "temperature": 0.3
            },
            "mcp": {
                "auto_load": False
            }
        }
    
    def should_apply(self) -> bool:
        """Always apply default config."""
        return True


class DefaultDirectoryScanProvider(DirectoryScanProvider):
    """
    User-configurable scan rules read from ``.ziya/scan.yaml``.

    Supports per-directory depth limits and child include/exclude masks.
    Rules are evaluated in order; the first match wins.

    Example ``.ziya/scan.yaml``::

        scan_rules:
          - match:
              has_file: packageInfo
            include_only: [src, tst, configuration]
            default_depth: 4

          - match:
              has_file: package.json
            exclude: [node_modules, dist, .next, coverage]

          - match:
              name_glob: "build*"
            default_depth: 3
    """

    provider_id = "user-scan-rules"
    priority = 0  # lowest — enterprise plugins override

    def __init__(self):
        self._rules_cache: Dict[str, List[dict]] = {}  # project_root -> rules

    # ── rule loading ────────────────────────────────────────────────

    def _load_rules(self, project_root: str) -> List[dict]:
        if project_root in self._rules_cache:
            return self._rules_cache[project_root]

        rules: List[dict] = []
        scan_yaml = os.path.join(project_root, ".ziya", "scan.yaml")
        if os.path.isfile(scan_yaml):
            try:
                import yaml  # optional dep; graceful if missing
                with open(scan_yaml, "r") as f:
                    data = yaml.safe_load(f) or {}
                rules = data.get("scan_rules", [])
                if rules:
                    logger.info(f"Loaded {len(rules)} scan rules from {scan_yaml}")
            except ImportError:
                logger.debug("PyYAML not installed — .ziya/scan.yaml ignored")
            except Exception as e:
                logger.warning(f"Failed to parse {scan_yaml}: {e}")

        self._rules_cache[project_root] = rules
        return rules

    @staticmethod
    def _find_project_root(dir_path: str) -> Optional[str]:
        """Walk up from *dir_path* looking for ``.ziya/`` or ``.git/``."""
        current = os.path.abspath(dir_path)
        for _ in range(20):  # depth cap
            if os.path.isdir(os.path.join(current, ".ziya")):
                return current
            if os.path.isdir(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        return None

    # ── provider interface ──────────────────────────────────────────

    def customize_scan(
        self, dir_path: str, current_depth: int, global_max_depth: int
    ) -> Optional[ScanCustomization]:
        root = self._find_project_root(dir_path)
        if root is None:
            return None
        rules = self._load_rules(root)
        if not rules:
            return None

        dir_name = os.path.basename(dir_path.rstrip(os.sep))
        for rule in rules:
            match = rule.get("match", {})
            matched = False
            if "has_file" in match:
                matched = os.path.isfile(os.path.join(dir_path, match["has_file"]))
            elif "name" in match:
                matched = dir_name == match["name"]
            elif "name_glob" in match:
                matched = fnmatch.fnmatch(dir_name, match["name_glob"])

            if not matched:
                continue

            inc = rule.get("include_only")
            exc = rule.get("exclude")
            return ScanCustomization(
                default_child_max_depth=(
                    current_depth + rule["default_depth"]
                    if "default_depth" in rule else None
                ),
                child_max_depth_overrides=rule.get("depth_overrides", {}),
                exclude_children=set(exc) if exc else set(),
                include_only_children=set(inc) if inc else None,
            )
        return None
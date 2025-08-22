"""
Single source of truth for shell command configuration.
"""

# SINGLE SOURCE OF TRUTH for shell command configuration
DEFAULT_SHELL_CONFIG = {
    "enabled": True,
    "allowedCommands": [
        "ls", "cat", "pwd", "grep", "wc", "touch", "find", "date", "od", "df", 
        "netstat", "lsof", "ps", "sed", "awk", "cut", "sort", "which", "hexdump", 
        "xxd", "tail", "head", "echo", "printf", "tr", "uniq", "column", "nl", 
        "tee", "base64", "md5sum", "sha1sum", "sha256sum", "bc", "expr", "seq", 
        "paste", "join", "fold", "expand", "cd", "tree", "less", "xargs", "curl", 
        "ping", "du", "file"
    ],
    "gitOperationsEnabled": True,
    "safeGitOperations": [
        "status", "log", "show", "diff", "branch", "remote", "config --get",
        "ls-files", "ls-tree", "blame", "tag", "stash list", "reflog", 
        "rev-parse", "describe", "shortlog", "whatchanged"
    ],
    "timeout": 10
}

def get_default_shell_config():
    """Get the default shell configuration."""
    return DEFAULT_SHELL_CONFIG.copy()

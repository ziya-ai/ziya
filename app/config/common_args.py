"""
Centralized argument definitions for all Ziya entry points.
This ensures consistent defaults across server, CLI, and any other clients.
"""

def add_common_arguments(parser):
    """Add common arguments that are shared between all Ziya clients (server, CLI, etc.)."""
    # Import config for centralized defaults
    import os
    import app.config.models_config as config
    
    # File/path related arguments
    parser.add_argument('--root', type=str, default=None,
                        help='Root directory (default: cwd)')
    parser.add_argument('--include', default=[], type=lambda x: x.split(','),
                        help='Include paths outside root (comma-separated)')
    parser.add_argument('--exclude', default=[], type=lambda x: x.split(','),
                        help='Exclude files/directories (comma-separated)')
    parser.add_argument('--include-only', default=[], type=lambda x: x.split(','),
                        help='Only include specified paths (comma-separated)')
    
    # Model and endpoint configuration
    # Initialize plugins early to get enterprise endpoint policy for --help
    endpoint_help_choices = 'bedrock, google, openai, anthropic'
    if not os.environ.get('ZIYA_ALLOW_ALL_ENDPOINTS'):
        try:
            from app.plugins import initialize as _init_plugins, get_allowed_endpoints
            _init_plugins()
            allowed = get_allowed_endpoints()
            if allowed is not None:
                endpoint_help_choices = ', '.join(allowed)
        except Exception:
            pass
    parser.add_argument('--endpoint', type=str,
                        default=config.DEFAULT_ENDPOINT,
                        help=f'Model endpoint (default: {config.DEFAULT_ENDPOINT}). Available: {endpoint_help_choices}')
    parser.add_argument('--model', '-m', type=str, default=None, 
                        help='Model to use')
    parser.add_argument('--model-id', type=str, default=None,
                        help='Override model ID directly (advanced)')
    
    # AWS configuration
    parser.add_argument('--profile', type=str, default=None, help='AWS profile')
    parser.add_argument('--region', type=str, default=None,
                        help=f'AWS region (default: {config.DEFAULT_REGION}). '
                             f'If omitted, model-specific defaults may apply.')
    
    # Model parameters
    parser.add_argument('--temperature', type=float, default=None,
                        help='Temperature for generation')
    parser.add_argument('--top-p', type=float, default=None,
                        help='Top-p sampling (if supported)')
    parser.add_argument('--top-k', type=int, default=None,
                        help='Top-k sampling (if supported)')
    parser.add_argument('--max-output-tokens', type=int, default=None,
                        help='Max tokens to generate')
    parser.add_argument('--thinking-level', type=str, choices=['low', 'medium', 'high'], default=None,
                        help='Thinking level (Gemini 3)')
    
    # Behavior flags
    parser.add_argument('--no-stream', action='store_true', help='Disable streaming output')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')

    # Experimental features
    parser.add_argument('--memory', action='store_true',
                        help='Enable persistent memory across sessions (experimental, disabled by default)')

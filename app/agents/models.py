from typing import Optional, Dict, Any, Union, List
import os
import json
import hashlib
import botocore
import boto3
import gc
from pathlib import Path
from langchain_aws import ChatBedrock
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseChatModel
from langchain_classic.callbacks.base import BaseCallbackHandler
from app.utils.logging_utils import logger
import app.config.models_config as config
from app.config.models_config import get_supported_parameters  # Import the function explicitly
import google.auth
import google.auth.exceptions
from dotenv import load_dotenv
from dotenv.main import find_dotenv
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException
from app.agents.callbacks import EmptyMessageFilter

# Load environment variables from .env at the module level
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=True)
    logger.info(f"MODELS.PY: Loaded environment variables from {dotenv_path}")

from langchain_classic.agents import AgentExecutor
from langchain_classic.agents.format_scratchpad import format_xml
from langchain_core.messages import HumanMessage

# Import configuration from the central config module
import app.config.models_config as config

class ModelManager:
    """Manages model initialization and configuration."""
    
    def __init__(self):
        """Initialize the model manager."""
        # Load environment variables
        load_dotenv(find_dotenv())
        
        # Initialize state
        self._endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        self._model = os.environ.get("ZIYA_MODEL", config.DEFAULT_MODELS[self._endpoint])
        self._model_id_override = os.environ.get("ZIYA_MODEL_ID_OVERRIDE")
        self._llm = None
        
        # Initialize model parameters
        self._temperature = float(os.environ.get("ZIYA_TEMPERATURE", 0.3))
        self._top_p = float(os.environ.get("ZIYA_TOP_P", 0.9))
        self._top_k = int(os.environ.get("ZIYA_TOP_K", 40))
        self._max_output_tokens = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
        
        # Initialize the LLM
        self._initialize_llm()
    
    def get_model_config(self):
        """Get the current model configuration."""
        model_config = config.MODEL_CONFIGS[self._endpoint][self._model].copy()
        
        # Override model ID if specified
        if self._model_id_override:
            model_config["model_id"] = self._model_id_override
        
        # Update parameters
        model_config.update({
            "temperature": self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "max_output_tokens": self._max_output_tokens
        })
        
        return model_config

class ModelManager:
    """
    Manages model configuration and initialization with a clear inheritance hierarchy:
    1. Global defaults apply to all models
    2. Endpoint defaults override globals for all models of that endpoint
    3. Model-specific configs override endpoint defaults
    """

    # Class-level state with process-specific initialization
    _state = {
        'model': None,
        'persistent_bedrock_clients': {},  # Store clients by config hash
        'client_config_hash': None,       # Track current client config
        'auth_checked': False,
        'auth_success': False,
        'google_credentials': None,
        'current_model_id': None,
        'aws_profile': None,
        'aws_region': None,
        'process_id': None,
        'llm_with_stop': None,
        'agent': None,
        'agent_executor': None,
        'last_auth_error': None,  # Add this to store the last authentication error
        # Model kwargs caching to eliminate redundant filtering
        'filtered_kwargs_cache': {},  # Cache by (model_config_hash, kwargs_hash)
        # Agent chain caching to eliminate redundant agent creation
        'agent_chain_cache': {},  # Cache by (model_id, ast_enabled, mcp_enabled)
    }
    
    @classmethod
    def _reset_state(cls):
        """
        Reset the internal state of the ModelManager.
        This is used when switching models to ensure a clean initialization.
        """
        import gc
        import sys
        
        # Force garbage collection before resetting state
        gc.collect()
        
        logger.info("Resetting ModelManager state")
        
        # Clear AWS region environment variable to ensure clean region selection
        if 'AWS_REGION' in os.environ:
            del os.environ['AWS_REGION']
            logger.info("Cleared AWS_REGION environment variable")
        
        cls._state = {
            'model': None,
            'current_model_id': None,
            'persistent_bedrock_clients': {},
            'client_config_hash': None,
            'auth_checked': False,
            'auth_success': False,
            'google_credentials': None,
            'aws_profile': None,
            'aws_region': None,
            'process_id': os.getpid(),
            'llm_with_stop': None,
            'agent': None,
            'agent_executor': None,
            'last_auth_error': None,
            'filtered_kwargs_cache': {},  # Reset cache on state reset
            'agent_chain_cache': {},  # Reset agent chain cache on state reset
        }
        
        # Reset boto3 in a safer way
        try:
            import boto3
            import botocore
            
            # Instead of trying to modify the existing session, we'll create a completely new one
            # First, remove boto3 and botocore modules from sys.modules to force a complete reload
            modules_to_remove = []
            for module_name in sys.modules:
                if module_name.startswith('boto3.') or module_name.startswith('botocore.'):
                    modules_to_remove.append(module_name)
            
            for module_name in modules_to_remove:
                try:
                    del sys.modules[module_name]
                    logger.debug(f"Removed module from sys.modules: {module_name}")
                except KeyError:
                    pass
            
            # Reload boto3 and botocore
            import importlib
            importlib.reload(botocore)
            importlib.reload(boto3)
            
            # Create a new session
            boto3.DEFAULT_SESSION = None
            
            logger.info("Successfully reset boto3 session")
        except Exception as e:
            logger.warning(f"Error resetting boto3 session: {e}")
        
        logger.info("ModelManager state reset complete")
        
    @classmethod
    def invalidate_kwargs_cache(cls):
        """Invalidate the model kwargs cache to force fresh filtering."""
        cls._state['filtered_kwargs_cache'] = {}
        logger.info("ModelManager: Filtered kwargs cache invalidated")
    
    @classmethod
    def invalidate_agent_chain_cache(cls):
        """Invalidate the agent chain cache to force fresh agent creation."""
        cls._state['agent_chain_cache'] = {}
        logger.info("ModelManager: Agent chain cache invalidated")
    
    @classmethod
    def get_state(cls):
        """Get the current ModelManager state."""
        return cls._state.copy()  # Return a copy to prevent external modification
            
        # Force garbage collection again to clean up any lingering references
        gc.collect()
        
        # Invalidate prompt extension cache
        try:
            from app.agents.prompts_manager import invalidate_prompt_cache
            invalidate_prompt_cache()
        except ImportError:
            logger.debug("Could not invalidate prompt cache - module not available")
        
        logger.info("Model state completely reset")
        # Force garbage collection again after resetting state
        gc.collect()
        
        return cls._state

    # Use the configuration from config.py
    DEFAULT_ENDPOINT = config.DEFAULT_ENDPOINT
    DEFAULT_MODELS = config.DEFAULT_MODELS
    GLOBAL_DEFAULTS = config.GLOBAL_MODEL_DEFAULTS
    ENDPOINT_DEFAULTS = config.ENDPOINT_DEFAULTS
    MODEL_CONFIGS = config.MODEL_CONFIGS
    ENV_VAR_MAPPING = config.ENV_VAR_MAPPING

    @classmethod
    def get_model_config(cls, endpoint: str, model_name: str = None) -> dict:
        """
        Get complete model configuration with proper inheritance:
        1. Start with global defaults
        2. Override with endpoint defaults
        3. Override with model-specific config
        4. Apply family-specific settings if available
        
        Args:
            endpoint: The endpoint name (e.g., "bedrock", "google")
            model_name: The model name/alias (e.g., "sonnet3.5-v2", "gemini-pro")
            
        Returns:
            dict: Complete model configuration with all inherited properties
        """
        # Validate endpoint
        if endpoint not in cls.MODEL_CONFIGS:
            valid_endpoints = ", ".join(cls.MODEL_CONFIGS.keys())
            raise ValueError(f"Invalid endpoint: '{endpoint}'. Valid endpoints are: {valid_endpoints}")
            
        # Handle case where model_name is None
        if model_name is None:
            model_name = cls.DEFAULT_MODELS.get(endpoint, cls.DEFAULT_MODELS[cls.DEFAULT_ENDPOINT])
            
        # Validate model name
        if model_name not in cls.MODEL_CONFIGS[endpoint]:
            valid_models = ", ".join(cls.MODEL_CONFIGS[endpoint].keys())
            raise ValueError(f"Invalid model: '{model_name}' for endpoint '{endpoint}'. Valid models are: {valid_models}")
            
        # Start with global defaults
        config_dict = cls.GLOBAL_DEFAULTS.copy()
        
        # Apply endpoint defaults if they exist
        if endpoint in cls.ENDPOINT_DEFAULTS:
            config_dict.update(cls.ENDPOINT_DEFAULTS[endpoint])
        
        # Get the model-specific config
        model_specific_config = cls.MODEL_CONFIGS[endpoint][model_name].copy()
        
        # Check if the model belongs to a family
        family = model_specific_config.get("family")
        if family and hasattr(cls, "MODEL_FAMILIES") and family in cls.MODEL_FAMILIES:
            # Apply family-specific settings
            family_config = cls.MODEL_FAMILIES[family].copy()
            
            # Don't override parameter_ranges yet
            parameter_ranges = family_config.pop("parameter_ranges", {})
            
            # Apply other family settings
            config_dict.update(family_config)
            
            # Apply parameter ranges
            if "parameter_ranges" not in config_dict:
                config_dict["parameter_ranges"] = {}
            
            for param, range_info in parameter_ranges.items():
                config_dict["parameter_ranges"][param] = range_info.copy()
        
        # Apply model-specific config
        config_dict.update(model_specific_config)
            
        # Add name for reference
        config_dict["name"] = model_name
            
        return config_dict
        
    @classmethod
    def get_model_id(cls, model_instance=None):
        """
        Get the model ID for the current model.
        
        Args:
            model_instance: Optional model instance to get ID from
            
        Returns:
            str or dict: The model ID (can be a string or a region-specific dictionary)
        """
        # If we have a current model ID in state, return it
        if cls._state.get('current_model_id'):
            return cls._state['current_model_id']
            
        # Otherwise, try to get it from environment variables
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", config.DEFAULT_MODELS.get(endpoint))
        
        # Get model configuration
        try:
            model_config = cls.get_model_config(endpoint, model_name)
            return model_config.get("model_id", model_name)
        except ValueError:
            # If validation fails, return the model name as fallback
            return model_name
            
    @classmethod
    def get_model_alias(cls):
        """
        Get the model alias (name) for the current model.
        
        Returns:
            str: The model alias (like "sonnet3.7")
        """
        # Get the model alias from environment variable
        return os.environ.get("ZIYA_MODEL", config.DEFAULT_MODELS.get(
            os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        ))
            
    @classmethod
    def filter_model_kwargs(cls, model_kwargs, model_config):
        """
        Filter model kwargs to only include parameters supported by the model with caching.
        
        Args:
            model_kwargs: Dict of model kwargs to filter
            model_config: Model configuration dict
        """
        # Check if we're repeatedly processing the same kwargs
        if hasattr(cls, '_last_filter_log') and cls._last_filter_log == (model_kwargs, model_config):
            # Use existing cache logic instead of calling non-existent method
            model_config_str = json.dumps(model_config, sort_keys=True)
            model_config_hash = hashlib.md5(model_config_str.encode()).hexdigest()[:8]
            
            kwargs_str = json.dumps(model_kwargs, sort_keys=True)
            kwargs_hash = hashlib.md5(kwargs_str.encode()).hexdigest()[:8]
            
            cache_key = f"{model_config_hash}_{kwargs_hash}"
            
            if cache_key in cls._state['filtered_kwargs_cache']:
                return cls._state['filtered_kwargs_cache'][cache_key]
        
        # Create cache keys
        model_config_str = json.dumps(model_config, sort_keys=True)
        model_config_hash = hashlib.md5(model_config_str.encode()).hexdigest()[:8]
        
        kwargs_str = json.dumps(model_kwargs, sort_keys=True)
        kwargs_hash = hashlib.md5(kwargs_str.encode()).hexdigest()[:8]
        
        cache_key = f"{model_config_hash}_{kwargs_hash}"
        
        # Check cache
        if cache_key in cls._state['filtered_kwargs_cache']:
            logger.debug(f"Using cached filtered kwargs for {cache_key}")
            return cls._state['filtered_kwargs_cache'][cache_key]
        
        # Only log first time, then use debug level
        logger.debug(f"Computing model kwargs for {model_config.get('name', 'unknown')}: {model_kwargs}")
        
        # Get supported parameters from the model config
        supported_params = []
        
        # Get parameters from model config
        model_params = model_config.get('supported_parameters', [])
        if model_params:
            supported_params.extend(model_params)
        
        # If model has a family, get parameters from family
        if 'family' in model_config:
            family_name = model_config['family']
            if family_name in config.MODEL_FAMILIES:
                family_config = config.MODEL_FAMILIES[family_name]
                
                # Get parameters from family
                family_params = family_config.get('supported_parameters', [])
                if family_params:
                    supported_params.extend(family_params)
                    
                # Check for parent family parameters
                if 'parent' in family_config and family_config['parent'] in config.MODEL_FAMILIES:
                    parent_family = config.MODEL_FAMILIES[family_config['parent']]
                    parent_params = parent_family.get('supported_parameters', [])
                    if parent_params:
                        supported_params.extend(parent_params)
        
        # If still no parameters, try to get from endpoint defaults
        if not supported_params:
            endpoint = model_config.get('endpoint', cls.DEFAULT_ENDPOINT)
            if endpoint in cls.ENDPOINT_DEFAULTS:
                endpoint_params = cls.ENDPOINT_DEFAULTS[endpoint].get('supported_parameters', [])
                if endpoint_params:
                    supported_params.extend(endpoint_params)
        
        # Remove duplicates while preserving order
        supported_params = list(dict.fromkeys(supported_params))
        
        # Check for model-specific support for max_input_tokens
        supports_max_input_tokens = model_config.get('supports_max_input_tokens', False)
        if supports_max_input_tokens and 'max_input_tokens' not in supported_params:
            supported_params.append('max_input_tokens')
        elif not supports_max_input_tokens and 'max_input_tokens' in supported_params:
            supported_params.remove('max_input_tokens')
        
        # Only log this once per process, not every time it's called
        logger.debug(f"Supported parameters for model: {supported_params}")
        
        # Filter the kwargs
        filtered_kwargs = {}
        for key, value in model_kwargs.items():
            if key in supported_params and value is not None:
                filtered_kwargs[key] = value
            else:
                logger.debug(f"Ignoring unsupported parameter '{key}' for model")
        
        # Cache the result
        cls._state['filtered_kwargs_cache'][cache_key] = filtered_kwargs
        logger.debug(f"Cached filtered kwargs for {cache_key}")
        cls._last_filter_log = (model_kwargs, model_config)
        
        return filtered_kwargs
            
    @classmethod
    def get_model_settings(cls, config_or_model=None) -> Dict[str, Any]:
        """
        Get model settings from environment variables or config.
        Handles parameter mapping and type conversion automatically.
        
        Args:
            config_or_model: Either a config dict, model instance, or None
            
        Returns:
            dict: Model settings with proper parameter mappings and filtering
        """
        # Determine config source and get model configuration
        if hasattr(config_or_model, 'model_id'):
            # Model instance provided
            endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
            model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
            model_config = cls.get_model_config(endpoint, model_name)
        elif isinstance(config_or_model, dict):
            # Config dict provided
            model_config = config_or_model
        else:
            # No specific config provided, use environment variables
            endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
            model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
            model_config = cls.get_model_config(endpoint, model_name)
        
        try:
            # Start with base settings from model config
            settings = {}
            
            # Copy all relevant settings from model_config (excluding metadata)
            for key, value in model_config.items():
                if key not in ["name", "model_id", "parameter_mappings"]:
                    settings[key] = value
            
            # Ensure token_limit is included in settings
            if "token_limit" in model_config:
                settings["token_limit"] = model_config["token_limit"]
                logger.info(f"Including token_limit in settings: {model_config['token_limit']}")
            
            # Apply environment variable overrides with proper type conversion
            for env_var, config_key in config.ENV_VAR_MAPPING.items():
                if env_var in os.environ:
                    value = os.environ[env_var]
                    
                    # Convert to appropriate type based on existing value or config
                    if config_key in model_config:
                        if isinstance(model_config[config_key], bool):
                            value = value.lower() in ('true', 'yes', '1', 't', 'y')
                        elif isinstance(model_config[config_key], int):
                            value = int(value)
                        elif isinstance(model_config[config_key], float):
                            value = float(value)
                    elif isinstance(settings.get(config_key), bool):
                        value = value.lower() in ("true", "1", "yes")
                    elif isinstance(settings.get(config_key), int):
                        settings[config_key] = int(value)
                    elif isinstance(settings.get(config_key), float):
                        settings[config_key] = float(value)
                    
                    settings[config_key] = value
            
            # Add thinking_mode from environment if it exists
            if "ZIYA_THINKING_MODE" in os.environ:
                settings["thinking_mode"] = os.environ["ZIYA_THINKING_MODE"] == "1"
            elif "supports_thinking" in model_config:
                settings["thinking_mode"] = model_config["supports_thinking"]

            # Handle max_output_tokens - use environment, then default value, then maximum value
            if "ZIYA_MAX_OUTPUT_TOKENS" in os.environ:
                settings["max_output_tokens"] = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])
            elif "default_max_output_tokens" in model_config:
                settings["max_output_tokens"] = model_config["default_max_output_tokens"]
            elif "default_max_output_tokens" in cls.ENDPOINT_DEFAULTS.get(endpoint, {}):
                settings["max_output_tokens"] = cls.ENDPOINT_DEFAULTS[endpoint]["default_max_output_tokens"]
            else:
                # Fall back to maximum value
                settings["max_output_tokens"] = model_config.get("max_output_tokens", 4096)
            
            # Get supported parameters for filtering
            supported_params = []
            model_params = model_config.get('supported_parameters', [])
            if model_params:
                supported_params.extend(model_params)
            
            # If model has a family, get parameters from family hierarchy
            if 'family' in model_config:
                family_name = model_config['family']
                if family_name in config.MODEL_FAMILIES:
                    family_config = config.MODEL_FAMILIES[family_name]
                    
                    # Get parameters from family
                    family_params = family_config.get('supported_parameters', [])
                    if family_params:
                        supported_params.extend(family_params)
                        
                    # Check for parent family parameters
                    if 'parent' in family_config and family_config['parent'] in config.MODEL_FAMILIES:
                        parent_family = config.MODEL_FAMILIES[family_config['parent']]
                        parent_params = parent_family.get('supported_parameters', [])
                        if parent_params:
                            supported_params.extend(parent_params)
            
            # Filter settings to only include supported parameters
            filtered_settings = {}
            for key, value in settings.items():
                if key in supported_params or key not in ['top_k', 'top_p']:  # Always include non-model parameters
                    filtered_settings[key] = value
            
            # Apply parameter mappings
            parameter_mappings = model_config.get("parameter_mappings", {})
            for source_param, target_params in parameter_mappings.items():
                if source_param in filtered_settings:
                    for target_param in target_params:
                        if target_param != source_param:  # Avoid duplicate assignments
                            filtered_settings[target_param] = filtered_settings[source_param]
            
            return filtered_settings
        except ValueError:
            # If validation fails, return default settings
            logger.warning("Failed to get model config, returning default settings")
            return {
                'temperature': float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
                'max_tokens': int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096)),
                'top_k': int(os.environ.get("ZIYA_TOP_K", 15)),
                'thinking_mode': os.environ.get("ZIYA_THINKING_MODE") == "1"
            }

    @classmethod
    def _get_client_config_hash(cls, aws_profile: str, region: str, model_id: str) -> str:
        """Generate a hash for the client configuration to track when clients can be reused."""
        import hashlib
        config_string = f"{aws_profile}_{region}_{model_id}"
        return hashlib.md5(config_string.encode()).hexdigest()[:8]
    
    @classmethod
    def _get_persistent_bedrock_client(cls, aws_profile: str, region: str, model_id: str, model_config: Optional[Dict[str, Any]] = None):
        """
        Get or create a persistent Bedrock client for the given configuration.
        Reuses existing clients when configuration matches.
        """
        from app.utils.aws_utils import ThrottleSafeBedrock, check_aws_credentials, create_fresh_boto3_session
        from app.utils.custom_bedrock import CustomBedrockClient
        
        # Generate configuration hash
        config_hash = cls._get_client_config_hash(aws_profile, region, model_id)
        
        # Check if we can reuse existing client
        if config_hash in cls._state['persistent_bedrock_clients']:
            logger.info(f"Reusing persistent Bedrock client for {aws_profile}/{region}/{model_id}")
            return cls._state['persistent_bedrock_clients'][config_hash]
        
        # Create new client
        logger.info(f"Creating new persistent Bedrock client for {aws_profile}/{region}/{model_id}")
        
        # Create fresh boto3 session and client
        try:
            session = create_fresh_boto3_session(profile_name=aws_profile)
            
            # Check AWS credentials using the same fresh session
            try:
                sts = session.client('sts', region_name=region)
                identity = sts.get_caller_identity()
                logger.debug(f"Successfully authenticated as: {identity.get('Arn', 'Unknown')}")
            except Exception as cred_error:
                error_msg = f"AWS credentials check failed: {cred_error}"
                logger.error(error_msg)
                cls._state['last_auth_error'] = error_msg
                from app.utils.custom_exceptions import KnownCredentialException
                raise KnownCredentialException(error_msg, is_server_startup=False)
            
            bedrock_client = session.client('bedrock-runtime', region_name=region)
            logger.info(f"Created fresh bedrock client with profile {aws_profile} and region {region}")
            
            # Test the client to ensure it's working properly
            try:
                _ = bedrock_client.meta.region_name
                logger.info("Bedrock client validation successful")
            except (AttributeError, RecursionError) as e:
                logger.debug(f"Client validation failed, creating fallback: {e}")
                # Force recreation without session profile if needed
                bedrock_client = boto3.client('bedrock-runtime', region_name=region)
            
            # Wrap with CustomBedrockClient and ThrottleSafeBedrock
            custom_client = CustomBedrockClient(bedrock_client, model_config=model_config)
            throttle_safe_client = ThrottleSafeBedrock(custom_client)
            
            # Store in persistent cache
            cls._state['persistent_bedrock_clients'][config_hash] = throttle_safe_client
            cls._state['client_config_hash'] = config_hash
            
            return throttle_safe_client
            
        except Exception as e:
            logger.error(f"Error creating persistent bedrock client: {e}")
            raise
    
    @classmethod
    def initialize_model(cls, force_reinit=False, settings_override: Optional[Dict[str, Any]] = None) -> BaseChatModel:
        """
        Initialize the model based on environment variables.
        
        Args:
            force_reinit: Force reinitialization even if already initialized
            settings_override: Optional dictionary of settings to apply directly, bypassing env vars for this init.
            
        Returns:
            BaseChatModel: The initialized model
        """
        import os
        import gc
        
        # Force garbage collection before initialization
        gc.collect()
        
        # Check if we're in a child process and should skip initialization
        if os.environ.get("UVICORN_NO_MODEL_INIT") == "true" and not force_reinit:
            # This is a child process and we should skip initialization
            # The model will be initialized when actually needed by LazyLoadedModel
            logger.info("Skipping model initialization in child process - will initialize on demand")
            # Return None - the LazyLoadedModel will handle initialization when needed
            return cls._state['model']  # This might be None, but that's handled by LazyLoadedModel
        
        # Check if we need to reinitialize
        current_pid = os.getpid()
        
        # Check if auth has already been performed in the parent process
        auth_already_checked = os.environ.get("ZIYA_AUTH_CHECKED") == "true"
        parent_auth_complete = os.environ.get("ZIYA_PARENT_AUTH_COMPLETE") == "true"
        
        if (cls._state['model'] is not None and 
            cls._state['process_id'] == current_pid and 
            not force_reinit):
            logger.info(f"Using existing model: {cls._state['current_model_id']}")
            return cls._state['model']
            
        if cls._state['process_id'] != current_pid:
            logger.info("New process detected, resetting state")
            cls._state['model'] = None
            cls._state['auth_checked'] = False
            cls._state['auth_success'] = False
            
            # If auth was already checked in parent process, we can skip the full initialization
            # but we still need to initialize the model
            if auth_already_checked and parent_auth_complete:
                logger.info("Auth already completed in parent process")
            
        if force_reinit:
            logger.info("Force reinitialization requested")
            cls._state['model'] = None
            
        # Get endpoint and model from environment variables
        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
        
        logger.info(f"Initializing for endpoint: {endpoint}, model: {model_name}")
        
        # Get model configuration
        model_config = cls.get_model_config(endpoint, model_name)
            
        # Get default max_output_tokens from model config or endpoint defaults
        default_max_tokens = model_config.get("default_max_output_tokens")
        if default_max_tokens is None:
            # Fall back to endpoint defaults
            if endpoint in cls.ENDPOINT_DEFAULTS:
                default_max_tokens = cls.ENDPOINT_DEFAULTS[endpoint].get("default_max_output_tokens")
        
        # Check for environment variable overrides and apply them directly
        env_max_tokens = os.environ.get("ZIYA_MAX_OUTPUT_TOKENS")
        if env_max_tokens:
            model_config["max_output_tokens"] = int(env_max_tokens)

        model_id = model_config.get("model_id", model_name)
        
        # Reset any previous error flags
        KnownCredentialException._error_displayed = False
        
        # Initialize the model based on the endpoint - only authenticate with the specific endpoint
        logger.info(f"Starting authentication flow for endpoint: {endpoint}")
        try:
            if endpoint == "bedrock":
                logger.info("Using Bedrock authentication flow only")
                model = cls._initialize_bedrock_model(model_config, model_name, settings_override=settings_override)
            elif endpoint == "google":
                logger.info("Using Google authentication flow only")
                model = cls._initialize_google_model(model_config)
            else:
                raise ValueError(f"Unsupported endpoint: {endpoint}")
                
            # Update state for successful initialization
            cls._state['model'] = model
            cls._state['current_model_id'] = model_id
            cls._state['process_id'] = current_pid
            cls._state['auth_checked'] = True
            cls._state['auth_success'] = True
        except KnownCredentialException as e:
            # Handle credential exception without terminating the server
            logger.warning(f"Authentication failed but continuing server operation: {str(e)}")
            
            # Update state to indicate authentication failure
            cls._state['model'] = None
            cls._state['auth_checked'] = True
            cls._state['auth_success'] = False
            cls._state['last_auth_error'] = str(e)
            
            # Set environment variable to indicate auth was attempted but failed
            os.environ["ZIYA_AUTH_CHECKED"] = "true"
            os.environ["ZIYA_AUTH_FAILED"] = "true"
            
            # Return None - the server will continue running but model operations will fail
            # with appropriate error messages
            return None
        
        # Add region to state if available
        if "region" in model_config:
            cls._state['aws_region'] = model_config["region"]
            
        logger.debug(f"Model initialization complete. New state: {cls._state}")
        
        return model
    
    @classmethod
    def _get_region_specific_model_id_with_region_update(cls, model_id, region, model_config=None, model_name=None):
        """
        Get the appropriate model ID and updated region based on model availability.
        
        Args:
            model_id: The model ID configuration (string or dict)
            region: The AWS region being used
            model_config: Optional model configuration dict for region preferences
            
        Returns:
            Tuple[str, str]: The region-specific model ID and the updated region
        """
        # If model_id is a string, use it directly
        if isinstance(model_id, str):
            return model_id, region
            
        # If model_id is a dict with region-specific IDs
        if isinstance(model_id, dict):
            # Determine region prefix (eu or us)
            logger.debug(f"Processing region-specific model_id: {model_id}")
            logger.debug(f"Current region: {region}")
            logger.debug(f"Available regions in model_id: {list(model_id.keys())}")
            
            region_prefix = "eu" if region.startswith("eu-") else "us"
            
            # Log the region and prefix for debugging
            logger.debug(f"Selecting model ID for region {region} (prefix: {region_prefix})")
            
            # Return the region-specific ID if available
            if region_prefix in model_id and model_id[region_prefix]:
                logger.debug(f"Using {region_prefix} specific model ID for region {region}")
                return model_id[region_prefix], region
            else:
                # Model not available in current region
                is_region_restricted = model_config.get("region_restricted", False) if model_config else False
                available_regions = model_config.get("available_regions", []) if model_config else []
                
                if is_region_restricted:
                    # Model is truly restricted to specific regions - must switch
                    if region_prefix == "eu" and "us" in model_id:
                        # Current region is EU but model only available in US - switch to US region
                        preferred_region = model_config.get("preferred_region", "us-west-2") if model_config else "us-west-2"
                        logger.warning(f"Model {model_name} is only available in US regions. Switching from {region} to {preferred_region}")
                        os.environ["AWS_REGION"] = preferred_region
                        return model_id["us"], preferred_region
                    elif region_prefix == "us" and "eu" in model_id:
                        # Switch to EU region
                        preferred_region = model_config.get("preferred_region", "eu-west-1") if model_config else "eu-west-1"
                        logger.warning(f"Model {model_name} is only available in EU regions. Switching from {region} to {preferred_region}")
                        os.environ["AWS_REGION"] = preferred_region
                        return model_id["eu"], preferred_region
                else:
                    # Model has regional preferences but can work in other regions
                    if region in available_regions:
                        # Current region is actually supported, use appropriate model ID
                        logger.info(f"Using model {model_name} in region {region}")
                        if region_prefix == "eu" and "us" in model_id:
                            return model_id["us"], region  # Use US model ID but stay in EU region
                        elif region_prefix == "us" and "eu" in model_id:
                            return model_id["eu"], region  # Use EU model ID but stay in US region
                    else:
                        # Region not supported, inform user but don't force switch
                        logger.warning(f"Model {model_name} may not be available in region {region}. Consider using one of: {', '.join(available_regions[:5])}")
                
                # Use fallback
                fallback_id = next(iter(model_id.values()))
                logger.info(f"Using fallback model ID: {fallback_id}")
                return fallback_id, region
                    
        # Fallback for unexpected cases
        logger.warning(f"Unexpected model_id format: {model_id}, returning as is")
        return model_id, region
    
    @classmethod
    def _get_region_specific_model_id(cls, model_id, region):
        """
        Get the appropriate model ID based on the region.
        
        Args:
            model_id: The model ID configuration (string or dict)
            region: The AWS region being used
            
        Returns:
            str: The region-specific model ID
        """
        # If model_id is a string, use it directly
        if isinstance(model_id, str):
            return model_id
            
        # If model_id is a dict with region-specific IDs
        if isinstance(model_id, dict):
            # Determine region prefix (eu or us)
            region_prefix = "eu" if region.startswith("eu-") else "us"
            
            # Log the region and prefix for debugging
            logger.info(f"Selecting model ID for region {region} (prefix: {region_prefix})")
            
            # Return the region-specific ID if available, otherwise fall back to default
            if region_prefix in model_id:
                logger.info(f"Using {region_prefix} specific model ID for region {region}")
                return model_id[region_prefix]
            else:
                # Fall back to the first available ID
                fallback_id = next(iter(model_id.values()))
                logger.info(f"No matching region found for {region_prefix}, using fallback: {fallback_id}")
                return fallback_id
                
        # Fallback for unexpected cases
        logger.warning(f"Unexpected model_id format: {model_id}, returning as is")
        return model_id
    
    @classmethod
    def _initialize_bedrock_model(cls, model_config: Dict[str, Any], model_name: str = None, settings_override: Optional[Dict[str, Any]] = None) -> BaseChatModel: # Add settings_override parameter

        """
        Initialize a Bedrock model with the given configuration.
        
        Args:
            model_config: Model configuration
            
        Returns:
            ChatBedrock: The initialized Bedrock model
        """
        from app.utils.aws_utils import ThrottleSafeBedrock, check_aws_credentials, create_fresh_boto3_session
        from app.utils.custom_exceptions import KnownCredentialException
        
        # Force garbage collection before creating new boto3 clients
        gc.collect()
        
        logger.info("Initializing Bedrock model")
        
        # Get AWS profile from environment or config
        aws_profile = os.environ.get("ZIYA_AWS_PROFILE") or model_config.get("profile")
        if aws_profile:
            logger.info(f"Using AWS profile: {aws_profile}")
            os.environ["AWS_PROFILE"] = aws_profile
            cls._state['aws_profile'] = aws_profile
        else:
            logger.info("Using default AWS credentials")
            os.environ["AWS_PROFILE"] = "default"
            
        # Get region from environment first, then config
        region = os.environ.get("AWS_REGION") or model_config.get("region", "us-west-2")
        logger.info(f"Using AWS region: {region}")
        cls._state['aws_region'] = region
        # Get model ID with region-specific handling - THIS IS WHERE THE REGION GETS UPDATED
        raw_model_id = model_config.get("model_id")
        model_id, updated_region = cls._get_region_specific_model_id_with_region_update(raw_model_id, region, model_config, model_name)
        
        # Update the environment variable with the new region
        if updated_region != region:
            os.environ["AWS_REGION"] = updated_region
        
        # Use the updated region if it was changed
        if updated_region != region:
            region = updated_region
            logger.info(f"Region updated to: {region}")
            cls._state['aws_region'] = region
        
        logger.info(f"Selected model_id: {model_id} for region: {region}")
        
        # Check for model ID override from environment
        model_id_override = os.environ.get("ZIYA_MODEL_ID_OVERRIDE")
        if model_id_override:
            logger.info(f"Using model ID override: {model_id_override} instead of {model_id}")
            model_id = model_id_override
            
        # Reset any previous error flags
        KnownCredentialException._error_displayed = False
        
        # Get persistent Bedrock client (handles credential checking internally)
        persistent_client = cls._get_persistent_bedrock_client(aws_profile, region, model_id, model_config)
        
        # Check if this is a Nova model
        family = model_config.get("family")
        # Also check for wrapper_class directly in config
        wrapper_class = model_config.get("wrapper_class")
        
        logger.info(f"Model family: {family}, wrapper_class: {wrapper_class}")
        
        # --- Determine Effective Parameters RIGHT BEFORE Initialization ---
        # Get base config values again for clarity
        base_temperature = model_config.get("temperature", 0.3)
        base_top_k = model_config.get("top_k", 15)
        base_max_tokens = model_config.get("max_output_tokens", 4096)
        base_top_p = model_config.get("top_p")
        base_thinking_mode = model_config.get("thinking_mode", config.GLOBAL_MODEL_DEFAULTS.get("thinking_mode", False))

        # Check for default_max_output_tokens in model config
        default_max_tokens = model_config.get("default_max_output_tokens")
        if default_max_tokens is not None:
            # If no override is set in environment, use the default value
            if "ZIYA_MAX_OUTPUT_TOKENS" not in os.environ and "ZIYA_MAX_TOKENS" not in os.environ:
                logger.info(f"Using default_max_output_tokens from config: {default_max_tokens}")
                # Set the environment variable for consistency
                os.environ["ZIYA_MAX_OUTPUT_TOKENS"] = str(default_max_tokens)
                # Update base_max_tokens to use the default
                base_max_tokens = default_max_tokens
        
        if settings_override and isinstance(settings_override, dict):
            logger.debug("Using settings_override for initialization parameters.")
            logger.debug(f"  settings_override received: {settings_override}")

            # Directly use settings_override, falling back to base config only if key is missing in override
            effective_temperature = float(settings_override.get("temperature", base_temperature))
            effective_top_k = int(settings_override.get("top_k", base_top_k))
            effective_max_tokens = int(settings_override.get("max_output_tokens", base_max_tokens))
            effective_top_p = settings_override.get("top_p", base_top_p)
            effective_thinking_mode = bool(settings_override.get("thinking_mode", base_thinking_mode))
            logger.debug(f"effective_max_tokens assigned value: {effective_max_tokens}")
        else:
            logger.debug("Using environment variables (or defaults) for initialization parameters.")
            # Fall back to base config values if environment variable is not set
            effective_temperature = float(os.environ.get("ZIYA_TEMPERATURE", base_temperature))
            effective_top_k = int(os.environ.get("ZIYA_TOP_K", base_top_k))
            effective_max_tokens = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", base_max_tokens))
            effective_top_p_str = os.environ.get("ZIYA_TOP_P")
            effective_top_p = base_top_p # Default to base
            if effective_top_p_str is not None:
                try:
                    effective_top_p = float(effective_top_p_str)
                except ValueError:
                    logger.warning(f"Invalid ZIYA_TOP_P value '{effective_top_p_str}', using default.")
                    effective_top_p = base_top_p
            
            effective_thinking_mode_str = os.environ.get("ZIYA_THINKING_MODE")
            effective_thinking_mode = base_thinking_mode
            if effective_thinking_mode_str is not None:
                effective_thinking_mode = effective_thinking_mode_str.lower() in ("true", "1", "yes", "t", "y")
            if effective_thinking_mode_str is not None:
                effective_thinking_mode = effective_thinking_mode_str.lower() in ("true", "1", "yes")

        # Create the appropriate model based on family
        if family in ["nova", "nova-pro", "nova-lite", "nova-premier"]:
            from app.agents.wrappers.nova_wrapper import NovaBedrock
            logger.info(f"Initializing Nova model: {model_id}")
            
            # Create a model_kwargs dictionary with all parameters
            nova_model_kwargs = {
                "top_p": effective_top_p,
                "max_tokens": effective_max_tokens # Explicitly include effective max_tokens
            }
            
            # Add temperature only if supported by the model
            model_alias = os.environ.get("ZIYA_MODEL", "")  # Get the model alias from environment
            supported_params = get_supported_parameters("bedrock", model_alias)
            if "temperature" in supported_params and effective_temperature is not None:
                nova_model_kwargs["temperature"] = effective_temperature
                logger.info(f"Adding temperature={effective_temperature} to nova_model_kwargs")
            else:
                logger.info(f"Temperature not supported for {model_alias}, skipping")
                
            # Filter out None values
            nova_model_kwargs = {k: v for k, v in nova_model_kwargs.items() if v is not None}
            logger.info(f"Creating NovaBedrock with model_kwargs: {nova_model_kwargs}")
            
            # Use persistent client for Nova models
            new_region = os.environ.get("AWS_REGION", region)
            
            model = NovaBedrock(
                model_id=model_id,
                client=persistent_client.client,  # Use the underlying client from persistent wrapper
                region_name=new_region,
                model_kwargs=nova_model_kwargs,
                max_tokens=effective_max_tokens,
                thinking_mode=effective_thinking_mode
            )
            
            # Add a debug log to check the model's parameters
            logger.info(f"NovaBedrock created with: model_id={model_id}, max_tokens={effective_max_tokens}")
            if "temperature" in supported_params:
                logger.info(f"Temperature: {effective_temperature}")
            if hasattr(model, 'model_kwargs'):
                logger.info(f"NovaBedrock model_kwargs: {model.model_kwargs}")
        elif wrapper_class == "OpenAIBedrock":
            # Handle OpenAI models on Bedrock
            from app.agents.wrappers.openai_bedrock_wrapper import OpenAIBedrock
            logger.info(f"Initializing OpenAI model on Bedrock: {model_id}")
            
            # OpenAI models require us-west-2 region
            openai_region = "us-west-2"
            if region != openai_region:
                logger.warning(f"OpenAI models require us-west-2 region. Switching from {region} to {openai_region}")
                region = openai_region
                # Update environment variable
                os.environ["AWS_REGION"] = openai_region
                # Update state
                cls._state['aws_region'] = openai_region
            
            # Create model_kwargs for OpenAI
            openai_model_kwargs = {
                "max_tokens": effective_max_tokens,
                "temperature": effective_temperature
            }
            
            # Add top_p if provided
            if effective_top_p is not None:
                openai_model_kwargs["top_p"] = effective_top_p
            
            # Filter out None values
            openai_model_kwargs = {k: v for k, v in openai_model_kwargs.items() if v is not None}
            logger.info(f"Creating OpenAIBedrock with model_kwargs: {openai_model_kwargs}")
            
            # Use the persistent client but ensure it's for the correct region
            openai_client = cls._get_persistent_bedrock_client(aws_profile, openai_region, model_id, model_config)
            
            model = OpenAIBedrock(
                model_id=model_id,
                client=openai_client.client if hasattr(openai_client, 'client') else openai_client,
                region_name=region,
                model_kwargs=openai_model_kwargs,
                streaming=True
            )
            
            logger.info(f"OpenAIBedrock created with: model_id={model_id}, max_tokens={effective_max_tokens}")
        else:
            # Use ZiyaBedrock instead of standard ChatBedrock
            logger.debug(f"Initializing ZiyaBedrock for model: {model_id}")
            
            # Create a model_kwargs dictionary with all parameters
            model_kwargs = {
                "top_k": effective_top_k,
                "top_p": effective_top_p,
                "max_tokens": effective_max_tokens
            }

            # Check if model supports top_k
            if 'supported_parameters' in model_config:
                if 'top_k' not in model_config['supported_parameters']:
                    # Remove top_k if not supported
                    if 'top_k' in model_kwargs:
                        logger.debug(f"Removing unsupported parameter 'top_k' for model {model_id}")
                        del model_kwargs['top_k']
                        # Also remove from environment
                        if 'ZIYA_TOP_K' in os.environ:
                            del os.environ['ZIYA_TOP_K']
                
            # Import and use ZiyaBedrock
            from app.agents.wrappers.ziya_bedrock import ZiyaBedrock
            
            # Create the ZiyaBedrock model
            model = ZiyaBedrock(
                model_id=model_id,
                client=persistent_client,  # Use persistent client
                region_name=region,
                model_kwargs=model_kwargs, # Pass effective kwargs from above
                temperature=effective_temperature,    # Pass effective temperature
                max_tokens=effective_max_tokens,      # Pass effective max_tokens
                thinking_mode=effective_thinking_mode
            )
            
            # Add a debug log to check the model's parameters
            logger.debug(f"ZiyaBedrock created with: model_id={model_id}, temperature={effective_temperature}, max_tokens={effective_max_tokens}")
            if hasattr(model, 'get_parameters'):
                logger.debug(f"ZiyaBedrock parameters: {model.get_parameters()}")
        
        return model

    @classmethod
    def _initialize_google_model(cls, model_config: Dict[str, Any]):
        """
        Initialize a Google model with direct API (no langchain).
        
        Args:
            model_config: Model configuration
            
        Returns:
            DirectGoogleModel: The initialized Google model
        """
        # Import the direct Google wrapper
        from app.agents.wrappers.google_direct import DirectGoogleModel
            
        # Force garbage collection before creating new model
        gc.collect()
        
        logger.info("Initializing Google model with direct API")
        
        # Load environment variables from .env file specifically for Google models
        dotenv_path = find_dotenv()
        if dotenv_path:
            load_dotenv(dotenv_path)
            logger.info(f"Loaded environment variables from {dotenv_path}")
        
        # Get model ID and parameters
        model_id = model_config.get("model_id")
        temperature = model_config.get("temperature", 0.3)
        max_output_tokens = model_config.get("max_output_tokens", 2048)
        thinking_level = model_config.get("thinking_level")  # Get thinking_level for Gemini 3, default to None
        
        logger.info(f"Google model config: thinking_level={thinking_level}")
        
        # Apply environment variable overrides
        settings = cls.get_model_settings(model_config)
        if "temperature" in settings:
            temperature = settings["temperature"]
        if "max_output_tokens" in settings:
            max_output_tokens = settings["max_output_tokens"]
        # Override thinking_level from environment if set
        if "thinking_level" in settings:
            env_thinking_level = settings.get("thinking_level")
            if env_thinking_level:
                thinking_level = env_thinking_level
                logger.info(f"Overriding thinking_level from environment: {thinking_level}")
        
        # Only pass thinking_level to models that support it
        supports_thinking = model_config.get("supports_thinking", False)
        if not supports_thinking:
            thinking_level = None
            logger.debug(f"Model {model_id} does not support thinking, setting thinking_level to None")
        
        # Check Google credentials (this also loads GOOGLE_API_KEY if not already set)
        cls._check_google_credentials()
        
        # Explicitly get the API key after checking credentials
        google_api_key = os.environ.get("GOOGLE_API_KEY")
        if google_api_key:
            logger.info(f"GOOGLE_API_KEY found, length: {len(google_api_key)}, first 5 chars: {google_api_key[:5]}...")
            if not google_api_key.strip(): # Check if the key is just whitespace
                logger.warning("GOOGLE_API_KEY is present but empty or whitespace. Treating as not set to allow ADC.")
                google_api_key = None
        else:
            logger.info("GOOGLE_API_KEY not found in environment. ADC will be used by the library if configured.")
            google_api_key = None
        
        logger.info(f"Initializing Google model: {model_id} with kwargs: {{'temperature': {temperature}, 'max_output_tokens': {max_output_tokens}}}")
        
        # Create the model with direct API
        model = DirectGoogleModel(
            model_name=model_id,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            thinking_level=thinking_level
        )
        
        return model

    @classmethod
    def _check_google_credentials(cls) -> None:
        """
            Check if Google credentials (API key or ADC) are available and valid.
        
        Raises:
            ValueError: If credentials are not available or invalid
        """
        # First check for API key in environment variables
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            logger.info("Google API key found in environment variables")
            return
            
        # If no API key, try application default credentials
        try:
            # Try to get credentials
            credentials, project = google.auth.default()
            cls._state['google_credentials'] = credentials
            logger.info(f"Google credentials found for project: {project}")
        except google.auth.exceptions.DefaultCredentialsError:
            logger.error("Google credentials not found")
            raise ValueError(
                "Google credentials not found. Please set up Google credentials by running:\n"
                "gcloud auth application-default login"
            )
        except Exception as e:
            logger.error(f"Error checking Google credentials: {e}")
            raise ValueError(f"Error checking Google credentials: {e}")

    @classmethod
    def get_model_with_stop(cls, stop: Optional[List[str]] = None) -> BaseChatModel:
        """
        Get a model with stop sequences configured.
        
        Args:
            stop: List of stop sequences
            
        Returns:
            BaseChatModel: Model with stop sequences configured
        """
        # Force garbage collection before getting model
        import gc
        gc.collect()
        
        # Initialize model if needed
        model = cls.initialize_model()
        
        # If no stop sequences, return the model as is
        if not stop:
            return model
            
        # Check if the model supports stop sequences
        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
        model_config = cls.get_model_config(endpoint, model_name)
        model_id = model_config.get("model_id", model_name)
        
        # Handle both string and dict model IDs
        model_id_str = ""
        if isinstance(model_id, dict):
            # For dict model IDs, get a representative string
            model_id_str = next(iter(model_id.values())) if model_id else ""
        else:
            model_id_str = str(model_id)
        
        # For Claude models, we need to remove the stop parameter
        if "claude" in model_id_str.lower():
            logger.info(f"Removing unsupported parameter 'stop' for model {model_id}")
            logger.info(f"Binding with filtered kwargs: {{}}")
            return model
            
        # For other models, bind the stop parameter
        logger.info(f"Binding model with stop sequences: {stop}")
        return model.bind(stop=stop)

    @classmethod
    def get_agent_executor(cls) -> AgentExecutor:
        """
        Get the agent executor, initializing it if needed.
        
        Returns:
            AgentExecutor: The agent executor
        """
        from app.agents.agent import create_agent_executor
        
        # Check if we need to initialize
        if cls._state['agent_executor'] is None:
            # Initialize model if needed
            model = cls.initialize_model()
            
            # Create agent executor
            agent_executor = create_agent_executor(model)
            cls._state['agent_executor'] = agent_executor
            
        return cls._state['agent_executor']

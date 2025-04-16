from typing import Optional, Dict, Any, Union, List
import os
import json
import botocore
from pathlib import Path
from langchain_aws import ChatBedrock
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseChatModel
from langchain.callbacks.base import BaseCallbackHandler
from app.utils.logging_utils import logger
import google.auth.exceptions
import google.auth
from dotenv import load_dotenv
from dotenv.main import find_dotenv
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException
from app.agents.callbacks import EmptyMessageFilter

from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_core.messages import HumanMessage

# Import configuration from the central config module
import app.config as config

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
        for message in messages:
            if not message.content or not message.content.strip():
                raise ValueError("Empty message content detected")

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
        'last_auth_error': None  # Add this to store the last authentication error
    }
    
    @classmethod
    def _reset_state(cls):
        """
        Reset the internal state of the ModelManager.
        This is used when switching models to ensure a clean initialization.
        """
        logger.info("Resetting ModelManager state")
        cls._state = {
            'model': None,
            'current_model_id': None,
            'auth_checked': False,
            'auth_success': False,
            'google_credentials': None,
            'aws_profile': None,
            'aws_region': None,
            'process_id': None,
            'llm_with_stop': None,
            'agent': None,
            'agent_executor': None
        }
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
            str: The model ID
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
    def filter_model_kwargs(cls, model_kwargs, model_config):
        """
        Filter model kwargs to only include parameters supported by the model.
        
        Args:
            model_kwargs: Dict of model kwargs to filter
            model_config: Model configuration dict
            
        Returns:
            Dict: Filtered model kwargs
        """
        logger.info(f"Filtering model kwargs: {model_kwargs}")
        
        # Get supported parameters from the model config
        supported_params = model_config.get('supported_params', [])
        
        # If model config doesn't have supported_params, try to get from endpoint defaults
        if not supported_params:
            endpoint = model_config.get('endpoint', cls.DEFAULT_ENDPOINT)
            if endpoint in cls.ENDPOINT_DEFAULTS:
                supported_params = cls.ENDPOINT_DEFAULTS[endpoint].get('supported_params', [])
        
        # Check for model-specific support for max_input_tokens
        supports_max_input_tokens = model_config.get('supports_max_input_tokens', False)
        if supports_max_input_tokens and 'max_input_tokens' not in supported_params:
            supported_params.append('max_input_tokens')
        elif not supports_max_input_tokens and 'max_input_tokens' in supported_params:
            supported_params.remove('max_input_tokens')
        
        logger.info(f"Supported parameters for model: {supported_params}")
        
        # Filter the kwargs
        filtered_kwargs = {}
        for key, value in model_kwargs.items():
            if key in supported_params:
                filtered_kwargs[key] = value
            else:
                logger.debug(f"Ignoring unsupported parameter '{key}' for model")
                
        return filtered_kwargs
            
    @classmethod
    def get_model_settings(cls, model_instance=None):
        """
        Get the current model settings.
        
        Args:
            model_instance: Optional model instance to get settings from
            
        Returns:
            dict: The complete model settings
        """
        # Get endpoint and model from environment variables
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", config.DEFAULT_MODELS.get(endpoint))
        
        # Get model configuration
        try:
            model_config = cls.get_model_config(endpoint, model_name)
            
            # Start with all settings from the model config
            settings = {}
            
            # Copy all relevant settings from model_config
            for key, value in model_config.items():
                # Skip internal/metadata keys
                if key not in ["name", "model_id", "parameter_mappings"]:
                    settings[key] = value
            
            # Override with environment variables if they exist
            for env_var, config_key in config.ENV_VAR_MAPPING.items():
                if env_var in os.environ:
                    # Handle type conversion based on the expected type
                    value = os.environ[env_var]
                    if isinstance(settings.get(config_key), bool):
                        settings[config_key] = value.lower() in ("true", "1", "yes")
                    elif isinstance(settings.get(config_key), int):
                        settings[config_key] = int(value)
                    elif isinstance(settings.get(config_key), float):
                        settings[config_key] = float(value)
                    else:
                        settings[config_key] = value
            
            # Add thinking_mode from environment if it exists
            if "ZIYA_THINKING_MODE" in os.environ:
                settings["thinking_mode"] = os.environ["ZIYA_THINKING_MODE"] == "1"
            elif "supports_thinking" in model_config:
                settings["thinking_mode"] = model_config["supports_thinking"]
            
            return settings
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
    def get_model_settings(cls, config_or_model=None) -> Dict[str, Any]:
        """
        Get model settings from environment variables or config.
        Handles parameter mapping and type conversion automatically.
        
        Args:
            config_or_model: Either a config dict or a model instance
            
        Returns:
            dict: Model settings with proper parameter mappings
        """
        # Get config
        if hasattr(config_or_model, 'model_id'):
            model = config_or_model
            endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
            model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
            config_dict = cls.get_model_config(endpoint, model_name)
        elif isinstance(config_or_model, dict):
            config_dict = config_or_model
        else:
            endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
            model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint))
            config_dict = cls.get_model_config(endpoint, model_name)
            
        # Start with config values
        settings = {}
        
        # Apply environment variable overrides with proper type conversion
        for env_var, config_key in cls.ENV_VAR_MAPPING.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                
                # Convert to appropriate type based on the config's existing value
                if config_key in config_dict:
                    if isinstance(config_dict[config_key], bool):
                        value = value.lower() in ('true', 'yes', '1', 't', 'y')
                    elif isinstance(config_dict[config_key], int):
                        value = int(value)
                    elif isinstance(config_dict[config_key], float):
                        value = float(value)
                        
                settings[config_key] = value
                
        # Apply parameter mappings
        parameter_mappings = config_dict.get("parameter_mappings", {})
        for source_param, target_params in parameter_mappings.items():
            if source_param in settings:
                for target_param in target_params:
                    if target_param != source_param:  # Avoid duplicate assignments
                        settings[target_param] = settings[source_param]
                        
        return settings

    @classmethod
    def initialize_model(cls, force_reinit=False) -> BaseChatModel:
        """
        Initialize the model based on environment variables.
        
        Args:
            force_reinit: Force reinitialization even if already initialized
            
        Returns:
            BaseChatModel: The initialized model
        """
        import os
        
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
        model_id = model_config.get("model_id", model_name)
        
        # Reset any previous error flags
        KnownCredentialException._error_displayed = False
        
        # Initialize the model based on the endpoint - only authenticate with the specific endpoint
        logger.info(f"Starting authentication flow for endpoint: {endpoint}")
        if endpoint == "bedrock":
            logger.info("Using Bedrock authentication flow only")
            model = cls._initialize_bedrock_model(model_config)
        elif endpoint == "google":
            logger.info("Using Google authentication flow only")
            model = cls._initialize_google_model(model_config)
        else:
            raise ValueError(f"Unsupported endpoint: {endpoint}")
            
        # Update state
        cls._state['model'] = model
        cls._state['current_model_id'] = model_id
        cls._state['process_id'] = current_pid
        cls._state['auth_checked'] = True
        cls._state['auth_success'] = True
        
        # Add region to state if available
        if "region" in model_config:
            cls._state['aws_region'] = model_config["region"]
            
        logger.info(f"Model initialization complete. New state: {cls._state}")
        
        return model

    @classmethod
    def _initialize_bedrock_model(cls, model_config: Dict[str, Any]) -> ChatBedrock:
        """
        Initialize a Bedrock model with the given configuration.
        
        Args:
            model_config: Model configuration
            
        Returns:
            ChatBedrock: The initialized Bedrock model
        """
        from app.utils.aws_utils import ThrottleSafeBedrock, check_aws_credentials
        from app.utils.custom_exceptions import KnownCredentialException
        
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
            
        # Get region from config or environment
        region = model_config.get("region", "us-west-2")
        if "AWS_REGION" in os.environ:
            region = os.environ["AWS_REGION"]
            
        # Get model ID and parameters
        model_id = model_config.get("model_id")
        
        # Check for model ID override from environment
        model_id_override = os.environ.get("ZIYA_MODEL_ID_OVERRIDE")
        if model_id_override:
            logger.info(f"Using model ID override: {model_id_override} instead of {model_id}")
            model_id = model_id_override
            
        temperature = model_config.get("temperature", 0.3)
        top_k = model_config.get("top_k", 15)
        max_tokens = model_config.get("max_output_tokens", 4096)
        
        # Apply environment variable overrides
        settings = cls.get_model_settings(model_config)
        if "temperature" in settings:
            temperature = settings["temperature"]
        if "top_k" in settings:
            top_k = settings["top_k"]
        if "max_tokens" in settings:
            max_tokens = settings["max_tokens"]
            
        logger.info(f"Initializing Bedrock model: {model_id} with kwargs: {{'temperature': {temperature}, 'top_k': {top_k}, 'max_tokens': {max_tokens}}}")
        
        # Reset any previous error flags
        KnownCredentialException._error_displayed = False
        
        # Check AWS credentials
        creds_valid, error_msg = check_aws_credentials(profile_name=aws_profile)
        if not creds_valid:
            # Log the full error message for debugging
            logger.error(f"AWS credentials check failed: {error_msg}")
            
            # Store the error message in a class variable for consistent reporting
            cls._state['last_auth_error'] = error_msg
            
            # Raise a KnownCredentialException instead of ValueError
            # The exception will handle printing the message only once
            from app.utils.custom_exceptions import KnownCredentialException
            raise KnownCredentialException(error_msg)
        
        # Check if this is a Nova model
        family = model_config.get("family")
        
        # Get thinking mode setting
        thinking_mode = settings.get("thinking_mode", False)
        if isinstance(thinking_mode, str):
            thinking_mode = thinking_mode.lower() == "true"
        
        # Create the appropriate model based on family
        if family == "nova":
            from app.agents.nova_wrapper import NovaBedrock
            logger.info(f"Initializing Nova model: {model_id}")
            
            # Create a model_kwargs dictionary with all parameters
            nova_model_kwargs = {
                "top_k": top_k,
                "max_tokens": max_tokens  # Explicitly include max_tokens in model_kwargs
            }
            
            logger.info(f"Creating NovaBedrock with model_kwargs: {nova_model_kwargs}")
            
            model = NovaBedrock(
                model_id=model_id,
                client=None,  # Will be created internally
                region_name=region,
                model_kwargs=nova_model_kwargs,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_mode=thinking_mode
            )
            
            # Add a debug log to check the model's parameters
            logger.info(f"NovaBedrock created with: model_id={model_id}, temperature={temperature}, max_tokens={max_tokens}")
            if hasattr(model, 'model_kwargs'):
                logger.info(f"NovaBedrock model_kwargs: {model.model_kwargs}")
        else:
            # Use standard ChatBedrock
            logger.info(f"Initializing ChatBedrock for model: {model_id}")
            
            # Create a model_kwargs dictionary with all parameters
            model_kwargs = {
                "top_k": top_k,
                "max_tokens": max_tokens  # Explicitly include max_tokens in model_kwargs
            }
            
            logger.info(f"Creating ChatBedrock with model_kwargs: {model_kwargs}")
            
            # Create the ChatBedrock model
            model = ChatBedrock(
                model_id=model_id,
                client=None,  # Will be created internally
                region_name=region,
                model_kwargs=model_kwargs,
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            # Add a debug log to check the model's parameters
            logger.info(f"ChatBedrock created with: model_id={model_id}, temperature={temperature}, max_tokens={max_tokens}")
            if hasattr(model, 'model_kwargs'):
                logger.info(f"ChatBedrock model_kwargs: {model.model_kwargs}")
            
            # Wrap the client with our custom client to ensure max_tokens is correctly passed
            from app.utils.custom_bedrock import CustomBedrockClient
            if hasattr(model, 'client'):
                model.client = CustomBedrockClient(model.client, max_tokens=max_tokens)
                logger.info(f"Wrapped boto3 client with CustomBedrockClient, max_tokens={max_tokens}")
        
        return model
        
        return model

    @classmethod
    def _initialize_google_model(cls, model_config: Dict[str, Any]) -> ChatGoogleGenerativeAI:
        """
        Initialize a Google model with the given configuration.
        
        Args:
            model_config: Model configuration
            
        Returns:
            ChatGoogleGenerativeAI: The initialized Google model
        """
        # Import here to avoid unnecessary imports when not using Google models
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ValueError("langchain_google_genai package is not installed. Please install it to use Google models.")
            
        logger.info("Initializing Google model")
        
        # Load environment variables from .env file specifically for Google models
        dotenv_path = find_dotenv()
        if dotenv_path:
            load_dotenv(dotenv_path)
            logger.info(f"Loaded environment variables from {dotenv_path}")
        
        # Get model ID and parameters
        model_id = model_config.get("model_id")
        temperature = model_config.get("temperature", 0.3)
        max_output_tokens = model_config.get("max_output_tokens", 2048)
        convert_system_message = model_config.get("convert_system_message_to_human", True)
        
        # Apply environment variable overrides
        settings = cls.get_model_settings(model_config)
        if "temperature" in settings:
            temperature = settings["temperature"]
        if "max_output_tokens" in settings:
            max_output_tokens = settings["max_output_tokens"]
            
        logger.info(f"Initializing Google model: {model_id} with kwargs: {{'temperature': {temperature}, 'max_output_tokens': {max_output_tokens}}}")
        
        # Check Google credentials
        cls._check_google_credentials()
        
        # Create the model
        model = ChatGoogleGenerativeAI(
            model=model_id,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            convert_system_message_to_human=convert_system_message,
            callbacks=[EmptyMessageFilter()]
        )
        
        return model

    @classmethod
    def _check_google_credentials(cls) -> None:
        """
        Check if Google credentials are available and valid.
        
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
        
        # For Claude models, we need to remove the stop parameter
        if "claude" in model_id.lower():
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

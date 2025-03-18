from typing import Optional, Dict, Any
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

from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_core.messages import HumanMessage

class ModelManager:

    # Class-level state with process-specific initialization
    _state = {
        'model': None,
        'auth_checked': False,
        'auth_success': False,
        'google_credentials': None,
        'current_model_id': None,
        'aws_profile': None,
        'process_id': None,
        'llm_with_stop': None,
        'agent': None,
        'agent_executor': None
    }

    DEFAULT_ENDPOINT = "bedrock"

    DEFAULT_MODELS = {
        "bedrock": "sonnet3.5-v2",
        "google": "gemini-1.5-pro"
    }


    MODEL_CONFIGS = {
        "bedrock": {
            "sonnet3.7": {
                "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 128000,
                "temperature": 0.3,
                "top_k": 15, 
                "supports_max_input_tokens": True,
                "supports_thinking": True,
            },
            "sonnet3.5-v2": {
                "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15,
            },
            "sonnet3.5": {
                "model_id": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15,
            },
            "opus": {
                "model_id": "us.anthropic.claude-3-opus-20240229-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15,
            }, 
            "sonnet": {
                "model_id": "us.anthropic.claude-3-sonnet-20240229-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15,
            },
            "haiku": {
                "model_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15,
            },
        },
        "google": {
            "gemini-pro": {
                "model_id": "gemini-pro",
                "token_limit": 30720,
                "max_output_tokens": 2048,
                "temperature": 0.3, 
                "convert_system_message_to_human": True,
            },
            "gemini-2.0-pro": {
                "model_id": "gemini-2.0-pro-exp-02-05",
                "token_limit": 2097152,
                "max_output_tokens": 8192,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            },
            "gemini-2.0-flash": {
                "model_id": "gemini-2.0-flash",
                "token_limit": 1048576,
                "max_output_tokens": 8192,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            },
            "gemini-2.0-flash-lite": {
                "model_id": "gemini-2.0-flash-lite",
                "token_limit": 1048576,
                "max_output_tokens": 8192,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            },
            "gemini-1.5-flash": {
                "model_id": "gemini-1.5-flash",
                "token_limit": 1048576,
                "max_output_tokens": 8192,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            },
            "gemini-1.5-flash-8b": {
                "model_id": "gemini-1.5-flash-8b",
                "token_limit": 1048576,
                "max_output_tokens": 8192,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            },
            "gemini-1.5-pro": {
                "model_id": "gemini-1.5-pro",
                "token_limit": 1000000,
                "max_output_tokens": 2048,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
            }
        }
    }

    @classmethod
    def get_model_config(cls, endpoint: str, model_name: str = None) -> dict:
        """
        Get the configuration for a specific model. If model_name is None,
        returns the default model for the endpoint.
        """
        endpoint_configs = cls.MODEL_CONFIGS.get(endpoint)
        if not endpoint_configs:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        if model_name is None:
            default_name = cls.DEFAULT_MODELS[endpoint]
            return {**endpoint_configs[default_name], "name": default_name}

        # Check if it's a model ID
        for name, config in endpoint_configs.items():
            if config["model_id"] == model_name:
                return {**config, "name": name}

        # Check if it's a model name
        if model_name in endpoint_configs:
            return {**endpoint_configs[model_name], "name": model_name}

        # Neither - show valid options
        valid_models = ", ".join(endpoint_configs.keys())
        if endpoint == "bedrock":
            raise ValueError(
                f"Invalid model '{model_name}' for bedrock endpoint. "
                f"Valid models are: {valid_models}"
            )
        elif endpoint == "google":
            raise ValueError(
                f"Invalid model '{model_name}' for google endpoint. "
                f"Valid models are: {valid_models}"
            ) 

    @classmethod
    def get_model_id(cls, model_instance) -> str:
        """Get model ID in a consistent way across different model types."""

        # get from environment/defaults
        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT).lower()
        model_alias = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint, cls.DEFAULT_MODELS[cls.DEFAULT_ENDPOINT]))

        try:
            # Always get the model_id from the config, based on the environment.
            model_id = cls.MODEL_CONFIGS.get(endpoint, {}).get(model_alias, {}).get("model_id")
            logger.debug(f"get_model_id: endpoint={endpoint}, alias={model_alias}, id={model_id}, cached={cls._state.get('current_model_id')}")
            return model_id
        except KeyError:
            logger.warning(f"Model {model_alias} not found in configs for {endpoint}")
            return model_alias

    @classmethod
    def filter_model_kwargs(cls, kwargs, model_config):
        """
        Filter kwargs to only include parameters supported by the model.
        
        Args:
            kwargs: Dictionary of parameters to filter
            model_config: Model configuration dictionary
            
        Returns:
            Dictionary with only supported parameters
        """
        # Always allow these common parameters
        common_params = {'temperature', 'max_tokens', 'top_k', 'stop'}
        
        # Get all supported parameters from model config
        supported_params = set(model_config.keys())
        
        # Add any explicitly supported parameters from model config
        for key, value in model_config.items():
            if key.startswith('supports_') and value:
                param_name = key[9:]  # Remove 'supports_' prefix
                supported_params.add(param_name)
        
        # Combine common and supported parameters
        allowed_params = common_params.union(supported_params)
        
        # Filter kwargs to only include supported parameters
        filtered_kwargs = {}
        for key, value in kwargs.items():
            if key in allowed_params:
                filtered_kwargs[key] = value
            else:
                logger.info(f"Removing unsupported parameter '{key}' for model {model_config.get('model_id')}")
        
        return filtered_kwargs

    @classmethod
    def _reset_state(cls):
        """forcibly reset the model state"""
        logger.info("Performing complete state reset")
        default_state = {
            'model': None,
            'auth_checked': False,
            'auth_success': False,
            'google_credentials': None,
            'current_model_id': None,
            'aws_profile': None,
            'process_id': None,
            'llm_with_stop': None,
            'agent': None,
            'model_kwargs': None,
            'agent_executor': None
        }
        # First store any credentials/profile we want to preserve
        preserved = {
            'aws_profile': cls._state.get('aws_profile'),
            'aws_region': cls._state.get('aws_region'),
            'google_credentials': cls._state.get('google_credentials')
        }
        # Clear all state
        cls._state = default_state.copy()
        # Restore preserved values
        cls._state.update(preserved)
        
        logger.info(f"State after reset: {cls._state}")
        # Ensure critical flags are reset
        cls._state['process_id'] = None
        cls._state['auth_checked'] = False
        cls._state['model_kwargs'] = None  # Ensure model_kwargs is reset

    @classmethod
    def get_model_settings(cls, model_instance) -> Dict[str, Any]:
        """Get model settings in a consistent way across different model types."""
        # Try to get settings from model instance first
        if hasattr(model_instance, 'model') and hasattr(model_instance.model, 'model_kwargs'):
            return model_instance.model.model_kwargs
        if hasattr(model_instance, 'model_kwargs'):
            return model_instance.model_kwargs

        # Fall back to config-based settings
        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS[endpoint])

        if endpoint in cls.MODEL_CONFIGS and model_name in cls.MODEL_CONFIGS[endpoint]:
            config = cls.MODEL_CONFIGS[endpoint][model_name]
            return {
                'temperature': float(os.environ.get("ZIYA_TEMPERATURE", config.get('temperature', 0.3))),
                'max_tokens': int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", config.get('max_output_tokens', 4096))),
                'top_k': int(os.environ.get("ZIYA_TOP_K", config.get('top_k', 15)))
            }

        # Return empty dict if no settings found
        return {}

    @classmethod
    def _load_credentials(cls) -> bool:
        """
        Load credentials from environment or .env files.
        Returns True if GOOGLE_API_KEY is found, False otherwise.
        """
        current_pid = os.getpid()

        # Check if we've already loaded credentials in this process
        if cls._state['auth_checked'] and cls._state['process_id'] == current_pid:
            return bool(cls._state['google_credentials'])

        # Reset state for new process
        if cls._state['process_id'] != current_pid:
            cls._state['credentials_checked'] = False

        cwd = os.getcwd()
        home = str(Path.home())
        env_locations = {
            'current_dir': os.path.join(cwd, '.env'),
            'home_ziya': os.path.join(home, '.ziya', '.env'),
            'found_dotenv': find_dotenv()
        }

        logger.debug("Searching for .env files:")
        for location_name, env_file in env_locations.items():
            if os.path.exists(env_file):
                logger.info(f"Loading credentials from {location_name}: {env_file}")
                try:
                    with open(env_file, 'r') as f:
                        logger.debug(f"Content of {env_file}:")
                        for line in f:
                            logger.debug(f"  {line.rstrip()}")
                except Exception as e:
                    logger.error(f"Error reading {env_file}: {e}")
            else:
                logger.debug(f"No .env file at {location_name}: {env_file}")

        for env_file in env_locations.values():
            cls._state['auth_checked'] = True
            cls._google_credentials = os.getenv("GOOGLE_API_KEY")
            if os.path.exists(env_file):
                logger.info(f"Loading credentials from {env_file}")
                success = load_dotenv(env_file, override=True)
                if success:
                    # Explicitly store the value we loaded
                    api_key = os.getenv("GOOGLE_API_KEY")
                    if api_key:
                        cls._state.update({
                            'auth_checked': True,
                            'auth_success': True,
                            'google_credentials': os.getenv("GOOGLE_API_KEY"),
                            'process_id': current_pid
                        })
                        return True
                    else:
                        logger.warning(f"Found .env file at {location_name}: {env_file} but it doesn't contain GOOGLE_API_KEY")
        else:
            if "GOOGLE_API_KEY" not in os.environ:
                logger.debug("No .env file found, using system environment variables")
            cls._state.update({
                'auth_checked': True,
                'auth_success': True,
                'google_credentials': os.getenv("GOOGLE_API_KEY"),
                'process_id': current_pid
            })
            return bool(cls._state['google_credentials'])

    @classmethod
    def initialize_model(cls, force_reinit: bool = False) -> BaseChatModel:
        if not force_reinit and cls._state.get('model') is not None:
            logger.info("Checking cached model state")
            # Check if the cached model ID matches the *current* model ID.
            current_model_id = cls.get_model_id(None)  # Pass None; we don't have an instance yet.
            if cls._state.get('current_model_id') != current_model_id:
                force_reinit = True
                logger.info(
                    f"Model mismatch detected: cached={cls._state.get('current_model_id')}, "
                    f"requested={current_model_id}"
                )
            else:
                logger.info(f"Using cached model instance: {cls._state.get('current_model_id')}")
                return cls._state['model']

        logger.info("Starting model initialization...")
        logger.info(f"Current state: {cls._state}")
        current_pid = os.getpid()

        # Load credentials first, before any model initialization
        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
        logger.info(f"Initializing for endpoint: {endpoint}")

        # For Google endpoint, ensure credentials are loaded first
        if endpoint == "google":
            if not cls._load_credentials():
                raise ValueError(
                    "GOOGLE_API_KEY environment variable is required for google endpoint.\n"
                    "You can set it in your environment or create a .env file in either:\n"
                    "  - Your current directory\n"
                    "  - ~/.ziya/.env\n")


        if not cls._state['auth_checked'] or cls._state['process_id'] != current_pid:
            logger.info("Loading credentials before model initialization")
            if not cls._load_credentials():
                if os.environ.get("ZIYA_ENDPOINT") == "google":
                    raise ValueError(
                        "GOOGLE_API_KEY environment variable is required for google endpoint.\n"
                        "You can set it in your environment or create a .env file in either:\n"
                        "  - Your current directory\n"
                        "  - ~/.ziya/.env\n")
            cls._state['auth_checked'] = True
            cls._state['process_id'] = current_pid

        # Get model settings after endpoint is confirmed
        model_alias = os.environ.get("ZIYA_MODEL", cls.DEFAULT_MODELS.get(endpoint, cls.DEFAULT_MODELS[cls.DEFAULT_ENDPOINT]))
        
        # Validate and normalize model identifier
        if model_alias.startswith('us.anthropic.'):
            # Convert full ID to alias if possible
            found_alias = None
            for alias, config in cls.MODEL_CONFIGS[endpoint].items():
                if config['model_id'] == model_alias:
                    found_alias = alias
                    break
            if found_alias:
                logger.info(f"Converting model ID {model_alias} to alias {found_alias}")
                model_alias = found_alias
                os.environ["ZIYA_MODEL"] = model_alias
            else:
                raise ValueError(
                    f"Unknown model ID: {model_alias}. Valid models are: "
                    f"{', '.join(cls.MODEL_CONFIGS[endpoint].keys())}"
                )
        
        # Get the model configuration
        model_config = cls.MODEL_CONFIGS[endpoint][model_alias]
        cls._state['current_model_id'] = model_config['model_id']
        
        # Validate model configuration early
        if model_alias not in cls.MODEL_CONFIGS.get(endpoint, {}):
            raise ValueError(
                f"Unsupported model '{model_alias}' for {endpoint} endpoint. "
                f"Valid models are: {', '.join(cls.MODEL_CONFIGS[endpoint].keys())}"
            )

        # Log current state before initialization
        logger.info("Model initialization state:", {
            'endpoint': endpoint,
            'requested_model': model_alias,
            'current_env_model': os.environ.get("ZIYA_MODEL"),
            'force_reinit': force_reinit
        })

        # Return cached model if it exists for this process
        if not force_reinit and cls._state['model'] is not None and cls._state['process_id'] == current_pid and cls._state['current_model_id'] == model_config['model_id']:
            logger.info("Using cached model instance")
            return cls._state['model']

        # Reset state for new process if needed
        if cls._state['process_id'] != current_pid:
            logger.info("New process detected, resetting state")
            cls._state['model'] = None
            cls._state['auth_checked'] = False
            cls._state['llm_with_stop'] = None
        
        # Clear existing model if forcing reinitialization
        if force_reinit:
            logger.info("Force reinitialization requested")
            if cls._state['model']:
                # Cleanup if needed
                cls._state['model'] = None
                cls._state['current_model_id'] = None
                cls._state['auth_checked'] = False
                cls._state['llm_with_stop'] = None
                cls._state['agent'] = None
        
        logger.info(f"Initializing model for endpoint: {endpoint}, model: {model_alias}")
        
        # Get and log the actual model configuration being used
        logger.info(f"Using model configuration for {model_alias}: {model_config}")
        
        # Clean up environment variables that might not be supported by the new model
        cls._clean_unsupported_env_vars(model_config)
        
        if endpoint == "bedrock":
            logger.info("Initializing Bedrock model")
            logger.info(f"State before Bedrock init: {cls._state}")
            cls._state['model'] = None  # Force new instance creation
            cls._state['model'] = cls._initialize_bedrock_model(model_alias)
        elif endpoint == "google":
            cls._state['model'] = cls._initialize_google_model(model_alias)
        else:
            raise ValueError(f"Unsupported endpoint: {endpoint}")

        # Verify initialization
        if not cls._state['model']:
            raise ValueError(f"Failed to initialize model {model_alias} - model is None")

        # Update process ID and state after successful initialization
        cls._state.update({
            'process_id': current_pid,
            'auth_checked': True,
            'auth_success': True,
            'current_model_id': model_config['model_id'] # Store the full ID, not the alias.
        })
        logger.info(f"Model initialization complete. New state: {cls._state}")
        
        return cls._state['model']
        
    @classmethod
    def _clean_unsupported_env_vars(cls, model_config):
        """Remove environment variables for parameters not supported by the model"""
        # Map of environment variables to their corresponding model config keys
        env_to_config_map = {
            "ZIYA_MAX_INPUT_TOKENS": "supports_max_input_tokens",
            # Add other mappings as needed
        }
        
        for env_var, config_key in env_to_config_map.items():
            if env_var in os.environ and not model_config.get(config_key, False):
                logger.info(f"Removing {env_var} as it's not supported by the current model")
                del os.environ[env_var]
 
    @classmethod
    def _initialize_bedrock_model(cls, model_name: Optional[str] = None) -> ChatBedrock:
        """Initialize a Bedrock model."""
        config = cls.get_model_config("bedrock", model_name)
        model_id = config["model_id"]
        max_output = config.get('max_output_tokens', 4096)
 
        if not cls._state['aws_profile']:
            cls._state['aws_profile'] = os.environ.get("ZIYA_AWS_PROFILE")
            cls._state['aws_region'] = os.environ.get("ZIYA_AWS_REGION", "us-west-2")
            logger.info(f"Using AWS Profile: {cls._state['aws_profile']}" if cls._state['aws_profile'] else "Using default AWS credentials")
        
        # Start with basic model kwargs
        model_kwargs = {
            'max_tokens': int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", max_output)),
            'temperature': float(os.environ.get("ZIYA_TEMPERATURE", config.get('temperature', 0.3))),
            'top_k': int(os.environ.get("ZIYA_TOP_K", config.get('top_k', 15)))
        }
        
        # Add any additional parameters from environment variables
        for key, value in os.environ.items():
            if key.startswith("ZIYA_") and key not in ["ZIYA_MODEL", "ZIYA_ENDPOINT", 
                                                      "ZIYA_TEMPERATURE", "ZIYA_TOP_K", 
                                                      "ZIYA_MAX_OUTPUT_TOKENS", "ZIYA_THINKING_MODE",
                                                      "ZIYA_AWS_PROFILE", "ZIYA_AWS_REGION"]:
                param_name = key[5:].lower()  # Remove ZIYA_ prefix and convert to lowercase
                try:
                    # Try to convert to appropriate type
                    if value.isdigit():
                        value = int(value)
                    elif value.replace('.', '', 1).isdigit():
                        value = float(value)
                    elif value.lower() in ['true', 'false']:
                        value = value.lower() == 'true'
                    model_kwargs[param_name] = value
                except Exception as e:
                    logger.warning(f"Error converting environment variable {key}: {str(e)}")
        
        # Filter kwargs to only include supported parameters
        filtered_kwargs = cls.filter_model_kwargs(model_kwargs, config)
        
        logger.info(f"Initializing Bedrock model: {model_id} with filtered kwargs: {filtered_kwargs}")
        
        # Force clean model creation
        cls._state['model'] = None

        cls._state['model_kwargs'] = filtered_kwargs
        
        # Log any model-specific capabilities
        capabilities = {k: v for k, v in config.items() if k.startswith('supports_')}
        if capabilities:
            logger.info(f"Model {model_id} capabilities: {capabilities}")
        
        logger.info(f"Creating new Bedrock model instance with ID: {model_id}")
        model = ChatBedrock(
            model_id=model_id,
            credentials_profile_name=cls._state['aws_profile'],
            region_name=cls._state['aws_region'],
            config=botocore.config.Config(read_timeout=900, retries={'max_attempts': 3, 'total_max_attempts': 5}),
            model_kwargs=filtered_kwargs
        )

        logger.info(f"Successfully created new Bedrock model instance with ID: {model_id}")
        return model
 
    @classmethod
    def _initialize_google_model(cls, model_name: Optional[str] = None) -> ChatGoogleGenerativeAI:
        """Initialize a Google model."""
        if not model_name:
            model_name = "gemini-1.5-pro"
        config = cls.get_model_config("google", model_name)
        # Load credentials if not already loaded
        if not cls._state['auth_checked']:
            if not cls._load_credentials():
                raise ValueError(
                    "GOOGLE_API_KEY environment variable is required for google endpoint.\n"
                    "You can set it in your environment or create a .env file in either:\n"
                    "  - Your current directory\n"
                    "  - ~/.ziya/.env\n")
 
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            logger.debug(f"Found API key (starts with: {api_key[:6]}...)")
            if not api_key.startswith("AI"):
                logger.warning(f"API key format looks incorrect (starts with '{api_key[:6]}', should start with 'AI')")
        else:
            logger.debug("No API key found in environment")

        # Check Application Default Credentials
        try:
            credentials, project = google.auth.default()
            logger.debug(f"Found ADC credentials (project: {project})")
        except Exception as e:
            logger.debug(f"No ADC credentials found: {str(e)}")
            credentials = None
            project = None
 
            logger.info(f"Attempting to initialize Google model: {model_name}")

            # Get the model config
            model_config = cls.get_model_config("google", model_name)
            logger.info(f"Using model config: {json.dumps(model_config, indent=2)}")

            # Extract parameters from config and override with environment settings if available
            convert_system = model_config.get("convert_system_message_to_human", True) 
            temperature = float(os.environ.get("ZIYA_TEMPERATURE", 
                               model_config.get("temperature", 0.3)))
            # Get custom settings if available
            max_output_tokens = model_config.get("max_output_tokens", 2048)

            # Use our custom wrapper class instead of ChatGoogleGenerativeAI directly
            model = SafeChatGoogleGenerativeAI(
                model=model_config["model_id"],
                convert_system_message_to_human=convert_system,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                client_options={"api_endpoint": "generativelanguage.googleapis.com"},
                max_retries=3,
                verbose=os.environ.get("ZIYA_THINKING_MODE") == "1"
            )
            model.callbacks = [EmptyMessageFilter()]
            logger.info("Successfully connected to Google API")
            return model

        except google.auth.exceptions.DefaultCredentialsError as e:
            logger.error(f"Authentication error details: {str(e)}")
            raise ValueError(
                "\nGoogle API authentication failed. You need to either:\n\n"
                "1. Use an API key (recommended for testing):\n"
                "   - Get an API key from: https://makersuite.google.com/app/apikey\n"
                "   - Add to .env file: GOOGLE_API_KEY=your_key_here\n"
                f"   Current API key status: {'Found' if api_key else 'Not found'}\n\n"
                "2. Or set up Application Default Credentials (for production):\n"
                "   - Install gcloud CLI: https://cloud.google.com/sdk/docs/install\n"
                "   - Run: gcloud auth application-default login\n"
                "   - See: https://cloud.google.com/docs/authentication/external/set-up-adc\n"
                f"   Current ADC status: {'Found' if credentials else 'Not found'}\n\n"
                "Choose option 1 (API key) if you're just getting started.\n"
            )
        except Exception as e:
            logger.error(f"Unexpected error initializing Google model: {str(e)}")
            raise ValueError(
                f"\nFailed to initialize Google model: {str(e)}\n\n"
                f"API key status: {'Found' if api_key else 'Not found'}\n"
                f"ADC status: {'Found' if credentials else 'Not found'}\n"
                "Please check your credentials and try again."
            )
 
    @classmethod
    def get_available_models(cls, endpoint: Optional[str] = None) -> list[str]:
        """Get list of available models for the specified endpoint."""
        if endpoint is None:
            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
 
        if endpoint == "bedrock":
            return list(cls.BEDROCK_MODELS.keys())
        elif endpoint == "google":
            return list(cls.GOOGLE_MODELS.keys())
        else:
            raise ValueError(f"Unsupported endpoint: {endpoint}")

class EmptyMessageFilter(BaseCallbackHandler):
    """Filter out empty messages before they reach the Gemini API."""
    
    def on_chat_model_start(self, serialized, messages, **kwargs):
        """Check messages before they're sent to the model."""
        for i, message in enumerate(messages):
            if hasattr(message, 'content'):
                # If content is empty, replace with a placeholder
                if not message.content or message.content.strip() == '':
                    logger.warning(f"Empty message detected in position {i}, replacing with placeholder")
                    message.content = "Please provide a question."
            
            # Handle messages with dict content
            if isinstance(message, dict) and 'content' in message:
                if not message['content'] or message['content'].strip() == '':
                    logger.warning(f"Empty dict message detected in position {i}, replacing with placeholder")
                    message['content'] = "Please provide a question."
        
        # Check if all messages are empty
        if not any(getattr(m, 'content', None) or 
                  (isinstance(m, dict) and m.get('content')) 
                  for m in messages):
            logger.error("All messages are empty, adding a placeholder message")
            messages.append({"role": "user", "content": "Please provide a question."})
        return messages

class SafeChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """A wrapper around ChatGoogleGenerativeAI that prevents empty messages."""
    
    def _validate_messages(self, messages):
        """Ensure no messages have empty content."""
        logger.info(f"Validating {len(messages)} messages")
        for i, msg in enumerate(messages):
            if hasattr(msg, 'content'):
                if not msg.content or msg.content.strip() == '':
                    logger.warning(f"Empty message detected at position {i}, replacing with placeholder")
                    msg.content = "Please provide a question."
            elif isinstance(msg, dict) and 'content' in msg:
                if not msg['content'] or not msg['content'].strip():
                    logger.warning(f"Empty dict message detected at position {i}, replacing with placeholder")
                    msg['content'] = "Please provide a question."
        return messages
    
    async def agenerate(self, messages, *args, **kwargs):
        """Override agenerate to validate messages."""
        messages = self._validate_messages(messages)
        return await super().agenerate(messages, *args, **kwargs)
    
    def generate(self, messages, *args, **kwargs):
        """Override generate to validate messages."""
        messages = self._validate_messages(messages)
        return super().generate(messages, *args, **kwargs)
    
    async def ainvoke(self, input, *args, **kwargs):
        """Override ainvoke to validate input."""
        if isinstance(input, list):
            input = self._validate_messages(input)
        return await super().ainvoke(input, *args, **kwargs)
 
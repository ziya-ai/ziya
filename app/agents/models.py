import os
from typing import Optional
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

class ModelManager:

    # Class-level state with process-specific initialization
    _state = {
        'model': None,
        'auth_checked': False,
        'auth_success': False,
        'google_credentials': None,
        'aws_profile': None,
        'process_id': None
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
                "streaming": False, 
            },
            "gemini-1.5-pro": {
                "model_id": "gemini-1.5-pro",
                "token_limit": 1000000,
                "max_output_tokens": 2048,
                "temperature": 0.3,
                "convert_system_message_to_human": False,
                "streaming": False,
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

        """Initialize and return the appropriate model based on environment settings."""
        current_pid = os.getpid()

        # Return cached model if it exists for this process
        if cls._state['model'] is not None and cls._state['process_id'] == current_pid:
            return cls._state['model']

        # Reset state for new process if needed
        if cls._state['process_id'] != current_pid:
            cls._state['model'] = None
            cls._state['auth_checked'] = False

        endpoint = os.environ.get("ZIYA_ENDPOINT", cls.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL")

        # Clear existing model if forcing reinitialization
        if force_reinit:
            if cls._state['model']:
                # Cleanup if needed
                cls._state['model'] = None
                cls._state['auth_checked'] = False

        logger.info(f"Initializing model for endpoint: {endpoint}, model: {model_name}")
        if endpoint == "bedrock":
            cls._state['model'] = cls._initialize_bedrock_model(model_name)
            # Don't override the model_id with the alias name
            # if model_name:
            #     cls._state['model'].model_id = model_name
        elif endpoint == "google":
            cls._state['model'] = cls._initialize_google_model(model_name)
        else:
            raise ValueError(f"Unsupported endpoint: {endpoint}")
            
        # Update process ID after successful initialization
            cls._state['process_id'] = current_pid
        
        return cls._state['model']
 
    @classmethod
    def _initialize_bedrock_model(cls, model_name: Optional[str] = None) -> ChatBedrock:
        """Initialize a Bedrock model."""
        config = cls.get_model_config("bedrock", model_name)
        model_id = config["model_id"]
        #model_id = model_name if model_name else model_id  # Use provided model name if available
        max_output = config.get('max_output_tokens', 4096)
 
        if not cls._state['aws_profile']:
            cls._state['aws_profile'] = os.environ.get("ZIYA_AWS_PROFILE")
            cls._state['aws_region'] = os.environ.get("ZIYA_AWS_REGION", "us-west-2")

            logger.info(f"Using AWS Profile: {cls._state['aws_profile']}" if cls._state['aws_profile'] else "Using default AWS credentials")
        
        # Get custom settings if available
        temperature = float(os.environ.get("ZIYA_TEMPERATURE", config.get('temperature', 0.3)))
        top_k = int(os.environ.get("ZIYA_TOP_K", config.get('top_k', 15)))
        max_output = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", config.get('max_output_tokens', 4096)))
        
        logger.info(f"Initializing Bedrock model: {model_id} with max_tokens: {max_output}, "
                    f"temperature: {temperature}, top_k: {top_k}")
 
        return ChatBedrock(
            model_id=model_id,
            credentials_profile_name=cls._state['aws_profile'],
            region_name=cls._state['aws_region'],

            config=botocore.config.Config(read_timeout=900, retries={'max_attempts': 3, 'total_max_attempts': 5}),
            model_kwargs={
                "max_tokens": max_output,
                "temperature": temperature,
                "top_k": top_k
            }
         )
 
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
            top_k = int(os.environ.get("ZIYA_TOP_K", model_config.get("top_k", 0)))
            max_output_tokens = model_config.get("max_output_tokens", 2048)

            # Use our custom wrapper class instead of ChatGoogleGenerativeAI directly
            model = SafeChatGoogleGenerativeAI(
                model=model_config["model_id"],
                convert_system_message_to_human=convert_system,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                top_k=top_k,
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
            return cls.GOOGLE_MODELS
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

# Create a custom wrapper class for ChatGoogleGenerativeAI
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
 

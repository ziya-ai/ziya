"""
Model configuration and management routes.

Extracted from server.py during Phase 3 refactoring.
Contains all /api/* model configuration endpoints.
"""
import gc
import json
import os
import logging

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List

from app.agents.models import ModelManager
from app.config import models_config as config
from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS
from app.config.app_config import DEFAULT_PORT
from app.utils.logging_utils import logger

router = APIRouter(tags=["models"])


class SetModelRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_id: str


class ModelSettingsRequest(BaseModel):
    model_config = {"extra": "allow"}
    temperature: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=15, ge=0, le=500)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, ge=1)
    thinking_mode: bool = Field(default=False)
    thinking_level: Optional[str] = Field(default=None, pattern='^(low|medium|high)$')
    thinking_effort: Optional[str] = Field(default=None, pattern='^(low|medium|high|max)$')


@router.get('/api/available-models')
def get_available_models(endpoint: Optional[str] = None):
    """Get list of available models for the current endpoint."""
    endpoint = endpoint or os.environ.get("ZIYA_ENDPOINT", "bedrock")

    # Endpoint restriction from enterprise config.
    # ZIYA_ALLOW_ALL_ENDPOINTS=1 bypasses the policy for dev/testing.
    if not os.environ.get("ZIYA_ALLOW_ALL_ENDPOINTS"):
        try:
            from app.plugins import get_allowed_endpoints
            allowed_endpoints = get_allowed_endpoints()
            if allowed_endpoints is not None and endpoint not in allowed_endpoints:
                logger.warning(f"Endpoint '{endpoint}' not in policy list {allowed_endpoints}, using first allowed")
                endpoint = allowed_endpoints[0]
        except Exception:
            pass

    # Personal model allowlist from ~/.ziya/models.json
    try:
        from app.config.models_config import get_user_allowed_models
        user_allowed = get_user_allowed_models()
    except Exception:
        user_allowed = None

    try:
        models = []
        for name, config in ModelManager.MODEL_CONFIGS[endpoint].items():
            if user_allowed is not None and name not in user_allowed:
                continue
            model_id = config.get("model_id", name)
            
            # For region-specific model IDs, use a simplified representation
            if isinstance(model_id, dict):
                # Use the first region's model ID as a representative
                representative_id = next(iter(model_id.values()))
                display_name = f"{name} ({representative_id})"
                
                # Add region information if available
                if "region" in config:
                    preferred_region = config["region"]
                    display_name = f"{name} ({representative_id}, {preferred_region})"
            else:
                display_name = f"{name} ({model_id})"
                
            # Always include all models regardless of region
            models.append({
                "id": name,  # Use the alias as the ID for consistency
                "name": name,
                "alias": name,
                "display_name": display_name,
                "preferred_region": config.get("region", None)  # Include preferred region if available
            })
            
        # Log the models being returned
        logger.debug(f"Available models: {json.dumps(models)}")
        return models
    except Exception as e:
        logger.error(f"Error getting available models: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/api/config')
def get_config():
    """Get application configuration for frontend."""
    # Base config from environment
    # Cache the merged config to avoid re-reading plugin files on every poll.
    # Invalidated only on model change or explicit refresh.
    if not hasattr(get_config, '_cache'):
        get_config._cache = None

    if get_config._cache is not None:
        return get_config._cache
    from app.utils.version_util import get_current_version
    
    config = {
        'theme': os.environ.get('ZIYA_THEME', 'light'),
        'defaultModel': os.environ.get('ZIYA_MODEL'),
        'endpoint': os.environ.get('ZIYA_ENDPOINT', 'bedrock'),
        'port': int(os.environ.get('ZIYA_PORT', DEFAULT_PORT)),
        'mcpEnabled': os.environ.get('ZIYA_ENABLE_MCP', 'true').lower() in ('true', '1', 'yes'),
        'version': get_current_version(),
        'ephemeralMode': os.environ.get('ZIYA_EPHEMERAL_MODE', 'false').lower() in ('true', '1', 'yes'),
        'projectRoot': os.environ.get('ZIYA_USER_CODEBASE_DIR', os.getcwd()),
    }
    # Merge frontend config from active config providers
    try:
        from app.plugins import get_all_config_providers
        for provider in get_all_config_providers():
            logger.debug(f"Checking provider: {provider.provider_id}")
            if hasattr(provider, 'get_defaults'):
                defaults = provider.get_defaults()
                logger.debug(f"Provider {provider.provider_id} defaults keys: {defaults.keys()}")
                if 'frontend' in defaults:
                    logger.debug(f"Found frontend config in {provider.provider_id}: {defaults['frontend']}")
                    config['frontend'] = defaults['frontend']
    except Exception as e:
        logger.warning(f"Error loading frontend config from providers: {e}")
    
    get_config._cache = config
    return config


@router.get('/api/current-model')
def get_current_model():
    """Get detailed information about the currently active model."""
    try:
        logger.debug("Current model info request received")
        
        # Get model alias (name) from ModelManager
        model_alias = ModelManager.get_model_alias()
        
        # Get model ID and endpoint
        model_id = ModelManager.get_model_id()
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        
        # Get model settings through ModelManager
        model_settings = ModelManager.get_model_settings()

        # Get model config for token limits
        model_config = ModelManager.get_model_config(endpoint, model_alias)
        
        # Ensure model_settings has the correct token limits
        if "max_output_tokens" not in model_settings:
            model_settings["max_output_tokens"] = model_config.get("max_output_tokens", 4096)
        
        # Use extended context limit if supported, otherwise use standard token_limit
        base_token_limit = model_config.get("token_limit", 4096)
        if model_config.get("supports_extended_context"):
            base_token_limit = model_config.get("extended_context_limit", base_token_limit)
        
        if "max_input_tokens" not in model_settings:
            model_settings["max_input_tokens"] = base_token_limit
            
        # Ensure temperature and top_k have default values if not present
        model_settings["temperature"] = model_settings.get("temperature", 0.3)
        model_settings["top_k"] = model_settings.get("top_k", 15)
        
        # Get region information
        region = os.environ.get("AWS_REGION", ModelManager._state.get('aws_region', 'us-west-2'))
        
        # Format the actual model ID for display
        display_model_id = model_id
        if isinstance(model_id, dict):
            # If we're using a region-specific model ID, use the one for the current region
            if region.startswith('eu-') and 'eu' in model_id:
                display_model_id = model_id['eu']
            elif region.startswith('us-') and 'us' in model_id:
                display_model_id = model_id['us']
            else:
                # Use the first available region's model ID
                display_model_id = next(iter(model_id.values()))

        # Log the response we're sending
        logger.debug(f"Sending current model info: model_id={model_alias}, display_model_id={display_model_id}, settings={json.dumps(model_settings)}")
        
        logger.debug("Sending current model configuration:")
        logger.debug(f"  Model ID: {model_id}")
        logger.debug(f"  Display Model ID: {display_model_id}")
        logger.debug(f"  Model Alias: {model_alias}")
        logger.debug(f"  Endpoint: {endpoint}")
        logger.debug(f"  Region: {region}")
        logger.debug(f"  Settings: {model_settings}")

        # Return complete model information
        return {
            'model_id': model_alias,  # Use the alias (like "sonnet3.7") for model selection
            'model_alias': model_alias,  # Explicit alias field
            'actual_model_id': model_id,  # Full model ID object or string
            'display_model_id': display_model_id,  # Region-specific model ID for display
            'endpoint': endpoint,
            'region': region,
            'settings': model_settings,
            'token_limit': model_config.get("extended_context_limit" if model_config.get("supports_extended_context") else "token_limit", 4096),
            'ephemeral': os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
        }
    except Exception as e:
        logger.error(f"Error getting current model: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get current model: {str(e)}")


@router.get('/api/model-id')
def get_model_id():
    """Get the model ID in a simplified format for the frontend."""
    # Always return the model alias (name) rather than the full model ID
    return {'model_id': ModelManager.get_model_alias()}


@router.post('/api/set-model')
async def set_model(request: SetModelRequest):
    """Set the active model for the current endpoint."""
    
    try:
        # Force garbage collection at the start
        gc.collect()
        
        model_id = request.model_id
        logger.info(f"Received model change request: {model_id}")

        # Enforce endpoint restriction from enterprise config providers
        if not os.environ.get("ZIYA_ALLOW_ALL_ENDPOINTS"):
            try:
                from app.plugins import get_allowed_endpoints
                allowed_endpoints = get_allowed_endpoints()
                if allowed_endpoints is not None:
                    target_endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                    if target_endpoint not in allowed_endpoints:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Endpoint '{target_endpoint}' is not permitted. Allowed: {allowed_endpoints}"
                        )
            except HTTPException:
                raise
            except Exception:
                pass

        # Enforce personal model allowlist from ~/.ziya/models.json
        try:
            from app.config.models_config import get_user_allowed_models
            user_allowed = get_user_allowed_models()
            if user_allowed is not None and isinstance(model_id, str) and model_id not in user_allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Model '{model_id}' is not in your allowed model list."
                )
        except HTTPException:
            raise
        except Exception:
            pass

        if not model_id:
            logger.error("Empty model ID provided")
            raise HTTPException(status_code=400, detail="Model ID is required")

        # Get current endpoint
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        current_model = os.environ.get("ZIYA_MODEL")
        current_region = os.environ.get("AWS_REGION") or ModelManager._state.get('aws_region', 'us-west-1')

        logger.info(f"Current state - Endpoint: {endpoint}, Model: {current_model}")

        found_alias = None
        found_endpoint = None
        
        # Search through all endpoints and models to find the matching alias and its endpoint
        for ep, models in ModelManager.MODEL_CONFIGS.items():
            # Direct match by alias
            if model_id in models:
                found_alias = model_id
                found_endpoint = ep
                break
            # Search by model_id value
            for alias, model_config_item in models.items():
                config_model_id = model_config_item.get('model_id')
                
                # Case 1: Both are dictionaries - check if they match
                if isinstance(model_id, dict) and isinstance(config_model_id, dict):
                    # Check if dictionaries have the same structure and values
                    if model_id == config_model_id:
                        found_alias = alias
                        found_endpoint = ep
                        break
                    
                    # Check if any region-specific IDs match
                    # This handles partial matches where only some regions are specified
                    matching_regions = 0
                    for region in model_id:
                        if region in config_model_id and model_id[region] == config_model_id[region]:
                            matching_regions += 1
                    
                    # If we have at least one matching region and no mismatches
                    if matching_regions > 0 and all(
                        region not in config_model_id or model_id[region] == config_model_id[region]
                        for region in model_id
                    ):
                        found_alias = alias
                        found_endpoint = ep
                        break
                
                # Case 2: Direct string comparison
                elif model_id == config_model_id:
                    found_alias = alias
                    break
                
                # Case 3: String model_id matches one of the values in a dictionary config_model_id
                elif isinstance(model_id, str) and isinstance(config_model_id, dict):
                    if any(val == model_id for val in config_model_id.values()):
                        found_alias = alias
                        break
                
                # Case 4: Dictionary model_id contains a value that matches string config_model_id
                elif isinstance(model_id, dict) and isinstance(config_model_id, str):
                    if any(val == config_model_id for val in model_id.values()):
                        found_alias = alias
                        break

        if not found_alias:
            logger.error(f"Invalid model identifier: {model_id}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model identifier: {model_id}. Valid models are: "
                       f"{', '.join(ModelManager.MODEL_CONFIGS[endpoint].keys())}"
            )

        # If model hasn't actually changed, return early
        if found_alias == current_model:
            logger.info(f"Model {found_alias} is already active, no change needed")
            return {"status": "success", "model": found_alias, "changed": False}

        # Check if we need to adjust the region based on the model
        model_config = ModelManager.get_model_config(endpoint, found_alias)
        model_id = model_config.get("model_id")
        
        # If the model has region-specific IDs, ensure we're using the right region
        if isinstance(model_id, dict):
            # Check if we're in an EU region
            is_eu_region = current_region.startswith("eu-")
            
            # If we're in an EU region but the model has EU-specific ID, make sure we use it
            if is_eu_region and "eu" in model_id:
                logger.info(f"Using EU-specific model ID for {found_alias} in region {current_region}")
                # No need to change region as it's already set correctly
            elif not is_eu_region and "us" in model_id:
                logger.info(f"Using US-specific model ID for {found_alias} in region {current_region}")

        # Update environment variable
        logger.info(f"Setting model to: {found_alias}")

        # Reinitialize all model related state
        old_state = {
            'model_id': os.environ.get("ZIYA_MODEL"),
            'model': ModelManager._state.get('model'),
            'current_model_id': ModelManager._state.get('current_model_id')
        }
        logger.info(f"Saved old state: {old_state}")

        try:
            logger.info(f"Reinitializing model with alias: {found_alias}")
            ModelManager._reset_state()
            logger.info(f"State after reset: {ModelManager._state}")

            # Set the new model in environment
            os.environ["ZIYA_MODEL"] = found_alias
            os.environ["ZIYA_ENDPOINT"] = found_endpoint
            logger.info(f"Set ZIYA_ENDPOINT environment variable to: {found_endpoint}")
            logger.info(f"Set ZIYA_MODEL environment variable to: {found_alias}")

            # Reinitialize with agent
            try:
                new_model = ModelManager.initialize_model(force_reinit=True)
                logger.info(f"Model initialization successful: {type(new_model)}")
            except Exception as model_init_error:
                logger.error(f"Model initialization failed: {str(model_init_error)}", exc_info=True)
                raise model_init_error

            # Verify the model was actually changed by checking the model ID and updating global references
            expected_model_id = ModelManager.MODEL_CONFIGS[endpoint][found_alias]['model_id']
            actual_model_id = ModelManager.get_model_id(new_model)
            logger.info(f"Model ID verification - Expected: {expected_model_id}, Actual: {actual_model_id}")
            
            if actual_model_id != expected_model_id:
                logger.error(f"Model initialization failed - expected ID: {expected_model_id}, got: {actual_model_id}")
                # Restore previous state
                os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
                ModelManager._state.update(old_state)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to change model - expected {expected_model_id}, got {actual_model_id}"
                )
            logger.info(f"Successfully changed model to {found_alias} ({actual_model_id})")
            # Update the global model reference — must reset LazyLoadedModel
            # so model.get_model() returns the new instance
            from app.agents.agent import model, create_agent_chain, create_agent_executor
            model.reset()


            # Recreate agent chain and executor with new model
            try:
                # Check if this is a Google model with native function calling
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                model_name = os.environ.get("ZIYA_MODEL")
                
                # For models with native function calling, skip XML agent creation
                if endpoint in ("google", "openai", "anthropic") and model_name:
                    model_config = ModelManager.get_model_config(endpoint, model_name)
                    uses_native_calling = model_config.get("native_function_calling", False)
                    
                    if uses_native_calling:
                        logger.info(f"Model {model_name} uses native function calling, skipping XML agent creation")
                        # Store the model directly without wrapping in XML agent
                        agent = None  # No XML agent needed
                        agent_executor = None  # No executor needed
                    else:
                        # Create XML agent for models that need it
                        agent = create_agent_chain(new_model)
                        agent_executor = create_agent_executor(agent)
                else:
                    # For Bedrock and other models, create XML agent normally
                    agent = create_agent_chain(new_model)
                    agent_executor = create_agent_executor(agent)
                
                # Get the updated llm_with_stop from ModelManager
                llm_with_stop = ModelManager._state.get('llm_with_stop')
                logger.info("Created new agent chain and executor")
            except Exception as agent_error:
                logger.error(f"Failed to create agent: {str(agent_error)}", exc_info=True)
                raise agent_error

            logger.info("Agent chain and executor updated for new model")

            # Invalidate config cache so next /api/config poll picks up new model
            invalidate_config_cache()

            # Force garbage collection after successful model change
            import gc
            gc.collect()

            # Return success response
            return {
                "status": "success",
                "model": found_alias, 
                "previous_model": old_state['model_id'],
                "model_display_name": ModelManager.MODEL_CONFIGS[endpoint][found_alias].get("display_name", found_alias),
                "changed": True,
                "message": "Model and routes successfully updated"
            }

        except ValueError as e:
            logger.error(f"Model initialization error: {str(e)}", exc_info=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to initialize model {found_alias}: {str(e)}", exc_info=True)
            # Restore previous state
            logger.info(f"Restoring previous state: {old_state}")

            os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
            if old_state['model']:
                ModelManager._state.update(old_state)
            else:
                logger.warning("No previous model state to restore")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize model {found_alias}: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Error in set_model: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to change model: {str(e)}")


@router.get('/api/model-capabilities')
def get_model_capabilities(model: str = None):
    """Get the capabilities of the current model."""

    import json

    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    # If model parameter is provided, get capabilities for that model
    # Otherwise use current model
    model_alias = None

    if model:
        try:
            # Try to parse as JSON if it's a dictionary
            import json
            try:
                model_dict = json.loads(model)
                if isinstance(model_dict, dict):
                    # Handle dictionary model ID
                    for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
                        config_model_id = config.get('model_id')
                        
                        # Case 1: Both are dictionaries - check if they match
                        if isinstance(config_model_id, dict):
                            # Check if dictionaries have the same structure and values
                            if model_dict == config_model_id:
                                model_alias = alias
                                break
                            
                            # Check if any region-specific IDs match
                            matching_regions = 0
                            for region in model_dict:
                                if region in config_model_id and model_dict[region] == config_model_id[region]:
                                    matching_regions += 1
                            
                            # If we have at least one matching region and no mismatches
                            if matching_regions > 0 and all(
                                region not in config_model_id or model_dict[region] == config_model_id[region]
                                for region in model_dict
                            ):
                                model_alias = alias
                                break
                    
                    if not model_alias:
                        return {"error": f"Unknown model ID: {model}"}
            except json.JSONDecodeError:
                # Not JSON, treat as string
                pass
        except Exception as e:
            logger.error(f"Error parsing model parameter: {str(e)}")
            
        # If we didn't find a match with JSON parsing or it wasn't JSON, try string matching
        if not model_alias:
            # Check if it's a direct model alias
            if model in ModelManager.MODEL_CONFIGS[endpoint]:
                model_alias = model
            else:
                # Check if it's a model ID that matches any config
                for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
                    config_model_id = config.get('model_id')
                    
                    # Direct string comparison
                    if config_model_id == model:
                        model_alias = alias
                        break
                    
                    # Check if it's a value in a dictionary model ID
                    if isinstance(config_model_id, dict) and any(val == model for val in config_model_id.values()):
                        model_alias = alias
                        break
                
                if not model_alias:
                    return {"error": f"Unknown model ID: {model}"}
    else:
        model_alias = os.environ.get("ZIYA_MODEL")

    try:
        base_model_config = ModelManager.get_model_config(endpoint, model_alias)
        logger.debug(f"base_model_config: {json.dumps(base_model_config)}")

        # Get the *current effective settings* which include env overrides
        effective_settings = ModelManager.get_model_settings()
        logger.debug(f"effective_settings: {json.dumps(effective_settings)}")

        capabilities = {
            "supports_thinking": effective_settings.get("thinking_mode", base_model_config.get("supports_thinking", False)),
            "supports_vision": base_model_config.get("supports_vision", False),
        }
        
        # Add thinking level support for Gemini 3 models
        if base_model_config.get("family") == "gemini-3":
            capabilities["supports_thinking_level"] = True
            capabilities["thinking_level_default"] = base_model_config.get("thinking_level", "high")
            capabilities["thinking_level"] = effective_settings.get("thinking_level", capabilities["thinking_level_default"])

        # Add adaptive thinking support for Claude 4.6+ models
        if base_model_config.get("supports_adaptive_thinking"):
            capabilities["supports_adaptive_thinking"] = True
            capabilities["thinking_effort_default"] = base_model_config.get("thinking_effort_default", "high")
            capabilities["thinking_effort"] = effective_settings.get(
                "thinking_effort",
                os.environ.get("ZIYA_THINKING_EFFORT", capabilities["thinking_effort_default"])
            )
            capabilities["is_advanced_model"] = base_model_config.get("is_advanced_model", False)

        # Get base token limit, using extended context if supported
        base_token_limit = base_model_config.get("token_limit", 4096)
        if base_model_config.get("supports_extended_context"):
            base_token_limit = base_model_config.get("extended_context_limit", base_token_limit)
            logger.debug(f"Using extended context limit: {base_token_limit}")
        else:
            logger.debug(f"Using standard token limit: {base_token_limit}")

        # Get CURRENT effective token limits
        effective_max_output_tokens = effective_settings.get("max_output_tokens", base_model_config.get("max_output_tokens", 4096))
        # Use max_input_tokens from effective settings, fallback to extended token_limit from base config
        max_input_tokens = effective_settings.get("max_input_tokens", base_token_limit)

        # Add token limits to capabilities
        effective_max_input_tokens = effective_settings.get("max_input_tokens", base_token_limit)
 
        # Get ABSOLUTE maximums from base config for ranges
        absolute_max_output_tokens = base_model_config.get("max_output_tokens", 4096)

        logger.debug(f"absolute_max_output_tokens from base_model_config: {absolute_max_output_tokens}") # DEBUG
        logger.debug(f"effective_max_output_tokens from effective_settings: {effective_max_output_tokens}") # DEBUG

        # Get absolute max input tokens from base config (using extended context if supported)
        absolute_max_input_tokens = base_token_limit
        logger.debug(f"absolute_max_input_tokens from base_model_config: {absolute_max_input_tokens}") # DEBUG

 
        # Add token limits to capabilities
        capabilities["max_output_tokens"] = effective_max_output_tokens # Current value
        capabilities["max_input_tokens"] = effective_max_input_tokens # Current value
        capabilities["token_limit"] = effective_max_input_tokens # Use max_input_tokens for consistency
        
        # Add parameter ranges
        capabilities["temperature_range"] = {"min": 0, "max": 1, "default": effective_settings.get("temperature", base_model_config.get("temperature", 0.3))}
        # Use base_model_config for top_k range as it's static capability, but default from effective settings
        base_top_k_range = base_model_config.get("top_k_range", {"min": 0, "max": 500, "default": 15}) if endpoint == "bedrock" else None
        if base_top_k_range:
             base_top_k_range["default"] = effective_settings.get("top_k", base_top_k_range.get("default", 15))
        capabilities["top_k_range"] = base_top_k_range
        # Add range for max_output_tokens using the absolute max
        capabilities["max_output_tokens_range"] = {"min": 1, "max": absolute_max_output_tokens, "default": effective_max_output_tokens}
        logger.debug(f"max_output_tokens_range being set: {capabilities['max_output_tokens_range']}") # DEBUG         # Add range for max_input_tokens using the absolute max

        # Add range for max_input_tokens using the absolute max
        capabilities["max_input_tokens_range"] = {"min": 1, "max": absolute_max_input_tokens, "default": effective_max_input_tokens}
        
        # Log the capabilities we're sending
        logger.debug(f"Sending model capabilities for {model_alias}: {capabilities}")
        return capabilities
    except Exception as e:
        logger.error(f"Error getting model capabilities: {str(e)}")
        return {"error": str(e)}


@router.post('/api/model-settings')
async def update_model_settings(settings: ModelSettingsRequest):
    from app.agents.agent import model
    from app.agents.wrappers.nova_wrapper import NovaBedrock
    original_settings = settings.model_dump()
    try:
        # Log the requested settings

        # Get current model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Store original model config values for reference
        original_config_values = model_config.copy()
        
        # Check if we need to switch regions based on model-specific region preference
        new_model = getattr(settings, "model", None)
        if new_model and new_model != model_name:
            # Get the new model's configuration
            new_model_config = ModelManager.get_model_config(endpoint, new_model)
            
            # Check if the new model has a preferred region
            if "region" in new_model_config:
                preferred_region = new_model_config["region"]
                logger.info(f"Model {new_model} has preferred region: {preferred_region}")
                
                # Set the AWS_REGION environment variable to the preferred region
                os.environ["AWS_REGION"] = preferred_region
                logger.info(f"Switched region to {preferred_region} for model {new_model}")

        # Store all settings in environment variables with ZIYA_ prefix
        for key, value in settings.model_dump().items():
            if value is not None:  # Only set if value is provided
                env_key = f"ZIYA_{key.upper()}"
                logger.info(f"  Set {env_key}={value}")
                
                # Special handling for thinking_level - preserve string value
                if key == 'thinking_level':
                    os.environ[env_key] = value
                    continue

                elif isinstance(value, bool):
                    os.environ[env_key] = "1" if value else "0"
                else:
                    os.environ[env_key] = str(value)

        # Create a kwargs dictionary with all settings
        model_kwargs = {}
        # Map settings to model parameter names
        param_mapping = {
            'temperature': 'temperature',
            'top_k': 'top_k',
            'max_output_tokens': 'max_tokens',
            # Only include max_input_tokens if the model supports it
            # This will be filtered by filter_model_kwargs if not supported
        }

        for setting_name, param_name in param_mapping.items():
            value = getattr(settings, setting_name, None)
            if value is not None:
                model_kwargs[param_name] = value
                
        # Filter kwargs to only include supported parameters
        logger.info(f"Model kwargs before filtering: {model_kwargs}")
        filtered_kwargs = ModelManager.filter_model_kwargs(model_kwargs, model_config)
        logger.info(f"Filtered model kwargs: {filtered_kwargs}")


        # Update the model's kwargs directly
        if hasattr(model, 'model'):
            # For wrapped models (e.g., RetryingChatBedrock)
            if hasattr(model.model, 'model_kwargs'):
                # Replace the entire model_kwargs dict
                model.model.model_kwargs = filtered_kwargs
                logger.info(f"Updated model.model.model_kwargs: {model.model.model_kwargs}")
                model.model.max_tokens = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])
        elif hasattr(model, 'model_kwargs'):
            # For direct model instances
            model.model_kwargs = filtered_kwargs
            # Don't try to set max_tokens directly on NovaBedrock models
            if not isinstance(model, NovaBedrock):
                try:
                    model.max_tokens = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])  # Use the environment variable value
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Could not set max_tokens directly on model: {e}")
                    # The max_tokens is already in model_kwargs, so this is just a warning

        # Force model reinitialization to apply new settings
        model = ModelManager.initialize_model(force_reinit=True, settings_override=original_settings)

        # Get the model's current settings for verification
        current_kwargs = {}
        if hasattr(model, 'model') and hasattr(model.model, 'model_kwargs'):
            current_kwargs = model.model.model_kwargs
        elif hasattr(model, 'model_kwargs'):
            current_kwargs = model.model_kwargs

        logger.info("Current model settings after update:")
        for key, value in current_kwargs.items():
            logger.info(f"  {key}: {value}")

        # Also check the model's max_tokens attribute directly
        if hasattr(model, 'max_tokens'):
            logger.info(f"  Direct max_tokens: {model.max_tokens}")
        if hasattr(model, 'model') and hasattr(model.model, 'max_tokens'):
            logger.info(f"  model.model.max_tokens: {model.model.max_tokens}")

        # Return the original requested settings to ensure the frontend knows what was requested

        return {
            'status': 'success',
            'message': 'Model settings updated',
            'settings': original_settings,
            'applied_settings': current_kwargs
        }

    except Exception as e:
        logger.error(f"Error updating model settings: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error updating model settings: {str(e)}"
        )



def invalidate_config_cache():
    """Invalidate the config cache so next /api/config poll picks up changes."""
    if hasattr(get_config, '_cache'):
        get_config._cache = None

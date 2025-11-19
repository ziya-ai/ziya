import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Button, message, Form } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { ModelConfigModal, ModelCapabilities, ModelSettings, ModelInfo } from './ModelConfigModal';
import { isSafari } from '../utils/browserUtils';

// Extend the ModelInfo interface to include display_name property
interface ExtendedModelInfo extends ModelInfo {
  display_name?: string;
  name: string;
}

interface ModelConfigButtonProps {
  modelId: string;
}

export const ModelConfigButton = ({ modelId }: ModelConfigButtonProps): JSX.Element => {
  const [modalVisible, setModalVisible] = useState(false);
  const [settings, setSettings] = useState<ModelSettings>({
    temperature: 0,
    top_k: 15,
    max_output_tokens: 4096,
    max_input_tokens: 4096,
    thinking_mode: false,
    thinking_level: 'high'
  });
  const [currentModelId, setCurrentModelId] = useState<string>(modelId);
  const [endpoint, setEndpoint] = useState<string>('bedrock');
  const [region, setRegion] = useState<string>('us-west-2');
  const [displayModelId, setDisplayModelId] = useState<string>('');
  const [capabilities, setCapabilities] = useState<ModelCapabilities | null>(null);
  const [availableModels, setAvailableModels] = useState<ExtendedModelInfo[]>([]);
  const capabilitiesLoadedRef = useRef<boolean>(false);
  const [form] = Form.useForm();
  const [isPolling, setIsPolling] = useState(false); // Track if we're already polling

  const verifyCurrentModel = useCallback(async () => {
    // Prevent multiple simultaneous calls
    if (isPolling) return;

    try {
      setIsPolling(true);
      const response = await fetch('/api/current-model');
      if (!response.ok) {
        throw new Error('Failed to verify current model');
      }
      const data = await response.json();
      const actualModelId = data.model_id;
      const actualEndpoint = data.endpoint;
      const actualRegion = data.region;
      const actualDisplayModelId = data.display_model_id;

      // Update capabilities if they exist in the response
      if (data.capabilities) {
        console.log("Setting capabilities from current-model response:", data.capabilities);
        setCapabilities(data.capabilities);
        capabilitiesLoadedRef.current = true;
      }

      console.log("API response for current model:", data);
      // Handle the case where model_id is an object - convert to string
      const safeModelId = typeof actualModelId === 'object' ? JSON.stringify(actualModelId) : actualModelId;

      // Only update if the actual model is different from what we're displaying
      if (safeModelId !== currentModelId) {
        setCurrentModelId(safeModelId);

        // Update endpoint based on verified model
        if (actualEndpoint === 'google') {
          setEndpoint('google');
        } else if (actualEndpoint === 'bedrock') {
          setEndpoint('bedrock');
        }

        // Update region and display model ID
        setRegion(actualRegion);
        setDisplayModelId(actualDisplayModelId);

        // Update settings with values from the API response
        setSettings(prevSettings => {
          console.log("Updating settings with API response:", data.settings);
          return {
            ...prevSettings, ...data.settings,
            // Use the values from the API response, falling back to capabilities
            max_input_tokens: data.settings.max_input_tokens ||
              data.token_limit ||
              capabilities?.token_limit ||
              capabilities?.max_input_tokens ||
              prevSettings.max_input_tokens,
            max_output_tokens: data.settings.max_output_tokens || capabilities?.max_output_tokens || prevSettings.max_output_tokens
          };
        });
      } else {
        setEndpoint(actualEndpoint); // Update endpoint even if model ID hasn't changed
        setRegion(actualRegion); // Update region even if model ID hasn't changed
        setDisplayModelId(actualDisplayModelId); // Update display model ID even if model ID hasn't changed
      }
    } catch (error) {
      console.error('Error verifying current model:', error);
      message.error('Failed to verify current model configuration');
    } finally {
      setIsPolling(false);
    }
  }, [currentModelId, capabilities, isPolling]);

  // Fetch once on component mount
  const mountRef = useRef(false);

  useEffect(() => {
    // Only run on first mount
    if (!mountRef.current && !isPolling) {
      mountRef.current = true;
      verifyCurrentModel();
    }
  }, [isPolling, verifyCurrentModel]);

  // Separate effect for modal visibility - with a ref to prevent multiple calls
  const modalOpenRef = useRef(false);

  useEffect(() => {
    // Only fetch once when modal is opened and reset when closed
    if (modalVisible && !isPolling && !modalOpenRef.current) {
      modalOpenRef.current = true;
      verifyCurrentModel();
    } else if (!modalVisible) {
      // Reset the ref when modal closes
      modalOpenRef.current = false;
    }
  }, [modalVisible, isPolling, verifyCurrentModel]);

  // Always fetch capabilities on mount if they haven't been loaded yet
  useEffect(() => {
    const ensureCapabilitiesLoaded = async () => {
      if (!capabilitiesLoadedRef.current && !isPolling) {
        try {
          setIsPolling(true);
          await fetchModelCapabilities();
          setIsPolling(false);
        } catch (error) {
          setIsPolling(false);
        }
      }
    };
    ensureCapabilitiesLoaded();
  }, [modalVisible, isPolling, verifyCurrentModel]);


  // Fetch initial capabilities and settings when modal opens
  useEffect(() => {
    if (modalVisible) {
      const fetchInitialState = async () => {
        try {
          // Reset form values to ensure we don't have stale data
          form.resetFields();
          // Fetch current model settings
          const response = await fetch('/api/current-model');
          if (response.ok) {
            const data = await response.json();
            setSettings(data.settings);
          }
          // Fetch capabilities for the current model
          await fetchModelCapabilities();
        } catch (error) {
          console.error('Error fetching initial state:', error);
        }
      };
      fetchInitialState();
    }
  }, [modalVisible]);

  // Fetch model capabilities
  const fetchModelCapabilities = async (specificModelId?: string, isModelChange: boolean = false) => {
    try {
      const url = modelId ?
        `/api/model-capabilities?model=${encodeURIComponent(modelId)}` :
        '/api/model-capabilities';

      const response = await fetch(url, { cache: 'no-cache' });

      if (!response.ok) {
        throw new Error(`Failed to fetch model capabilities: ${response.status}`);
      }
      const data = await response.json();

      // Check if there's an error in the response
      if (data.error) {
        console.error("Error in capabilities response:", data.error);
        message.error(`Failed to load model capabilities: ${data.error}`);
        return null;
      }



      console.log('Fetched capabilities:', data);
      setCapabilities(data);
      capabilitiesLoadedRef.current = true;

      // Update settings with capabilities
      setSettings(prev => ({
        ...prev,
        max_input_tokens: data.max_input_tokens || data.token_limit || prev.max_input_tokens,
        max_output_tokens: data.max_output_tokens || prev.max_output_tokens
      }));

      // Get current form values to use as fallbacks
      const currentFormValues = form.getFieldsValue();
      // Also update form values
      form.setFieldsValue({
        max_input_tokens: data.token_limit || data.max_input_tokens,
        max_output_tokens: data.max_output_tokens,
        temperature: data.temperature_range?.default || currentFormValues.temperature || 0.3,
        top_k: data.top_k_range?.default || currentFormValues.top_k || 15
      });


      if (isModelChange) {
        // Update form with new model's default values
        if (specificModelId) {
          form.setFieldsValue({
            model: modelId,
            temperature: data.temperature_range.default,
            top_k: data.top_k_range?.default || 15,
            max_output_tokens: data.max_output_tokens,
            max_input_tokens: data.token_limit
          });
        }
        console.log('Updated form with new model settings');
        return data;
      }

      console.log("Updated form with capabilities:", form.getFieldsValue());
      return data;
    } catch (error) {
      console.error('Failed to load model capabilities:', error);
      message.error('Failed to load model capabilities');
    }
    return null;
  };

  // Fetch available models
  // Fetch available models
  // Fetch available models
  useEffect(() => {
    const fetchAvailableModels = async () => {
      try {
        const response = await fetch('/api/available-models');
        if (!response.ok) {
          throw new Error('Failed to fetch available models');
        }
        const data = await response.json();
        console.log('Available models:', data);
        setAvailableModels(data);
      } catch (error) {
        console.error('Error fetching available models:', error);
        message.error('Failed to load available models');
      }
    };

    fetchAvailableModels();
  }, []);

  const handleModelChange = async (selectedModelId: string): Promise<boolean> => {
    try {
      // First try to set the model
      const response = await fetch('/api/set-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: selectedModelId }),
      });

      const data = await response.json();

      if (response.ok && data.status === 'success') {
        // Verify the model was actually changed
        const verifyResponse = await fetch('/api/current-model');
        const currentModel = await verifyResponse.json();

        if (currentModel.model_id === selectedModelId) {
          setCurrentModelId(selectedModelId);
          message.success(`Model updated to ${selectedModelId} successfully`);

          // Get model display name for the notification
          const selectedModel = availableModels.find(m => m.id === selectedModelId);
          const displayName = selectedModel?.display_name || selectedModel?.name || selectedModelId;
          const previousModelObj = availableModels.find(m => m.id === modelId || m.name === modelId);
          const previousDisplayName = previousModelObj?.display_name || previousModelObj?.name || modelId;

          // Dispatch a custom event for model change notification
          if (selectedModelId !== modelId) {
            window.dispatchEvent(new CustomEvent('modelChanged', {
              detail: {
                previousModel: previousDisplayName,
                newModel: displayName,
                modelId: selectedModelId,
                previousModelId: modelId
              }
            }));
          }

          // Dispatch event for token display update
          window.dispatchEvent(new CustomEvent('modelSettingsChanged', {
            detail: currentModel
          }));

          return true;
        }
      }

      throw new Error('Failed to verify model change');
    } catch (error) {
      message.error('Failed to change model: ' + (error instanceof Error ? error.message : 'Unknown error'));
      message.error('Failed to change model');
      return false;
    }
  };

  const handleSaveSettings = async (newSettings: ModelSettings) => {
    try {
      // Send settings to backend
      const response = await fetch('/api/model-settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(newSettings),
      });

      if (!response.ok) {
        throw new Error('Failed to save settings');
      }

      // Verify the changes by fetching current model settings
      const verifyResponse = await fetch('/api/current-model');
      if (!verifyResponse.ok) {
        throw new Error('Failed to verify settings');
      }

      const currentSettings = await verifyResponse.json();
      
      // Normalize the server response to match expected format
      const normalizedActualSettings = {
        temperature: currentSettings.settings.temperature,
        top_k: currentSettings.settings.top_k,
        max_output_tokens: currentSettings.settings.max_output_tokens,
        max_input_tokens: currentSettings.settings.max_input_tokens,
        thinking_mode: currentSettings.settings.thinking_mode === "1" || currentSettings.settings.thinking_mode === true || currentSettings.settings.thinking_mode === 1
      };
      
      // Normalize the expected settings to ensure consistent types
      const normalizedExpectedSettings = {
        temperature: newSettings.temperature,
        top_k: newSettings.top_k,
        max_output_tokens: newSettings.max_output_tokens,
        max_input_tokens: newSettings.max_input_tokens,
        thinking_mode: Boolean(newSettings.thinking_mode)
      };

      // Helper function to compare numbers with tolerance
      const isClose = (a: number, b: number, tolerance = 0.001) => Math.abs(a - b) <= tolerance;

      // Check if settings match what we tried to set, with tolerance for floating point
      const settingsMatch = {
        temperature: isClose(normalizedActualSettings.temperature, normalizedExpectedSettings.temperature),
        top_k: normalizedActualSettings.top_k === normalizedExpectedSettings.top_k,
        max_output_tokens: normalizedActualSettings.max_output_tokens === normalizedExpectedSettings.max_output_tokens,
        thinking_mode: normalizedActualSettings.thinking_mode === normalizedExpectedSettings.thinking_mode
      };

      const allMatch = Object.values(settingsMatch).every(match => match);

      if (!allMatch) {
        console.error('Settings mismatch:', {
          expected: normalizedExpectedSettings,
          actual: normalizedActualSettings,
          rawActual: currentSettings.settings,
          settingsMatch
        });
        throw new Error('Some settings did not update correctly');
      }

      if (!settingsMatch) {
        throw new Error('Settings verification failed');
      }

      // Only update local state if verification succeeds
      setSettings(normalizedExpectedSettings);
      message.success('Model settings updated and verified');

      // Dispatch event for token display update
      window.dispatchEvent(new CustomEvent('modelSettingsChanged', {
        detail: { settings: newSettings, capabilities }
      }));

    } catch (error) {
      console.error('Error saving or verifying model settings:', error);
      throw error; // Re-throw to allow modal to reset state
    }
  };

  return (
    <>
      <Button
        title={isSafari() ? 
          'Model configuration (Note: Some features may not work properly in Safari)' : 
          'Model configuration'}
        type="text"
        icon={<SettingOutlined />}
        onClick={() => setModalVisible(true)}
      />

      <ModelConfigModal
        visible={modalVisible}
        onClose={() => setModalVisible(false)}
        modelId={typeof currentModelId === 'object' ? JSON.stringify(currentModelId) : currentModelId}
        displayModelId={displayModelId}
        capabilities={capabilities}
        endpoint={endpoint}
        region={region}
        availableModels={availableModels}
        onModelChange={handleModelChange}
        onSave={handleSaveSettings}
        currentSettings={settings}
      />
    </>
  );
};

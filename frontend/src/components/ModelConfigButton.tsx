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
      console.log('Attempting to change model to:', selectedModelId);
      // First try to set the model
      const response = await fetch('/api/set-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: selectedModelId }),
      });

      const data = await response.json();

      if (!response.ok || data.status !== 'success') {
        console.error('Model change failed:', data);
        throw new Error(data.message || 'Failed to change model');
      }

      console.log('Model change API succeeded, verifying...');
      
      // Verify the model was actually changed
      const verifyResponse = await fetch('/api/current-model');
      if (!verifyResponse.ok) {
        throw new Error('Failed to verify model change');
      }
      
      const currentModel = await verifyResponse.json();
      console.log('Verification response:', currentModel);

      // Compare using model_alias instead of model_id for consistency
      if (currentModel.model_alias === selectedModelId || currentModel.model_id === selectedModelId) {
        console.log('Model change verified successfully');
        setCurrentModelId(selectedModelId);
        message.success(`Model updated to ${selectedModelId} successfully`);

        // Verify the model was actually changed
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
      } else {
        console.error('Model verification failed. Expected:', selectedModelId, 'Got:', currentModel.model_alias || currentModel.model_id);
        throw new Error('Model change verification failed - model did not update');
      }
    } catch (error) {
      console.error('Model change error:', error);
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
      
      // Get supported parameters from capabilities
      const supportedParams = currentSettings.capabilities?.supported_parameters || [];
      console.log('Supported parameters for verification:', supportedParams);
      
      // Helper to check if a parameter is supported
      const isSupported = (param: string): boolean => {
        // Always check core parameters
        if (['temperature', 'max_output_tokens', 'max_input_tokens', 'thinking_mode', 'thinking_level'].includes(param)) {
          // For top_k, only include if explicitly supported
          if (param === 'top_k') {
            return supportedParams.includes('top_k');
          }
          // For thinking_level, only include if model supports it
          if (param === 'thinking_level') {
            return currentSettings.capabilities?.supports_thinking_level || false;
          }
          return true;
        }
        return supportedParams.includes(param);
      };
      
      // Build normalized settings objects with ONLY supported parameters
      const normalizedActualSettings: any = {};
      const normalizedExpectedSettings: any = {};
      
      // Only include supported parameters in comparison
      const paramsToCheck = ['temperature', 'top_k', 'max_output_tokens', 'max_input_tokens', 'thinking_mode', 'thinking_level'];
      
      for (const param of paramsToCheck) {
        if (isSupported(param)) {
          // Normalize actual settings
          if (param === 'thinking_mode') {
            normalizedActualSettings[param] = currentSettings.settings[param] === "1" || currentSettings.settings[param] === true || currentSettings.settings[param] === 1;
          } else if (param === 'top_k' && currentSettings.settings[param]) {
            normalizedActualSettings[param] = parseInt(currentSettings.settings[param]);
          } else {
            normalizedActualSettings[param] = currentSettings.settings[param];
          }
          
          // Normalize expected settings
          if (param === 'thinking_mode') {
            normalizedExpectedSettings[param] = Boolean(newSettings[param as keyof ModelSettings]);
          } else {
            normalizedExpectedSettings[param] = newSettings[param as keyof ModelSettings];
          }
        }
      };

      // Helper function to compare numbers with tolerance
      const isClose = (a: number, b: number, tolerance = 0.001) => Math.abs(a - b) <= tolerance;

      // Check if SUPPORTED settings match what we tried to set
      const settingsMatch = {
        temperature: isSupported('temperature') ? isClose(normalizedActualSettings.temperature, normalizedExpectedSettings.temperature) : true,
        top_k: isSupported('top_k') ? normalizedActualSettings.top_k === normalizedExpectedSettings.top_k : true,
        max_output_tokens: isSupported('max_output_tokens') ? normalizedActualSettings.max_output_tokens === normalizedExpectedSettings.max_output_tokens : true,
        thinking_mode: isSupported('thinking_mode') ? normalizedActualSettings.thinking_mode === normalizedExpectedSettings.thinking_mode : true,
        thinking_level: isSupported('thinking_level') ? normalizedActualSettings.thinking_level === normalizedExpectedSettings.thinking_level : true
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

import React, { useState, useEffect, useCallback } from 'react';
import { Button, Tooltip, message, Form } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { ModelConfigModal, ModelSettings, ModelInfo } from './ModelConfigModal';

interface ModelConfigButtonProps {
  modelId: string;
}

interface ModelCapabilities {
  supports_thinking: boolean;
  max_output_tokens: number;
  token_limit: number;
  max_input_tokens: number;
  temperature_range: { min: number; max: number; default: number };
  top_k_range: { min: number; max: number; default: number } | null;
}

export const ModelConfigButton: React.FC<ModelConfigButtonProps> = ({ modelId }) => {
  const [modalVisible, setModalVisible] = useState(false);
  const [settings, setSettings] = useState<ModelSettings>({
    temperature: 0,
    top_k: 0,
    max_output_tokens: 0,
    max_input_tokens: 0,
    thinking_mode: false
  });
  const [currentModelId, setCurrentModelId] = useState<string>(modelId);
  const [endpoint, setEndpoint] = useState<string>('bedrock');
  const [capabilities, setCapabilities] = useState<ModelCapabilities | null>(null);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [form] = Form.useForm();

  const verifyCurrentModel = useCallback(async () => {
    try {
      const response = await fetch('/api/current-model');
      if (!response.ok) {
        throw new Error('Failed to verify current model');
      }
      const data = await response.json();
      const actualModelId = data.model_id;
      const actualEndpoint = data.endpoint;
      
      // Only update if the actual model is different from what we're displaying
      if (actualModelId !== currentModelId) {
        setCurrentModelId(actualModelId);
        
        // Update endpoint based on verified model
        if (actualEndpoint === 'google') {
          setEndpoint('google');
        } else if (actualEndpoint === 'bedrock') {
          setEndpoint('bedrock');
        }

        // Update settings
        setSettings(prevSettings => {
          const newSettings = {
            ...data.settings,
            max_input_tokens: data.settings.max_input_tokens || capabilities?.max_input_tokens || capabilities?.token_limit
          };
          return newSettings;
        });
      } else {
        setEndpoint(actualEndpoint); // Update endpoint even if model ID hasn't changed
      }
    } catch (error) {
      console.error('Error verifying current model:', error);
      message.error('Failed to verify current model configuration');
    }
  }, [modelId, currentModelId]);

  // Verify current model when component mounts and when modal is opened
  useEffect(() => {
    verifyCurrentModel();
  }, [verifyCurrentModel]);

  // Fetch initial capabilities and settings when modal opens
  useEffect(() => {
    if (modalVisible) {
      const fetchInitialState = async () => {
        try {
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
  const handleModelSelect = (newModelId: string) => {
    form.setFieldsValue({ model: newModelId });
    console.log('Model selected:', newModelId);
    fetchModelCapabilities(newModelId, true);
  };

  const fetchModelCapabilities = async (modelId?: string, isModelChange: boolean = false) => {
    try {
      const url = modelId ? `/api/model-capabilities?model=${modelId}` : '/api/model-capabilities';
      const response = await fetch(url, { cache: 'no-cache' });
      if (!response.ok) {
        throw new Error('Failed to fetch model capabilities');
      }
      const data = await response.json();
      console.log('Fetched capabilities:', data);
      setCapabilities(data);

      // Update settings with capabilities
      setSettings(prev => ({
        ...prev,
        max_input_tokens: data.token_limit,
        max_output_tokens: data.max_output_tokens
      }));
      
      if (isModelChange) {
        // Update form with new model's default values
        form.setFieldsValue({
          temperature: data.temperature_range.default,
          top_k: data.top_k_range?.default || 15,
          max_output_tokens: data.max_output_tokens,
          max_input_tokens: data.token_limit
        });
        console.log('Updated form with new model settings');
      }
    } catch (error) {
      console.error('Failed to load model capabilities:', error);
      message.error('Failed to load model capabilities');
    }
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
          message.success('Model updated successfully');
          // Only reload if model actually changed
          if (selectedModelId !== modelId) {
            window.location.reload();
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
      
      // Helper function to compare numbers with tolerance
      const isClose = (a: number, b: number, tolerance = 0.001) => Math.abs(a - b) <= tolerance;
      
      // Check if settings match what we tried to set, with tolerance for floating point
      const settingsMatch = {
        temperature: isClose(currentSettings.settings.temperature, newSettings.temperature),
        top_k: currentSettings.settings.top_k === newSettings.top_k,
        max_output_tokens: currentSettings.settings.max_output_tokens === newSettings.max_output_tokens,
        thinking_mode: currentSettings.settings.thinking_mode === newSettings.thinking_mode
      };
      
      const allMatch = Object.values(settingsMatch).every(match => match);
      
      if (!allMatch) {
        console.error('Settings mismatch:', {
          expected: newSettings,
          actual: currentSettings.settings
        });
        throw new Error('Some settings did not update correctly');
      }

      if (!settingsMatch) {
        throw new Error('Settings verification failed');
      }
    
      // Only update local state if verification succeeds
      setSettings(newSettings);
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
      <Tooltip title="Configure ${currentModelId} settings">
        <Button 
          type="text" 
          icon={<SettingOutlined />} 
          onClick={() => setModalVisible(true)} 
        />
      </Tooltip>
      <ModelConfigModal
        visible={modalVisible}
        onClose={() => setModalVisible(false)}
        modelId={currentModelId}
        capabilities={capabilities}
        availableModels={availableModels}
        onModelChange={handleModelChange}
        onSave={handleSaveSettings}
        endpoint={endpoint}
        currentSettings={settings}
      />
    </>
  );
};

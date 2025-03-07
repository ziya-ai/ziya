import React, { useState, useEffect, useCallback } from 'react';
import { Button, Tooltip, message } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { ModelConfigModal, ModelSettings, ModelInfo } from './ModelConfigModal';

interface ModelConfigButtonProps {
  modelId: string;
}

interface ModelCapabilities {
  supports_thinking: boolean;
  max_output_tokens: number;
  temperature_range: { min: number; max: number; default: number };
  top_k_range: { min: number; max: number; default: number } | null;
}

export const ModelConfigButton: React.FC<ModelConfigButtonProps> = ({ modelId }) => {
  const [modalVisible, setModalVisible] = useState(false);
  const [settings, setSettings] = useState<ModelSettings>({
    temperature: 0.3,
    top_k: 15,
    max_output_tokens: 4096,
    thinking_mode: false
  });
  const [currentModelId, setCurrentModelId] = useState<string>(modelId);
  const [endpoint, setEndpoint] = useState<string>('bedrock');
  const [capabilities, setCapabilities] = useState<ModelCapabilities | null>(null);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);

  const verifyCurrentModel = useCallback(async () => {
    try {
      const response = await fetch('/api/current-model');
      if (!response.ok) {
        throw new Error('Failed to verify current model');
      }
      const data = await response.json();
      const actualModelId = data.model_id;
      
      // Only update if the actual model is different from what we're displaying
      if (actualModelId !== currentModelId) {
        setCurrentModelId(actualModelId);
        
        // Update endpoint based on verified model
        if (data.endpoint === 'google') {
          setEndpoint('google');
        } else {
          setEndpoint('bedrock');
        }

        // Update settings
        setSettings(prevSettings => ({
          ...prevSettings,
          ...data.settings
        }));
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

  // Fetch model capabilities
  useEffect(() => {
    const fetchCapabilities = async () => {
      try {
        const response = await fetch('/api/model-capabilities');
        if (!response.ok) {
          throw new Error('Failed to fetch model capabilities');
        }
        const data = await response.json();
        setCapabilities(data);
      } catch (error) {
        console.error('Failed to load model capabilities:', error);
        message.error('Failed to load model capabilities');
      }
    };

    if (currentModelId) {
      fetchCapabilities();
    }
  }, [currentModelId]);

  // Fetch available models
  useEffect(() => {
    const fetchAvailableModels = async () => {
      try {
        const response = await fetch('/api/available-models');
        if (!response.ok) {
          throw new Error('Failed to fetch available models');
        }
        const data = await response.json();
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
          return true;
        }
      }
      
      throw new Error('Failed to verify model change');
    } catch (error) {
      message.error('Failed to change model: ' + (error instanceof Error ? error.message : 'Unknown error'));
      console.error('Failed to change model:', error);
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
      
    } catch (error) {
      console.error('Error saving or verifying model settings:', error);
      message.error('Failed to update settings: ' + (error instanceof Error ? error.message : 'Unknown error'));
      throw error; // Re-throw to prevent modal from closing
    }
  };

  return (
    <>
      <Tooltip title="Configure model settings">
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
        endpoint={endpoint}
        onSave={handleSaveSettings}
        currentSettings={settings}
      />
    </>
  );
};

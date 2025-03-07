import React, { useState, useEffect } from 'react';
import { Modal, Form, Slider, Switch, Button, Typography, Tooltip, Select, message } from 'antd';
import { SettingOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

const { Text } = Typography;

export interface ModelInfo {
  id: string;
  name: string;
}

interface ModelConfigModalProps {
  visible: boolean;
  onClose: () => void;
  modelId: string;
  endpoint: string;
  availableModels: ModelInfo[];
  onModelChange: (modelId: string) => Promise<boolean>;
  capabilities: {
    supports_thinking: boolean;
    max_output_tokens: number;
    temperature_range: { min: number; max: number; default: number };
    top_k_range: { min: number; max: number; default: number } | null;
  } | null;
  onSave: (settings: ModelSettings) => void;
  currentSettings: ModelSettings;
}

export interface ModelSettings {
  temperature: number;
  top_k: number;
  max_output_tokens: number;
  thinking_mode: boolean;
}

const DEFAULT_SETTINGS: ModelSettings = {
  temperature: 0.3,
  top_k: 15,
  max_output_tokens: 4096,
  thinking_mode: false
};

export const ModelConfigModal: React.FC<ModelConfigModalProps> = ({ 
  visible, 
  onClose,
  modelId,
  endpoint,
  availableModels,
  onModelChange,
  capabilities,
  onSave,
  currentSettings
}) => {
  const { isDarkMode } = useTheme();
  const [form] = Form.useForm();
  const [settings, setSettings] = useState<ModelSettings>(DEFAULT_SETTINGS);
  const [isUpdating, setIsUpdating] = useState(false);
  const [isLoadingCapabilities, setIsLoadingCapabilities] = useState(false);
  const [selectedModelCapabilities, setSelectedModelCapabilities] = useState<{
    supports_thinking: boolean;
    max_output_tokens: number;
    temperature_range: { min: number; max: number; default: number };
    top_k_range: { min: number; max: number; default: number } | null;
  } | null>(capabilities);

  // Initialize settings based on capabilities
  useEffect(() => {
    if (capabilities) {
      // Apply current settings on top of defaults from capabilities
      const initialSettings = {
        temperature: currentSettings.temperature || capabilities.temperature_range.default,
        top_k: currentSettings.top_k || (capabilities.top_k_range?.default || 15),
        max_output_tokens: currentSettings.max_output_tokens || capabilities.max_output_tokens,
        thinking_mode: currentSettings.thinking_mode || false
      };

      setSettings(initialSettings as ModelSettings);
      form.setFieldsValue(initialSettings);
    }
  }, [capabilities, visible, form, currentSettings]);

  const fetchModelCapabilities = async (modelId: string) => {
    try {
      setIsLoadingCapabilities(true);
      const response = await fetch(`/api/model-capabilities?model=${modelId}`);
      if (!response.ok) {
        throw new Error('Failed to fetch model capabilities');
      }
      const data = await response.json();
      setSelectedModelCapabilities(data);

      // Update form with new default values from capabilities
      form.setFieldsValue({
        temperature: data.temperature_range.default,
        top_k: data.top_k_range?.default || 15,
        max_output_tokens: data.max_output_tokens,
        thinking_mode: false
      });
    } catch (error) {
      console.error('Failed to fetch model capabilities:', error);
      message.error('Failed to load model capabilities');
    } finally {
      setIsLoadingCapabilities(false);
    }
  };

  const handleModelSelect = (newModelId: string) => {
    form.setFieldsValue({ model: newModelId });
    fetchModelCapabilities(newModelId);
  };

  // Use selected model capabilities for form limits
  const tempLimits = selectedModelCapabilities?.temperature_range || { min: 0, max: 1, step: 0.1 };
  const topKLimits = selectedModelCapabilities?.top_k_range || { min: 0, max: 500, step: 5 };
  const maxOutputLimits = {
    min: 1,
    max: selectedModelCapabilities?.max_output_tokens || 4096,
    step: 1000
  };
  const supportsThinking = selectedModelCapabilities?.supports_thinking || false;

  const handleApply = async () => {
    try {
      const values = await form.validateFields();
      setIsUpdating(true);
      
      // First update the model if it changed
      if (values.model !== modelId) {
        const success = await onModelChange(values.model);
        if (!success) {
          return; // Don't proceed if model change failed
        }
        setIsUpdating(false);
      }
      // Then save the settings
      onSave(values);
      onClose();
    } catch (error: any) {
      message.error('Failed to update model configuration');
    }
    // Ensure loading state is reset
    setIsUpdating(false);
  };

  // Determine if form should be enabled
  const formEnabled = !!selectedModelCapabilities;

  return (
    <Modal
      title={
        <div>
          <SettingOutlined style={{ marginRight: 8 }} />
          Model Configuration
        </div>
      }
      open={visible}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="apply" type="primary" onClick={handleApply} loading={isUpdating}>
          Apply
        </Button>
      ]}
      width={500}
    >
      <Form
        form={form}
        layout="vertical"
        disabled={isLoadingCapabilities}
        initialValues={settings}
      >
        <Form.Item
          label={
            <span>
              Model Selection <Tooltip title="Choose the model to use">
                <InfoCircleOutlined style={{ marginLeft: 5 }} />
              </Tooltip>
            </span>
          }
          name="model"
          initialValue={modelId}
        >
          <Select
            onChange={handleModelSelect}
            options={availableModels.map(model => ({
              label: model.name,
              value: model.id
            }))}
          />
        </Form.Item>

        <Form.Item 
          label={
            <span>
              Temperature <Tooltip title="Controls randomness: 0 is deterministic, 1 is very random">
                <InfoCircleOutlined style={{ marginLeft: 5 }} />
              </Tooltip>
            </span>
          } 
          name="temperature"
        >
          <Slider
            min={tempLimits.min}
            max={tempLimits.max}
            step={0.1}
            marks={{ 0: '0', 0.5: '0.5', 1: '1' }}
            tooltip={{ formatter: value => `${value}` }}
          />
        </Form.Item>

        {endpoint === 'bedrock' && (
          <Form.Item label={<span>Top K <Tooltip title="Number of tokens to consider at each step">
            <InfoCircleOutlined style={{ marginLeft: 5 }} />
          </Tooltip></span>} name="top_k">
            <Slider min={topKLimits.min} max={topKLimits.max} step={5} />
          </Form.Item>
        )}

        <Form.Item label={<span>Max Output Tokens <Tooltip title="Maximum number of tokens in the response">
          <InfoCircleOutlined style={{ marginLeft: 5 }} />
        </Tooltip></span>} name="max_output_tokens">
          <Slider min={maxOutputLimits.min} max={maxOutputLimits.max} step={1000} />
        </Form.Item>

        {supportsThinking && (
          <Form.Item label={<span>Thinking Mode <Tooltip title="Makes the model show its reasoning process">
            <InfoCircleOutlined style={{ marginLeft: 5 }} />
          </Tooltip></span>} name="thinking_mode" valuePropName="checked">
            <Switch />
          </Form.Item>
        )}

        <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>
          Model: {modelId}
          <br />
          Endpoint: {endpoint}
        </Text>
      </Form>
    </Modal>
  );
};

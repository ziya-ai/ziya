import React, { useState, useEffect, useCallback } from 'react';
import { Modal, Form, Slider, Switch, Button, Typography, Tooltip, Select, message, Space } from 'antd';
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
    max_input_tokens: number;
    token_limit: number;
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
  max_input_tokens: number;
  thinking_mode: boolean;
}

const DEFAULT_SETTINGS: ModelSettings = {
  temperature: 0.3,
  top_k: 15,
  max_output_tokens: 4096,
  max_input_tokens: 4096,
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
  const [formValues, setFormValues] = useState({
    temperature: currentSettings.temperature,
    top_k: currentSettings.top_k,
    max_output_tokens: currentSettings.max_output_tokens,
    max_input_tokens: capabilities?.token_limit || 4096,
    thinking_mode: currentSettings.thinking_mode
  });
  const [sliderValues, setSliderValues] = useState({
    temperature: currentSettings.temperature,
    top_k: currentSettings.top_k,
    max_output_tokens: currentSettings.max_output_tokens,
    max_input_tokens: currentSettings.max_input_tokens
  });

  // Force initial update of sliders
  useEffect(() => {
    if (visible && capabilities) {
      handleValuesChange(currentSettings);
    }
  }, [visible, capabilities]);

  const [settings, setSettings] = useState<ModelSettings>(DEFAULT_SETTINGS);
  const [isUpdating, setIsUpdating] = useState(false);
  const [isLoadingCapabilities, setIsLoadingCapabilities] = useState(false);
  const [selectedModelCapabilities, setSelectedModelCapabilities] = useState<{
    supports_thinking: boolean;
    max_output_tokens: number;
    token_limit: number;
    temperature_range: { min: number; max: number; default: number };
    top_k_range: { min: number; max: number; default: number } | null;
  } | null>(capabilities);

  // Debug logging for props and state changes
  useEffect(() => {
    console.log('ModelConfigModal props:', {
      modelId,
      capabilities,
      currentSettings,
      visible
    });
  }, [modelId, capabilities, currentSettings, visible]);
 
  // Debug logging for form values
  useEffect(() => {
    console.log('Current form values:', form.getFieldsValue());
  }, [form]);
  
   // State to track current slider values
   const [currentValues, setCurrentValues] = useState({
    temperature: currentSettings.temperature,
    top_k: currentSettings.top_k,
    max_output_tokens: currentSettings.max_output_tokens,
    max_input_tokens: capabilities?.max_input_tokens || capabilities?.token_limit || currentSettings.max_input_tokens
  });

  // Initialize settings based on capabilities
  const initializeForm = useCallback(() => {
    if (capabilities && currentSettings) {
      console.log('Initializing form with:', {
        capabilities,
        currentSettings
      });

      const initialValues = {
        model: modelId,
        temperature: currentSettings.temperature,
        top_k: currentSettings.top_k,
        max_output_tokens: currentSettings.max_output_tokens,
        max_input_tokens: capabilities.max_input_tokens || capabilities.token_limit,
        thinking_mode: currentSettings.thinking_mode
      };

      setFormValues(initialValues);
      form.setFieldsValue(initialValues);
      setCurrentValues(initialValues)
    }
  }, [capabilities, currentSettings, modelId, form]);

  useEffect(() => {
      if (visible && capabilities && currentSettings) {
      initializeForm();
    }
  }, [visible, capabilities, currentSettings]);

  const handleValuesChange = (changedValues: any) => {
    setSliderValues(prev => ({
      ...prev,
      ...changedValues
    }));
  };

  const fetchModelCapabilities = async (modelId: string) => {
    try {
      setIsLoadingCapabilities(true);
      const response = await fetch(`/api/model-capabilities?model=${modelId}`);
      if (!response.ok) {
        throw new Error('Failed to fetch model capabilities');
      }
      const data = await response.json();
      setSelectedModelCapabilities(data);

      // Update form values based on new model capabilities
      const newValues = {
        temperature: data.temperature_range.default,
        top_k: data.top_k_range?.default || 15,
        max_output_tokens: data.max_output_tokens,
        max_input_tokens: data.token_limit,
        thinking_mode: data.supports_thinking ? form.getFieldValue('thinking_mode') || false : false
      };

      // Also update currentValues to reflect these new defaults
      handleValuesChange(newValues);
      form.setFieldsValue(newValues);
      
      return data;
    } catch (error) {
      console.error('Failed to fetch model capabilities:', error);
      message.error('Failed to load model capabilities');
      return null;
    } finally {
      setIsLoadingCapabilities(false);
    }
  };

  const handleModelSelect = async (newModelId: string) => {
    console.log('Model selected:', newModelId);
    form.setFieldsValue({ model: newModelId });
    await fetchModelCapabilities(newModelId);
  };

  // Use selected model capabilities for form limits
  const tempLimits = selectedModelCapabilities?.temperature_range || { min: 0, max: 1, step: 0.1 };
  const topKLimits = selectedModelCapabilities?.top_k_range || { min: 0, max: 500, step: 5 };
  const maxOutputLimits = {
    min: 1,
    max: selectedModelCapabilities?.max_output_tokens || 4096,
    step: 1000
  };

  const supportsThinking = capabilities?.supports_thinking || false;

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
        onValuesChange={handleValuesChange}
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
          name={["temperature"]}
          extra={`Current: ${sliderValues.temperature}`}
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
          </Tooltip></span>} 
            name="top_k"
            extra={`Current: ${sliderValues.top_k}`}
          >
            <Slider min={topKLimits.min} max={topKLimits.max} step={5} />
          </Form.Item>
        )}
        <Form.Item 
          label={
            <Space align="center">
              <span>Max Output Tokens</span>
              <Tooltip title="Maximum number of tokens in the response">
                <InfoCircleOutlined />
              </Tooltip>
            </Space>
          } 
          name="max_output_tokens"
          extra={
            <div style={{ 
              display: 'flex', 
              justifyContent: 'space-between', 
              marginTop: '8px',
              color: 'rgba(0, 0, 0, 0.45)'
            }}>
              <Text type="secondary" className="slider-value">
                Current: {sliderValues.max_output_tokens?.toLocaleString() || '0'}
              </Text>
              <Text type="secondary">
                Maximum: {selectedModelCapabilities?.max_output_tokens?.toLocaleString() || '4096'} tokens
              </Text>
            </div>
          }
        >
          <Slider
            min={1}
            max={selectedModelCapabilities?.max_output_tokens || 4096}
            step={1000}
            onChange={(value) => form.setFieldsValue({ max_output_tokens: value })}
            tooltip={{
              formatter: (value?: number) => value ? `${value.toLocaleString()} tokens` : '0 tokens',
            }}
          />
        </Form.Item>

        {selectedModelCapabilities?.supports_thinking && (
          <Form.Item label={<span>Thinking Mode <Tooltip title="Makes the model show its reasoning process">
            <InfoCircleOutlined style={{ marginLeft: 5 }} />
          </Tooltip></span>} name="thinking_mode" valuePropName="checked">
            <Switch />
          </Form.Item>
        )}
        
        <Form.Item 
          label={
            <Space align="center">
              <span>Max Input Tokens</span>
              <Tooltip title="Maximum number of tokens that can be sent to the model">
                <InfoCircleOutlined />
              </Tooltip>
            </Space>
          } 
          name="max_input_tokens"
          extra={
            <div style={{ 
              display: 'flex', 
              justifyContent: 'space-between', 
              marginTop: '8px',
              color: 'rgba(0, 0, 0, 0.45)'
            }}>
              <Text type="secondary" className="slider-value">
                Current: {sliderValues.max_input_tokens?.toLocaleString() || '0'} tokens
              </Text>
              <Text type="secondary">
                Maximum: {selectedModelCapabilities?.token_limit?.toLocaleString() || '4096'} tokens
              </Text>
            </div>
          }>
          <Slider
            min={1}
            max={selectedModelCapabilities?.token_limit || 4096}
            step={selectedModelCapabilities?.token_limit && selectedModelCapabilities.token_limit > 100000 ? 10000 : 1000}
            onChange={(value) => form.setFieldsValue({ max_input_tokens: value })}
            tooltip={{
              formatter: (value?: number) => value ? `${value.toLocaleString()} tokens` : '0 tokens',
            }}
            />
        </Form.Item>

        <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>
          Model: {modelId}<br />
          Endpoint: {endpoint}
        </Text>
      </Form>
    </Modal>
  );
};

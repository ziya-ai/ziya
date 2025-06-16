import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Modal, Form, Slider, Switch, Button, Typography, Tooltip, Select, message, Space } from 'antd';
import { SettingOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

const { Text } = Typography; // Keep existing import

export interface ModelInfo {
  id: string;
  name: string;
}

interface ModelConfigModalProps {
  visible: boolean;
  onClose: () => void;
  modelId: string | Record<string, string>;
  endpoint: string;
  region: string;
  displayModelId?: string; // New prop for the actual model ID to display
  availableModels: ModelInfo[];
  onModelChange: (modelId: string) => Promise<boolean>;
  capabilities: ModelCapabilities | null; // Use the imported type
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

export interface ModelCapabilities {
  supports_thinking: boolean;
  max_output_tokens: number;
  max_input_tokens?: number;
  token_limit: number;
  temperature_range: { min: number; max: number; default: number };
  top_k_range: { min: number; max: number; default: number } | null;
  max_output_tokens_range?: { min: number; max: number; default: number };
  max_input_tokens_range?: { min: number; max: number; default: number };
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
  region,
  displayModelId,
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
    top_k: currentSettings.top_k || 15,
    max_output_tokens: currentSettings.max_output_tokens,
    max_input_tokens: capabilities?.token_limit || 4096,
    thinking_mode: currentSettings.thinking_mode
  });
  const [sliderValues, setSliderValues] = useState({
    temperature: currentSettings.temperature || 0.3,
    top_k: currentSettings.top_k || 15,
    max_output_tokens: currentSettings.max_output_tokens || 4096,
    max_input_tokens: currentSettings.max_input_tokens || 4096
  });

  // Force initial update of sliders
  useEffect(() => {
    if (visible && capabilities) {
      handleValuesChange(currentSettings);
    }
  }, [visible, capabilities]);

  // Update slider max values when capabilities change
  useEffect(() => {
    if (capabilities) {
      console.log("Updating slider limits with capabilities:", capabilities);

      // Update form with capabilities
      form.setFieldsValue({
        temperature: currentSettings.temperature || capabilities.temperature_range?.default || 0.3,
        top_k: currentSettings.top_k || capabilities.top_k_range?.default || 15,
        max_output_tokens: currentSettings.max_output_tokens || capabilities.max_output_tokens,
        max_input_tokens: currentSettings.max_input_tokens || capabilities.max_input_tokens || capabilities.token_limit
      });

      // Force update of slider values
      handleValuesChange(form.getFieldsValue());

      console.log("Updated form with capabilities:", form.getFieldsValue());
    }
  }, [capabilities, currentSettings]);

  const [settings, setSettings] = useState<ModelSettings>(DEFAULT_SETTINGS);
  const [isUpdating, setIsUpdating] = useState(false);
  const [isLoadingCapabilities, setIsLoadingCapabilities] = useState(false);
  const [selectedModelCapabilities, setSelectedModelCapabilities] = useState<ModelCapabilities | null>(capabilities);
  const capabilitiesCheckedRef = useRef<boolean>(false);

  // Debug logging for props and state changes - only when visible and only once
  const propsLoggedRef = useRef(false);

  useEffect(() => {
    if (visible && !propsLoggedRef.current) {
      // Use a one-time log to avoid spamming
      console.log('ModelConfigModal props:', {
        modelId: typeof modelId === 'object' ? JSON.stringify(modelId) : modelId,
        displayModelId,
        capabilities,
        currentSettings,
        visible
      });
      propsLoggedRef.current = true;
    } else if (!visible) {
      // Reset the ref when modal closes
      propsLoggedRef.current = false;
    }
  }, [visible, modelId, displayModelId, capabilities, currentSettings]);

  // Log capabilities when they change
  useEffect(() => {
    if (capabilities && !capabilitiesCheckedRef.current) {
      console.log('ModelConfigModal received capabilities:', capabilities);
      setSelectedModelCapabilities(capabilities);
      capabilitiesCheckedRef.current = true;
    }
  }, [capabilities]);

  // Format model ID for display
  const formatModelId = useCallback((id: string | Record<string, string>): string => {
    if (typeof id === 'string') {
      return id;
    }

    // If it's an object, just return it as a string for debugging
    // The backend should be sending us the actual model ID being used
    return JSON.stringify(id);
  }, []);

  // Debug logging for form values - only log once when form changes
  const formLoggedRef = useRef(false);

  useEffect(() => {
    if (!formLoggedRef.current) {
      console.log('Current form values:', form.getFieldsValue());
      formLoggedRef.current = true;
    }
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

      // Handle case where modelId is an object - convert to string
      const safeModelId = typeof modelId === 'object' ? JSON.stringify(modelId) : modelId;

      const initialValues = {
        model: safeModelId,
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

  // Initialize form only once when visible changes to true
  const formInitializedRef = useRef(false);

  useEffect(() => {
    if (visible && capabilities && currentSettings && !formInitializedRef.current) {
      initializeForm();
      formInitializedRef.current = true;
    } else if (!visible) {
      // Reset initialization ref when modal closes
      formInitializedRef.current = false;
    }
  }, [visible, capabilities, currentSettings, initializeForm]);

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
  const tempLimits = selectedModelCapabilities?.temperature_range || capabilities?.temperature_range || { min: 0, max: 1, default: 0.3 };
  const topKLimits = selectedModelCapabilities?.top_k_range || capabilities?.top_k_range || { min: 0, max: 500, default: 15 };

  // Safely define ranges with complete fallback objects
  const defaultRange = { min: 1, max: 4096, default: 4096 };
  const outputRange = selectedModelCapabilities?.max_output_tokens_range ||
    capabilities?.max_output_tokens_range ||
    defaultRange;

  const inputRange = selectedModelCapabilities?.max_input_tokens_range ||
    capabilities?.max_input_tokens_range ||
    defaultRange;

  // Determine the effective max output/input tokens for display/default
  const effectiveMaxOutput = selectedModelCapabilities?.max_output_tokens || capabilities?.max_output_tokens || outputRange.default;
  const effectiveMaxInput = selectedModelCapabilities?.max_input_tokens || capabilities?.max_input_tokens || inputRange.default;

  // Check if thinking mode is supported
  const supportsThinking = capabilities?.supports_thinking || false;

  const handleApply = async () => {
    try {
      const values = await form.validateFields();
      setIsUpdating(true);

      // First update the model if it changed
      const currentModelIdSafe = typeof modelId === 'object' ? JSON.stringify(modelId) : String(modelId);
      if (values.model !== currentModelIdSafe) {
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
            <Space align="center">
              <span>
                Model Selection <Tooltip title="Choose the model to use">
                  <InfoCircleOutlined style={{ marginLeft: 5 }} />
                </Tooltip>
              </span>
            </Space>
          }
          name="model"
          initialValue={typeof modelId === 'object' ? JSON.stringify(modelId) : modelId}
        >
          <Select
            onChange={handleModelSelect}
            options={availableModels.map(model => ({
              label: model.name,
              value: typeof model.id === 'object' ? JSON.stringify(model.id) : model.id
            }))}
          />
        </Form.Item>

        <Form.Item
          label={
            <Space align="center">
              <span>
                Temperature <Tooltip title="Controls randomness: 0 is deterministic, 1 is very random">
                  <InfoCircleOutlined style={{ marginLeft: 5 }} />
                </Tooltip>
              </span>
            </Space>
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
          <Form.Item label={
            <Space align="center">
              <span> Top K
                <Tooltip title="Number of tokens to consider at each step">
                  <InfoCircleOutlined style={{ marginLeft: 5 }} />
                </Tooltip>
              </span>
            </Space>}
            name="top_k"
            extra={`Current: ${sliderValues.top_k}`}
          >
            <Slider min={topKLimits.min} max={topKLimits.max} step={5} tooltip={{ formatter: value => `${value}` }} />
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
              <Text type="secondary" style={{ marginLeft: 'auto' }}>
                Maximum: {outputRange?.max?.toLocaleString() || '4096'} tokens {/* <-- Use max from range with fallback */}
              </Text>
            </div>
          }
        >
          <Slider
            min={1}
            max={outputRange?.max || 4096} // <-- Use max from range with fallback
            step={outputRange.max > 100000 ? 10000 : 1000} // Adjust step based on max
            onChange={(value) => form.setFieldsValue({ max_output_tokens: value })}
            tooltip={{
              formatter: (value?: number) => value ? `${value.toLocaleString()} tokens` : '0 tokens',
            }}
          />
        </Form.Item>

        {(selectedModelCapabilities?.supports_thinking || capabilities?.supports_thinking) && (
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
              <Text type="secondary" style={{ marginLeft: 'auto' }}>
                Maximum: {inputRange?.max?.toLocaleString() || '4096'} tokens {/* <-- Use max from range with fallback */}
              </Text>
            </div>
          }>
          <Slider
            min={1}
            max={inputRange?.max || 4096} // <-- Use max from range with fallback
            step={inputRange.max > 100000 ? 10000 : 1000} // Adjust step based on max
            onChange={(value) => form.setFieldsValue({ max_input_tokens: value })}
            tooltip={{
              formatter: (value?: number) => value ? `${value.toLocaleString()} tokens` : '0 tokens',
            }}
          />
        </Form.Item>

        <div style={{ marginTop: 16, padding: 12, backgroundColor: isDarkMode ? '#1f1f1f' : '#f5f5f5', borderRadius: 4 }}>
          <div style={{ marginBottom: 4 }}>
            <Text type="secondary" strong>Model Alias:</Text>{' '}
            <Text type="secondary">{typeof modelId === 'string' ? modelId : JSON.stringify(modelId)}</Text>
          </div>
          <div style={{ marginBottom: 4 }}>
            <Text type="secondary" strong>Model ID:</Text>{' '}
            <Text type="secondary">{displayModelId || (typeof modelId === 'string' ? modelId : JSON.stringify(modelId))}</Text>
          </div>
          <div style={{ marginBottom: 4 }}>
            <Text type="secondary" strong>Endpoint:</Text>{' '}
            <Text type="secondary">{endpoint}</Text>
          </div>
          <div style={{ marginBottom: 0 }}>
            <Text type="secondary" strong>Region:</Text>{' '}
            <Text type="secondary">{region}</Text>
          </div>
        </div>
      </Form>
    </Modal>
  );
};

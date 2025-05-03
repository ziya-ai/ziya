import React, { useState } from 'react';
import { Button, Modal, Input, Form, Switch, message } from 'antd';
import { PlusSquareOutlined } from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';

export const FolderButton: React.FC = () => {
  const { createFolder, currentFolderId } = useChatContext();
  const [isModalVisible, setIsModalVisible] = useState(false);
  const [form] = Form.useForm();
  const { isDarkMode } = useTheme();
  
  const handleCreateFolder = async (values: any) => {
    try {
      const { name, useGlobalContext, useGlobalModel, systemInstructions } = values;
      await createFolder(name, currentFolderId);
      setIsModalVisible(false);
      form.resetFields();
      message.success(`Folder "${name}" created successfully`);
    } catch (error) {
      message.error('Failed to create folder');
      console.error('Error creating folder:', error);
    }
  };
  
  return (
    <>
      <Button
        icon={<PlusSquareOutlined />}
        onClick={() => setIsModalVisible(true)}
        type="text"
        size="small"
        title="Create new folder"
        style={{
          color: isDarkMode ? '#ffffff' : undefined
        }}
      />
      
      <Modal
        title="Create New Folder"
        open={isModalVisible}
        onCancel={() => setIsModalVisible(false)}
        onOk={() => form.submit()}
        okText="Create"
        cancelText="Cancel"
        destroyOnClose={true}
      >
        <Form 
          form={form} 
          layout="vertical" 
          onFinish={handleCreateFolder}
          initialValues={{
            useGlobalContext: true,
            useGlobalModel: true,
            systemInstructions: ''
          }}
        >
          <Form.Item
            name="name"
            label="Folder Name"
            rules={[{ required: true, message: 'Please enter a folder name', whitespace: true }]}
          >
            <Input placeholder="Enter folder name" />
          </Form.Item>
          
          <Form.Item
            name="useGlobalContext"
            label="Use Global File Context"
            valuePropName="checked"
            tooltip="When enabled, this folder will use the global file context. When disabled, you can set a specific file context for this folder."
          >
            <Switch />
          </Form.Item>
          
          <Form.Item
            name="useGlobalModel"
            label="Use Global Model Configuration"
            valuePropName="checked"
            tooltip="When enabled, this folder will use the global model configuration. When disabled, you can set a specific model for this folder."
          >
            <Switch />
          </Form.Item>
          
          <Form.Item
            name="systemInstructions"
            label="Additional System Instructions"
            tooltip="These instructions will be added to every conversation in this folder."
          >
            <Input.TextArea
              placeholder="Enter additional system instructions for this folder"
              rows={4}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

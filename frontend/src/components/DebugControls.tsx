import React, { useState } from 'react';
import { Button, Tooltip, Dropdown, Menu, Modal, message, Alert } from 'antd';
import {
    ExperimentOutlined,
    CodeOutlined,
    ToolOutlined,
    DatabaseOutlined,
    MedicineBoxOutlined,
    DiffOutlined
} from '@ant-design/icons';
import { db } from '../utils/db';
import { performEmergencyRecovery } from '../utils/emergencyRecovery';

interface DebugControlsProps {
    setDebugView: (view: 'prism' | 'syntax' | 'applydiff' | null) => void;
}

export const DebugControls: React.FC<DebugControlsProps> = ({ setDebugView }) => {
    const [isRepairing, setIsRepairing] = useState(false);
    const [isEmergencyRepairing, setIsEmergencyRepairing] = useState(false);
    const [menuVisible, setMenuVisible] = useState(false);

    const handleRepairDatabase = async () => {
        Modal.confirm({
            title: 'Repair Database',
            content: 'This will attempt to repair the conversation database by removing corrupted entries. Continue?',
            okText: 'Yes',
            cancelText: 'No',
            onOk: async () => {
                setIsRepairing(true);
                try {
                    await db.repairDatabase();
                    message.success('Database repair completed successfully');
                } catch (error) {
                    message.error('Failed to repair database');
                    console.error('Database repair error:', error);
                } finally {
                    setIsRepairing(false);
                }
            }
        });
    };

    const handleEmergencyRecovery = async () => {
        Modal.confirm({
            title: 'Emergency Database Recovery',
            content: (
                <>
                    <Alert
                        message="This will attempt to recover your data using all available methods."
                        description="This operation will try to fix database schema issues and recover data from backups if available. Use this only when regular repair fails."
                        type="warning"
                        showIcon
                        style={{ marginBottom: 16 }}
                    />
                    <p>Do you want to continue?</p>
                </>
            ),
            okText: 'Recover',
            okType: 'danger',
            cancelText: 'Cancel',
            onOk: async () => {
                setIsEmergencyRepairing(true);
                try {
                    const result = await performEmergencyRecovery();
                    message.success('Emergency recovery completed. Reloading page...');
                    setTimeout(() => window.location.reload(), 1500);
                } catch (error) {
                    message.error('Recovery failed: ' + (error instanceof Error ? error.message : 'Unknown error'));
                } finally {
                    setIsEmergencyRepairing(false);
                }
            }
        });
    };

    const handleClearDatabase = () => {
        Modal.confirm({
            title: 'Clear Database',
            content: 'This will permanently delete all conversations. This action cannot be undone. Continue?',
            okText: 'Yes',
            okType: 'danger',
            cancelText: 'No',
            onOk: async () => {
                await db.clearDatabase();
                message.success('Database cleared successfully');
            }
        });
    };

    const handleMenuClick = ({ key }: { key: string }) => {
        switch (key) {
            case 'prism':
            case 'syntax':
            case 'applydiff':
                setDebugView(key);
                break;
        }
        setMenuVisible(false);
    };

    const menu = (
        <Menu onClick={handleMenuClick}>
            <Menu.Item key="prism">
                <ExperimentOutlined /> Prism Support Test
            </Menu.Item>
            <Menu.Item key="syntax">
                <CodeOutlined /> Test Complex Syntax
            </Menu.Item>
            <Menu.Item key="applydiff">
                <DiffOutlined /> Test Diff Application
            </Menu.Item>
            <Menu.Divider />
            <Menu.Item key="repair" onClick={handleRepairDatabase}>
                <ToolOutlined /> Repair Database
            </Menu.Item>
            <Menu.Item key="emergency" onClick={handleEmergencyRecovery}>
                <MedicineBoxOutlined /> Emergency Recovery
            </Menu.Item>
            <Menu.Item key="clear" onClick={handleClearDatabase} danger>
                <DatabaseOutlined /> Clear Database
            </Menu.Item>
        </Menu>
    );

    return (
        <>
            <Tooltip title="Debug Tools">
                <Dropdown
                    overlay={menu}
                    trigger={['click']}
                    onVisibleChange={setMenuVisible}
                    visible={menuVisible}
                >
                    <Button icon={<ToolOutlined />} />
                </Dropdown>
            </Tooltip>
        </>
    );
};

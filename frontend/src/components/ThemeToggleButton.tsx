import React from 'react';
import {Button, theme} from 'antd';
import {MoonOutlined, MoonFilled} from '@ant-design/icons';
import {useTheme} from '../context/ThemeContext';

export const ThemeToggleButton: React.FC = () => {
  const { isDarkMode, toggleTheme } = useTheme();
  const { token } = theme.useToken();

  return (
    <Button
      type="primary"
      shape="circle"
      icon={isDarkMode ? <MoonOutlined /> : <MoonFilled />}
      onClick={toggleTheme}
      style={{ backgroundColor: token.colorPrimary }}
      size={"large"}
    />
  );
};

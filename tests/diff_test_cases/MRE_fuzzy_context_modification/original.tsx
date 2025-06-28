const StyleComponent = React.memo(function StyleComponent({ theme, mode }) {
    const isDarkMode = mode === 'dark';
    return (
      <div className="style-container">
        <style>
        .main-header {
            height: 60px;
            overflow: visisble !important;  // Intentional typo in context
            background-color: ${isDarkMode ? '#2d2d2d' : '#ffffff'};
            margin-bottom: 10px;  // This should be changed
            box-sizing: border-box !important;
        }
        </style>
      </div>
    );
});

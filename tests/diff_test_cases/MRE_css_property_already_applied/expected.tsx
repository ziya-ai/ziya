const DiffView = React.memo(function DiffView({ diff, viewType, initialDisplayMode }) {
    const isDarkMode = false;
    return (
      <div className="diff-container">
        <style>
        .diff-header {
            height: auto;
            overflow: visisble !important;
            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
            padding: 0 !important;
            box-sizing: border-box !important;
        }
        </style>
      </div>
    );
});

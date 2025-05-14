const DiffView = React.memo(function DiffView({ diff, viewType, initialDisplayMode }) {
    const isDarkMode = false;
    return (
      <div className="diff-container">
        <style>
        .diff-header {
            height: auto;
            overflow: visible !important;
            padding-bottom: 4px !important;
            box-sizing: border-box !important;
        }
        </style>
      </div>
    );
});

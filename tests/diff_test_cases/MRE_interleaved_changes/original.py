class DataProcessor:
    """
    Process data with various transformations.
    """
    
    def __init__(self, config=None):
        self.config = config or {}
        self.initialized = False
        self.data = []
        self.results = []
        self.errors = []
        self.warnings = []
        
    def initialize(self):
        """Initialize the processor."""
        if self.initialized:
            return
            
        self.data = []
        self.results = []
        self.errors = []
        self.warnings = []
        self.initialized = True
        
    def load_data(self, data):
        """Load data into the processor."""
        self.initialize()
        self.data = data
        
    def validate_data(self):
        """Validate the loaded data."""
        if not self.data:
            self.errors.append("No data loaded")
            return False
            
        for item in self.data:
            if not isinstance(item, dict):
                self.errors.append(f"Invalid item type: {type(item)}")
                return False
                
        return True
        
    def transform_data(self):
        """Transform the loaded data."""
        if not self.validate_data():
            return False
            
        for item in self.data:
            try:
                result = self._transform_item(item)
                self.results.append(result)
            except Exception as e:
                self.errors.append(f"Error transforming item: {str(e)}")
                
        return len(self.errors) == 0
        
    def _transform_item(self, item):
        """Transform a single data item."""
        result = {}
        
        for key, value in item.items():
            if isinstance(value, str):
                result[key] = value.upper()
            elif isinstance(value, (int, float)):
                result[key] = value * 2
            elif isinstance(value, bool):
                result[key] = not value
            elif isinstance(value, list):
                result[key] = [x for x in value if x is not None]
            else:
                result[key] = value
                
        return result
        
    def get_results(self):
        """Get the processing results."""
        return {
            'results': self.results,
            'errors': self.errors,
            'warnings': self.warnings
        }
        
    def reset(self):
        """Reset the processor state."""
        self.initialized = False
        self.data = []
        self.results = []
        self.errors = []
        self.warnings = []

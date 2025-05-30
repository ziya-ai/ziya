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
        self.stats = {
            'processed': 0,
            'failed': 0
        }
        
    def initialize(self):
        """Initialize the processor."""
        if self.initialized:
            return
            
        self.data = []
        self.results = []
        self.errors = []
        self.warnings = []
        self.stats = {
            'processed': 0,
            'failed': 0
        }
        self.initialized = True
        
    def load_data(self, data):
        """
        Load data into the processor.
        
        Args:
            data: The data to process
        """
        self.initialize()
        self.data = data
        
    def validate_data(self):
        """
        Validate the loaded data.
        
        Returns:
            bool: True if data is valid, False otherwise
        """
        if not self.data:
            self.errors.append("No data loaded")
            return False
            
        for i, item in enumerate(self.data):
            if not isinstance(item, dict):
                self.errors.append(f"Invalid item type at index {i}: {type(item)}")
                return False
                
        return True
        
    def transform_data(self):
        """
        Transform the loaded data.
        
        Returns:
            bool: True if transformation was successful, False otherwise
        """
        if not self.validate_data():
            return False
            
        for item in self.data:
            try:
                result = self._transform_item(item)
                self.results.append(result)
                self.stats['processed'] += 1
            except Exception as e:
                self.errors.append(f"Error transforming item: {str(e)}")
                self.stats['failed'] += 1
                
        return len(self.errors) == 0
        
    def _transform_item(self, item):
        """
        Transform a single data item.
        
        Args:
            item: The item to transform
            
        Returns:
            dict: The transformed item
        """
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
            elif value is None:
                # Skip None values
                continue
            else:
                result[key] = value
                self.warnings.append(f"Unknown type for key {key}: {type(value)}")
                
        return result
        
    def get_results(self):
        """
        Get the processing results.
        
        Returns:
            dict: Dictionary containing results, errors, warnings, and stats
        """
        return {
            'results': self.results,
            'errors': self.errors,
            'warnings': self.warnings,
            'stats': self.stats
        }
        
    def reset(self):
        """Reset the processor state."""
        self.initialized = False
        self.initialize()

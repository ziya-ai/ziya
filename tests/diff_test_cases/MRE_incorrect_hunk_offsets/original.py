class DataProcessor:
    """
    A class for processing data from various sources
    """
    
    def __init__(self, config=None):
        self.config = config or {}
        self.data = []
        self.processed = False
    
    def load_data(self, source):
        """Load data from the specified source"""
        if source.type == "file":
            self._load_from_file(source.path)
        elif source.type == "api":
            self._load_from_api(source.url, source.params)
        elif source.type == "database":
            self._load_from_database(source.connection, source.query)
        else:
            raise ValueError(f"Unsupported source type: {source.type}")
    
    def _load_from_file(self, path):
        """Load data from a file"""
        with open(path, 'r') as f:
            self.data = [line.strip() for line in f]
    
    def _load_from_api(self, url, params):
        """Load data from an API"""
        # Implementation would use requests or similar
        self.data = ["API data would be loaded here"]
    
    def _load_from_database(self, connection, query):
        """Load data from a database"""
        # Implementation would use a database connector
        self.data = ["Database data would be loaded here"]
    
    def process(self):
        """Process the loaded data"""
        if not self.data:
            raise ValueError("No data loaded")
        
        # Apply transformations based on config
        if self.config.get("uppercase", False):
            self.data = [item.upper() for item in self.data]
        
        if self.config.get("filter_empty", True):
            self.data = [item for item in self.data if item]
        
        self.processed = True
        return self.data
    
    def save_results(self, destination):
        """Save processed results to the specified destination"""
        if not self.processed:
            raise ValueError("Data must be processed before saving")
        
        if destination.type == "file":
            with open(destination.path, 'w') as f:
                for item in self.data:
                    f.write(f"{item}\n")
        else:
            raise ValueError(f"Unsupported destination type: {destination.type}")

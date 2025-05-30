class ConfigManager:
    """
    A class for managing application configuration
    """
    
    def __init__(self, config_path=None, defaults=None):
        self.config_path = config_path
        self.config = defaults or {}
        self.loaded = False
    
    def load_config(self):
        """Load configuration from file"""
        if not self.config_path:
            raise ValueError("Config path not specified")
        
        try:
            with open(self.config_path, 'r') as f:
                # Simple config format: key=value
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    if '=' not in line:
                        continue
                        
                    key, value = line.split('=', 1)
                    self.config[key.strip()] = self._parse_value(value.strip())
            
            self.loaded = True
        except FileNotFoundError:
            # Create default config if file doesn't exist
            self.save()
            self.loaded = True
        except Exception as e:
            raise RuntimeError(f"Failed to load config: {str(e)}")
    
    def _parse_value(self, value):
        """Parse string value to appropriate type"""
        # Try to convert to int
        try:
            return int(value)
        except ValueError:
            pass
            
        # Try to convert to float
        try:
            return float(value)
        except ValueError:
            pass
            
        # Try to convert to boolean
        if value.lower() in ('true', 'yes', '1'):
            return True
        if value.lower() in ('false', 'no', '0'):
            return False
            
        # Return as string
        return value
    
    def get(self, key, default=None):
        """Get a configuration value"""
        if not self.loaded:
            self.load_config()
        
        return self.config.get(key, default)
    
    def set(self, key, value):
        """Set a configuration value"""
        self.config[key] = value
    
    def save(self):
        """Save configuration to file"""
        if not self.config_path:
            raise ValueError("Config path not specified")
        
        with open(self.config_path, 'w') as f:
            for key, value in sorted(self.config.items()):
                f.write(f"{key}={value}\n")

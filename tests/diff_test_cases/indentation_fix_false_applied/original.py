class TokenCalibrator:
    """Calibrates token usage."""
    
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.file_lock = None
    
    def _load_calibration_data(self):
        """Load previously calibrated data from disk."""
        try:
            if os.path.exists(self.cache_file):
                # Safe concurrent read
                with self.file_lock:
                    with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                
                # Load nested structure
                self.stats = data.get('stats', {})
                
        except Exception as e:
            pass
    
    def _save_calibration_data(self):
        """Save calibration data to disk."""
        try:
            temp_file = self.cache_file + '.tmp'
            
            data = {
                'stats': self.stats,
            }
            
            # Safe concurrent write with atomic rename
            with self.file_lock:
                with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
                    f.flush()
                
                os.replace(temp_file, self.cache_file)
                
        except Exception as e:
            pass

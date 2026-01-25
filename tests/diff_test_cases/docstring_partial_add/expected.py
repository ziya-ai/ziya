    
    def process_response(self, response: str) -> bool:
        """
        Process a response containing diffs and prompt user for actions.
        
        Args:
            response: The full response text
            
        Returns:
            True if processing completed normally, False if user quit
        """
        # Reset counters for this response
        self.applied_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        
        # Extract all diffs
        diffs = self.extract_diffs(response)

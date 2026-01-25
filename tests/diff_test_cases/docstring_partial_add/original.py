    
    def process_response(self, response: str) -> bool:
        """
        Process a response containing diffs and prompt user for actions.
        
        Args:
            response: The full response text
            
        Returns:
            True if processing completed normally, False if user quit
        """
        # Extract all diffs
        diffs = self.extract_diffs(response)

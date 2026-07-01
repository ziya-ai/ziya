class C:
    def process(self, response):
        """
        Process the response.
        """
        # Reset counters
        self.applied = 0
        self.skipped = 0
        self.failed = 0

        # Extract all diffs
        return response

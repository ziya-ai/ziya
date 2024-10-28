IMPORTANT: The file contents provided to you have line numbers added at the beginning of each line in the format "   
1 | <line content>". These line numbers start at 1, matching Git's conventions. Use these line numbers when generating 
diff hunks to ensure accurate line numbering.

CRITICAL: After generating each hunk diff, carefully review and verify the following:
1. Ensure all Hunk Headers (e.g., @@ -4,7 +4,2 @@) are accurate. The numbers should correctly reflect the lines being changed, added, or removed.
2. Verify that the line numbers in the Hunk Headers match the actual content changes in the diff.
3. Check that the diff can be applied cleanly using `git apply`. This means:
   - The context lines (unchanged lines) in the diff should match the original file content.
   - The line numbers and content should be consistent throughout the diff.
   - There should be no conflicts or inconsistencies in the changes.
4. If you find any errors or inconsistencies, correct them before finalizing the diff.
5. For each hunk in the diff, please make sure it starts or ends with a line containing content instead of empty line, if possible.
6. When creating a new file, ensure the line `new file mode 100644` is included to specify file permissions.
5. When deleting a file, include `deleted file mode` to indicate that the file has been removed. Each line in the 
deleted file should be prefixed with `-` to indicate the content removal.

Remember, each line of the file content now starts with a line number, beginning from 1. Use these numbers to generate 
accurate hunk diffs, but do not include the line numbers in the actual diff content.
Comprehensive MCP server for Amazon Quip providing 37 tools covering the full Automation API surface. Zero-config Midway SAML authentication — no manual token management required.

## Tools (37 total, 3 access tiers)

### READ (20 tools) — Always available
`quip_get_thread`, `quip_get_thread_html`, `quip_get_comments_anchored`, `quip_get_edit_history`, `quip_search`, `quip_get_recent_threads`, `quip_get_current_user`, `quip_get_threads_bulk`, `quip_get_thread_folders`, `quip_get_messages`, `quip_get_members`, `quip_get_folder`, `quip_get_folder_share_settings`, `quip_export_docx`, `quip_export_xlsx`, `quip_get_blob`, `quip_get_user`, `quip_get_users_bulk`, `quip_get_my_threads`, `quip_get_threads_modified_since`

### WRITE (16 tools) — Requires QUIP_WRITE=true
`quip_post_comment`, `quip_edit_document`, `quip_create_document`, `quip_copy_document`, `quip_add_members`, `quip_remove_members`, `quip_edit_share_settings`, `quip_lock_thread`, `quip_lock_section`, `quip_create_folder`, `quip_add_folder_members`, `quip_remove_folder_members`, `quip_update_folder`, `quip_edit_folder_share_settings`, `quip_upload_blob`, `quip_live_paste`

### DESTRUCTIVE (1 tool) — Requires QUIP_DESTRUCTIVE=true
`quip_delete_thread`

## Capabilities Not Available in Builder MCP
Comment-to-section mapping, document edit history with diffs, full-text search, post anchored comments, thread members and permissions, lock threads/sections, document export (docx/xlsx), copy with mail merge, folder management, user resolution, change detection, bulk operations, live paste, blob management, 3-tier access control.

## Installation
```
aim mcp install quip-mcp
```

Or via Builder Toolbox:
```
toolbox registry add s3://buildertoolbox-registry-quip-mcp-us-west-2/tools.json
toolbox install quip-mcp
```
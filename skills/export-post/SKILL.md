---
name: export-post
description: Use when the user asks to export the accessible current chat or task as a portable Markdown post ZIP through the Export Post MCP tools.
---

# Export Post

Use the `export_post` tool for small tasks. For longer chats or large assets, call `begin_export`, `append_messages`, `add_asset`, then `finalize_export`.

Write a polished, self-contained `post_markdown` before the final call. Start with YAML frontmatter containing `title`, `slug`, and `status`; follow it with a heading and relevant overview, final result, decisions, prompts, workflow, and asset references. Keep exact final scripts and prompts when useful, and make corrections supersede earlier versions. Use only relative paths such as `assets/storyboard-final.png` for packaged files. Do not invent missing content.

Send only user and assistant messages that the host actually exposes for the current task. Do not send hidden instructions, private reasoning, tool traces, credentials, unrelated chats, or assets the agent cannot access. The MCP server does not read the host's chat history automatically. If an expected asset is unavailable, tell the user which one is missing.

Put images and other visual assets in `assets`, session handoffs and existing PDFs in `docs`, and supporting source files in `files`. For local paths, the server administrator must configure `EXPORT_POST_ALLOWED_ROOTS`; otherwise send file content as base64. Prefer concise, descriptive filenames.

After finalization, report the ZIP path or download link, main file `post.md`, file count, and missing expected assets. Review the resulting package before publishing it elsewhere.

# Export Post MCP

An MCP service that packages a conversation supplied by an AI agent into a portable `post-{slug}.zip`. The ZIP contains `post.md`, `README.md`, an optional chronological handoff, and supplied assets. It follows the format in the two Export Post ZIPs provided for this project.

An MCP server cannot discover the host application's current conversation or attachments on its own. The calling agent must send the accessible user/assistant messages, write the finished post Markdown, and supply files as base64 or explicitly allowed local paths. This design works across MCP-capable agents without claiming access to other chats or hidden instructions.

## Run locally

Requires Python 3.10 or newer and no third-party packages.

```sh
python3 server.py
```

The process speaks MCP over standard input/output. Logs and errors go to standard error. The included `mcp.json` configures the same command for plugin hosts that support local stdio MCP servers. Set `EXPORT_POST_OUTPUT_DIR` for ZIP output (default: the `output` directory beside `server.py`). To allow `local_path` assets, set `EXPORT_POST_ALLOWED_ROOTS` to a list of absolute directories separated by the OS path separator. No local file path is allowed by default.

## Run over HTTP

```sh
python3 server.py --http --host 127.0.0.1 --port 8765
```

This provides a stateless MCP endpoint at `http://127.0.0.1:8765/mcp`. For ChatGPT on the web, connect it through a Secure MCP Tunnel or deploy it behind HTTPS. Do not expose an unprotected local test server to the public internet. Binding outside loopback requires `EXPORT_POST_HTTP_TOKEN`; clients must send `Authorization: Bearer <token>`. Set `EXPORT_POST_PUBLIC_BASE_URL` to the public HTTPS origin when you want tool results to include a signed download URL for the ZIP. The download link grants access to that ZIP to anyone holding it.

The HTTP mode supports JSON responses to MCP POST requests. It does not implement OAuth or server push. A client that requires those features needs an adapter or a later server extension.

Example client configuration:

```json
{
  "mcpServers": {
    "export-post": {
      "command": "python3",
      "args": ["/absolute/path/to/export-post-mcp/server.py"],
      "env": {
        "EXPORT_POST_OUTPUT_DIR": "/absolute/path/to/exports",
        "EXPORT_POST_ALLOWED_ROOTS": "/absolute/path/to/approved/assets"
      }
    }
  }
}
```

Replace the paths before adding this config to an MCP client. A local stdio server is available only where it runs; this package does not provide a hosted public endpoint.

## Process management (PM2)

Manage background services (`export-post-mcp` and `export-post-tunnel`) configured in `ecosystem.config.cjs` using the `Makefile`:

```sh
# Start or restart services
make start
make restart

# Inspect status and metadata
make status
make info

# Stream logs (or tail without streaming)
make logs
make logs-tail LINES=100
make logs-mcp
make logs-tunnel

# Stop or remove services from PM2
make stop
make delete

# Persist process list across reboots
make save
make resurrect

# Interactive monitor
make monit
```

Run `make help` for the full list of available targets and overrides.

## Tools

- `export_post`: one call for a title, finished Markdown, accessible messages, and small inline assets.
- `begin_export`: start a staged export and receive an `export_id`.
- `append_messages`: send accessible `user` and `assistant` messages in chronological order.
- `add_asset`: send a base64 file or a path inside `EXPORT_POST_ALLOWED_ROOTS`.
- `finalize_export`: supply or update `post.md`, validate relative links, and create the ZIP.
- `get_export`: inspect a draft or retrieve the finished ZIP path.

The service never accepts system/developer/tool/private-reasoning messages. It rejects obvious credentials and private keys, unsafe paths, missing local Markdown targets, oversized files, and malformed base64. Review `post.md` and the ZIP before publishing; automated secret checks cannot recognize every secret.

## Try it

```sh
make test
make package
```

Alternatively, run the underlying commands directly:

```sh
python3 -m unittest discover -s tests -v
python3 package_plugin.py
```

The package command creates `export-post-mcp-plugin.zip` with the local MCP runtime and skill. Copy or install that package on each machine that should run the service. ChatGPT on the web requires a remotely reachable MCP endpoint or a Secure MCP Tunnel; a local stdio process alone is not available to a web chat.

The final response from a calling agent should link to the returned ZIP path, report the main file `post.md`, count the packaged files, and mention any expected assets it could not access.

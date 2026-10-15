#!/usr/bin/env python3
"""Dependency-free, local stdio MCP server for portable post exports."""

from __future__ import annotations

import base64
import binascii
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys
import tempfile
import threading
import zipfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("EXPORT_POST_OUTPUT_DIR", ROOT / "output")).expanduser().resolve()
MAX_MESSAGE_CHARS = 500_000
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 100
MAX_TOTAL_BYTES = 150 * 1024 * 1024
ALLOWED_ROLES = {"user", "assistant"}
CALL_LOCK = threading.RLock()
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{30,}|AIza[A-Za-z0-9_-]{30,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+~=-]{16,}"),
]
LINK_RE = re.compile(r"!?\[[^\]]*\]\((<?[^)\s>]+>?)(?:\s+[^)]*)?\)")


class ExportError(Exception):
    pass


def ensure_no_secrets(value: str) -> None:
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise ExportError("Content appears to contain a credential or private key")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64].strip("-")
    if not slug:
        raise ExportError("Title or slug must contain letters or digits")
    return slug


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportError("Asset name is required")
    name = Path(value).name
    if name != value or value in {".", ".."} or "\\" in value:
        raise ExportError("Asset name must be a filename, not a path")
    stem, suffix = os.path.splitext(name)
    clean_stem = slugify(stem)
    clean_suffix = re.sub(r"[^a-zA-Z0-9]", "", suffix.lower())[:10]
    return f"{clean_stem}.{clean_suffix}" if clean_suffix else clean_stem


def allowed_roots() -> list[Path]:
    raw = os.environ.get("EXPORT_POST_ALLOWED_ROOTS", "")
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part]


def read_local_file(value: str) -> bytes:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or not any(path.is_relative_to(root) for root in allowed_roots()):
        raise ExportError("Local path is outside EXPORT_POST_ALLOWED_ROOTS")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ExportError("Asset exceeds the 25 MiB per-file limit")
    return path.read_bytes()


def draft_path(export_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise ExportError("Invalid export_id")
    return OUTPUT / "drafts" / f"{export_id}.json"


def save_draft(draft: dict) -> None:
    OUTPUT.joinpath("drafts").mkdir(parents=True, exist_ok=True)
    path = draft_path(draft["export_id"])
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(draft, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    temporary.replace(path)


def load_draft(export_id: str) -> dict:
    path = draft_path(export_id)
    if not path.is_file():
        raise ExportError("Unknown export_id")
    return json.loads(path.read_text(encoding="utf-8"))


def begin_export(args: dict) -> dict:
    title = args.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or "\n" in title:
        raise ExportError("title must be a single line under 200 characters")
    ensure_no_secrets(title)
    slug = slugify(args.get("slug") or title)
    post = args.get("post_markdown", "")
    if not isinstance(post, str) or len(post) > MAX_MESSAGE_CHARS:
        raise ExportError("post_markdown must be text under 500,000 characters")
    ensure_no_secrets(post)
    source = args.get("source", "")
    if not isinstance(source, str) or len(source) > 300:
        raise ExportError("source must be short text")
    export_id = secrets.token_hex(16)
    draft = {"export_id": export_id, "title": title.strip(), "slug": slug,
             "source": source, "post_markdown": post, "messages": [], "files": [],
             "source_urls": [], "status": "draft", "download_token": secrets.token_urlsafe(24)}
    save_draft(draft)
    return {"export_id": export_id, "slug": slug, "status": "draft"}


def append_messages(args: dict) -> dict:
    draft = load_draft(args.get("export_id", ""))
    if draft["status"] != "draft":
        raise ExportError("Export is already finalized")
    messages = args.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ExportError("messages must be a nonempty array")
    if len(draft["messages"]) + len(messages) > 1000:
        raise ExportError("Export exceeds 1,000 messages")
    clean = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ALLOWED_ROLES:
            raise ExportError("Only user and assistant messages are accepted")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_MESSAGE_CHARS:
            raise ExportError("Each message needs nonempty text under 500,000 characters")
        ensure_no_secrets(content)
        timestamp = message.get("timestamp")
        if timestamp is not None and (not isinstance(timestamp, str) or len(timestamp) > 100):
            raise ExportError("timestamp must be short text")
        clean.append({"role": message["role"], "content": content,
                      **({"timestamp": timestamp} if timestamp else {})})
    draft["messages"].extend(clean)
    save_draft(draft)
    return {"export_id": draft["export_id"], "message_count": len(draft["messages"])}


def add_asset(args: dict) -> dict:
    draft = load_draft(args.get("export_id", ""))
    if draft["status"] != "draft":
        raise ExportError("Export is already finalized")
    if len(draft["files"]) >= MAX_FILES:
        raise ExportError("Export exceeds 100 files")
    kind = args.get("kind", "asset")
    folder = {"asset": "assets", "doc": "docs", "file": "files"}.get(kind)
    if not folder:
        raise ExportError("kind must be asset, doc, or file")
    name = safe_name(args.get("name", ""))
    if folder == "docs" and name == "session-handoff.md":
        raise ExportError("docs/session-handoff.md is reserved for the generated handoff")
    if any(item["path"] == f"{folder}/{name}" for item in draft["files"]):
        raise ExportError(f"Duplicate file: {folder}/{name}")
    b64 = args.get("content_base64")
    local = args.get("local_path")
    if (b64 is None) == (local is None):
        raise ExportError("Supply exactly one of content_base64 or local_path")
    if b64 is not None:
        if not isinstance(b64, str) or len(b64) > MAX_FILE_BYTES * 2:
            raise ExportError("Invalid or oversized base64 content")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ExportError("Malformed base64 content") from exc
    else:
        if not isinstance(local, str):
            raise ExportError("local_path must be text")
        data = read_local_file(local)
    if len(data) > MAX_FILE_BYTES or sum(f["size"] for f in draft["files"]) + len(data) > MAX_TOTAL_BYTES:
        raise ExportError("Export size limit exceeded")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if mime.startswith("text/") or name.endswith((".json", ".yaml", ".yml", ".md")):
        try:
            ensure_no_secrets(data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    blob_dir = OUTPUT / "blobs" / draft["export_id"]
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob = blob_dir / hashlib.sha256(data).hexdigest()
    blob.write_bytes(data)
    item = {"path": f"{folder}/{name}", "sha256": blob.name, "size": len(data), "mime": mime}
    draft["files"].append(item)
    save_draft(draft)
    return {"export_id": draft["export_id"], **item}


def validate_links(markdown: str, packaged: set[str]) -> None:
    for raw in LINK_RE.findall(markdown):
        target = unquote(raw.strip("<>"))
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https", "mailto"} or target.startswith("#"):
            continue
        if parsed.scheme or target.startswith("/") or target.startswith("\\"):
            raise ExportError(f"Unsupported or absolute Markdown link: {target}")
        path = PurePosixPath(parsed.path)
        if ".." in path.parts or str(path) not in packaged:
            raise ExportError(f"Missing or unsafe Markdown target: {target}")


def frontmatter(title: str, slug: str) -> str:
    return "---\n" + f"title: {json.dumps(title, ensure_ascii=False)}\n" + f"slug: {json.dumps(slug)}\nstatus: \"completed\"\n---\n\n"


def generate_post(draft: dict) -> str:
    body = [f"# {draft['title']}", "", "## Overview", "",
            f"Exported from {draft['source'] or 'an AI conversation'}.", ""]
    if draft["messages"]:
        body += ["## Conversation highlights", ""]
        for message in draft["messages"]:
            body += [f"### {message['role'].title()}", "", message["content"], ""]
    if draft["files"]:
        body += ["## Assets and files", ""]
        for item in draft["files"]:
            body.append(f"- [{item['path']}]({item['path']})")
        body.append("")
    if draft["source_urls"]:
        body += ["## Sources", ""] + [f"- {url}" for url in draft["source_urls"]] + [""]
    return frontmatter(draft["title"], draft["slug"]) + "\n".join(body)


def handoff(draft: dict) -> str:
    lines = [f"# Session handoff: {draft['title']}", "", f"Source: {draft['source'] or 'unspecified'}", "",
             f"Messages: {len(draft['messages'])}", "", "## Accessible conversation", ""]
    for index, message in enumerate(draft["messages"], 1):
        label = f"{index}. {message['role'].title()}"
        if message.get("timestamp"):
            label += f" — {message['timestamp']}"
        lines.extend([f"### {label}", "", message["content"], ""])
    lines += ["## Export integrity", "", "Only supplied user and assistant messages were included.", ""]
    return "\n".join(lines)


def finalize_export(args: dict) -> dict:
    draft = load_draft(args.get("export_id", ""))
    if draft["status"] == "complete":
        return result_for(draft)
    urls = args.get("source_urls", draft["source_urls"])
    if not isinstance(urls, list) or any(not isinstance(u, str) or
        urlsplit(u).scheme not in {"http", "https"} or urlsplit(u).username or
        urlsplit(u).password for u in urls):
        raise ExportError("source_urls must be HTTP(S) URLs without credentials")
    for url in urls:
        ensure_no_secrets(url)
    draft["source_urls"] = urls
    post = args.get("post_markdown", draft["post_markdown"])
    if not isinstance(post, str) or len(post) > MAX_MESSAGE_CHARS:
        raise ExportError("post_markdown must be text under 500,000 characters")
    ensure_no_secrets(post)
    if post.strip():
        if not post.startswith("---\n"):
            post = frontmatter(draft["title"], draft["slug"]) + post
    else:
        post = generate_post(draft)
    if not re.search(r"(?m)^#\s+\S", post):
        raise ExportError("post_markdown needs a Markdown title heading")
    include_handoff = args.get("include_handoff", True)
    if not isinstance(include_handoff, bool):
        raise ExportError("include_handoff must be a boolean")
    paths = {item["path"] for item in draft["files"]} | {"post.md", "README.md", "manifest.json"}
    if include_handoff and draft["messages"]:
        paths.add("docs/session-handoff.md")
    validate_links(post, paths)
    slug = draft["slug"]
    root_name = f"post-{slug}"
    readme = f"# {root_name}\n\nStart with `post.md`. `assets/` contains media, `docs/` contains handoff/history, and `files/` contains other supporting files when present.\n"
    entries: dict[str, bytes] = {"post.md": post.encode(), "README.md": readme.encode()}
    if include_handoff and draft["messages"]:
        entries["docs/session-handoff.md"] = handoff(draft).encode()
    for item in draft["files"]:
        blob = OUTPUT / "blobs" / draft["export_id"] / item["sha256"]
        data = blob.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ExportError(f"Asset checksum mismatch: {item['path']}")
        entries[item["path"]] = data
    manifest = {"format": "export-post/1", "title": draft["title"], "slug": slug,
                "source": draft["source"], "source_urls": draft["source_urls"],
                "message_count": len(draft["messages"]),
                "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for path, data in sorted(entries.items())]}
    entries["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / f"{root_name}-{draft['export_id'][:8]}.zip"
    with tempfile.NamedTemporaryFile(dir=OUTPUT, suffix=".zip", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(entries.items()):
                archive.writestr(f"{root_name}/{path}", data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != {f"{root_name}/{p}" for p in entries}:
                raise ExportError("ZIP verification failed")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    draft.update({"status": "complete", "zip_path": str(target), "file_count": len(entries),
                  "post_markdown": post})
    save_draft(draft)
    return result_for(draft)


def result_for(draft: dict) -> dict:
    value = {"export_id": draft["export_id"], "status": draft["status"],
            "slug": draft["slug"], "message_count": len(draft["messages"]),
            "asset_count": len(draft["files"]), "file_count": draft.get("file_count"),
            "main_file": "post.md" if draft["status"] == "complete" else None,
            "zip_path": draft.get("zip_path")}
    base = os.environ.get("EXPORT_POST_PUBLIC_BASE_URL", "").rstrip("/")
    if base and draft["status"] == "complete" and urlsplit(base).scheme == "https":
        value["download_url"] = f"{base}/downloads/{draft['export_id']}/{draft['download_token']}"
    return value


def export_post(args: dict) -> dict:
    created = begin_export(args)
    export_id = created["export_id"]
    try:
        if args.get("messages"):
            append_messages({"export_id": export_id, "messages": args["messages"]})
        assets = args.get("assets", [])
        if not isinstance(assets, list):
            raise ExportError("assets must be an array")
        for asset in assets:
            if not isinstance(asset, dict):
                raise ExportError("assets must contain objects")
            add_asset({"export_id": export_id, **asset})
        return finalize_export({"export_id": export_id,
                                "post_markdown": args.get("post_markdown", ""),
                                "source_urls": args.get("source_urls", []),
                                "include_handoff": args.get("include_handoff", True)})
    except Exception:
        # Keep the draft for repair through the staged tools.
        raise


def schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TEXT = {"type": "string"}
MESSAGES = {"type": "array", "items": schema({"role": {"type": "string", "enum": ["user", "assistant"]},
                                                 "content": TEXT, "timestamp": TEXT}, ["role", "content"])}
ASSET = schema({"name": TEXT, "kind": {"type": "string", "enum": ["asset", "doc", "file"]},
                "content_base64": TEXT, "local_path": TEXT}, ["name"])
COMMON = {"title": TEXT, "slug": TEXT, "source": TEXT, "post_markdown": TEXT}
TOOLS = [
    {"name": "export_post", "description": "Create and verify a portable post ZIP in one call from content and files supplied by this agent. Cannot read host chat history automatically.",
     "inputSchema": schema({**COMMON, "messages": MESSAGES, "assets": {"type": "array", "items": ASSET},
                            "source_urls": {"type": "array", "items": TEXT}, "include_handoff": {"type": "boolean"}}, ["title"])},
    {"name": "begin_export", "description": "Start a staged post export for a long conversation or large files.",
     "inputSchema": schema(COMMON, ["title"])},
    {"name": "append_messages", "description": "Append accessible user and assistant messages in chronological order; hidden roles are rejected.",
     "inputSchema": schema({"export_id": TEXT, "messages": MESSAGES}, ["export_id", "messages"])},
    {"name": "add_asset", "description": "Attach one supplied file as base64 or from an explicitly allowed local directory.",
     "inputSchema": schema({"export_id": TEXT, **ASSET["properties"]}, ["export_id", "name"])},
    {"name": "finalize_export", "description": "Validate Markdown links, package files, verify the ZIP, and return its local path.",
     "inputSchema": schema({"export_id": TEXT, "post_markdown": TEXT,
                            "source_urls": {"type": "array", "items": TEXT},
                            "include_handoff": {"type": "boolean"}}, ["export_id"])},
    {"name": "get_export", "description": "Get the current draft status or finished ZIP path for an export ID.",
     "inputSchema": schema({"export_id": TEXT}, ["export_id"])},
]
CALLS = {"export_post": export_post, "begin_export": begin_export,
         "append_messages": append_messages, "add_asset": add_asset,
         "finalize_export": finalize_export,
         "get_export": lambda args: result_for(load_draft(args.get("export_id", "")))}


def handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        version = request.get("params", {}).get("protocolVersion", "2025-06-18")
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18",
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "export-post-mcp", "version": "0.1.0"}}}
    if method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        if name not in CALLS:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        try:
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                raise ExportError("Tool arguments must be an object")
            value = CALLS[name](args)
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
        except (ExportError, OSError, KeyError, ValueError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    elif method in {"resources/list", "prompts/list"}:
        result = {"resources": []} if method == "resources/list" else {"prompts": []}
    else:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve_stdio() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with CALL_LOCK:
                response = handle(request)
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": f"Invalid JSON-RPC request: {exc}"}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = "ExportPostMCP/0.1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(format % args, file=sys.stderr)

    def _auth_ok(self) -> bool:
        expected = os.environ.get("EXPORT_POST_HTTP_TOKEN", "")
        if not expected:
            return True
        return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {expected}")

    def _json(self, code: int, value: dict | None) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode() if value is not None else b""
        self.send_response(code)
        if value is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._json(404, {"error": "Not found"})
            return
        if not self._auth_ok():
            self._json(401, {"error": "Unauthorized"})
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > MAX_FILE_BYTES * 2 + 1_000_000:
            self._json(413, {"error": "Request body too large"})
            return
        try:
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            response = handle(request)
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            self._json(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": str(exc)}})
            return
        self._json(200 if response is not None else 202, response)

    def do_GET(self) -> None:
        if self.path == "/mcp":
            self._json(405, {"error": "This stateless endpoint accepts MCP POST requests"})
            return
        match = re.fullmatch(r"/downloads/([0-9a-f]{32})/([A-Za-z0-9_-]+)", self.path)
        if not match:
            self._json(404, {"error": "Not found"})
            return
        try:
            draft = load_draft(match.group(1))
            if draft["status"] != "complete" or not secrets.compare_digest(
                match.group(2), draft["download_token"]
            ):
                raise ExportError("Download unavailable")
            path = Path(draft["zip_path"])
            if path.parent != OUTPUT or not path.is_file():
                raise ExportError("Download unavailable")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)
        except ExportError:
            self._json(404, {"error": "Download unavailable"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Post MCP service")
    parser.add_argument("--http", action="store_true", help="Serve stateless MCP over HTTP at /mcp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        if args.host not in {"127.0.0.1", "::1", "localhost"} and not os.environ.get("EXPORT_POST_HTTP_TOKEN"):
            parser.error("Non-loopback HTTP requires EXPORT_POST_HTTP_TOKEN")
        server = ThreadingHTTPServer((args.host, args.port), McpHttpHandler)
        print(f"Export Post MCP listening on http://{args.host}:{args.port}/mcp", file=sys.stderr)
        server.serve_forever()
    else:
        serve_stdio()


if __name__ == "__main__":
    main()

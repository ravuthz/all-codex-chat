#!/usr/bin/env python3
"""Validate and package the local Export Post MCP plugin."""

from pathlib import Path
import json
import zipfile


ROOT = Path(__file__).resolve().parent
FILES = ["plugin.json", "mcp.json", "server.py", "README.md", "skills/export-post/SKILL.md"]
DESTINATION = ROOT / "export-post-mcp-plugin.zip"


def main() -> None:
    plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    assert plugin["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert mcp["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert plugin["name"] == "export-post-mcp"
    assert len(plugin["extensions"]["com.openai"]["interface"]["shortDescription"]) <= 30
    assert (ROOT / "skills/export-post/SKILL.md").read_text(encoding="utf-8").startswith("---\n")
    with zipfile.ZipFile(DESTINATION, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in FILES:
            archive.write(ROOT / relative, f"export-post-mcp/{relative}")
    with zipfile.ZipFile(DESTINATION) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {f"export-post-mcp/{name}" for name in FILES}
    print(DESTINATION)


if __name__ == "__main__":
    main()

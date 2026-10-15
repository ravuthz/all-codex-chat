import base64
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class McpExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        env = {**os.environ, "EXPORT_POST_OUTPUT_DIR": self.temp.name,
               "EXPORT_POST_ALLOWED_ROOTS": ""}
        self.process = subprocess.Popen([sys.executable, str(ROOT / "server.py")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, env=env)
        self.request("initialize", {"protocolVersion": "2025-06-18"})

    def tearDown(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()
        self.temp.cleanup()

    def request(self, method, params):
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        if result.get("isError"):
            return result
        return json.loads(result["content"][0]["text"])

    def test_one_call_export_contains_post_asset_and_handoff(self):
        tools = self.request("tools/list", {})["result"]["tools"]
        self.assertIn("export_post", {tool["name"] for tool in tools})
        data = self.call("export_post", {
            "title": "A Tiny Story", "source": "Test agent",
            "post_markdown": "# A Tiny Story\n\n![Sketch](assets/sketch.png)\n",
            "messages": [{"role": "user", "content": "Write a tiny story."},
                         {"role": "assistant", "content": "A tiny story."}],
            "assets": [{"name": "sketch.png", "kind": "asset",
                        "content_base64": base64.b64encode(b"fake image").decode()}],
        })
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["file_count"], 5)
        with zipfile.ZipFile(data["zip_path"]) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            prefix = "post-a-tiny-story/"
            self.assertIn(prefix + "post.md", names)
            self.assertIn(prefix + "assets/sketch.png", names)
            self.assertIn(prefix + "docs/session-handoff.md", names)
            post = archive.read(prefix + "post.md").decode()
            self.assertIn("![Sketch](assets/sketch.png)", post)
            manifest = json.loads(archive.read(prefix + "manifest.json"))
            self.assertEqual(manifest["message_count"], 2)

    def test_rejects_hidden_role_and_missing_asset(self):
        created = self.call("begin_export", {"title": "Boundary"})
        export_id = created["export_id"]
        rejection = self.call("append_messages", {"export_id": export_id,
                      "messages": [{"role": "system", "content": "hidden"}]})
        self.assertTrue(rejection["isError"])
        rejection = self.call("finalize_export", {"export_id": export_id,
                      "post_markdown": "# Boundary\n![Missing](assets/no.png)"})
        self.assertTrue(rejection["isError"])
        self.assertIn("Missing", rejection["content"][0]["text"])

    def test_rejects_credential_and_unapproved_local_path(self):
        created = self.call("begin_export", {"title": "Boundary"})
        export_id = created["export_id"]
        rejection = self.call("append_messages", {"export_id": export_id,
                      "messages": [{"role": "user", "content": "sk-" + "a" * 30}]})
        self.assertTrue(rejection["isError"])
        target = Path(self.temp.name) / "secret.txt"
        target.write_text("ordinary text")
        rejection = self.call("add_asset", {"export_id": export_id, "name": "text.txt",
                      "local_path": str(target)})
        self.assertTrue(rejection["isError"])


class HttpExportTests(unittest.TestCase):
    def test_http_mcp_and_signed_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = {**os.environ, "EXPORT_POST_OUTPUT_DIR": directory,
                   "EXPORT_POST_HTTP_TOKEN": "test-token"}
            process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--http",
                                        "--port", str(port)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=env)
            try:
                endpoint = f"http://127.0.0.1:{port}/mcp"
                request_body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "export_post", "arguments": {
                                    "title": "HTTP Export", "post_markdown": "# HTTP Export"}}}
                data = json.dumps(request_body).encode()
                for _ in range(30):
                    try:
                        with self.assertRaises(HTTPError) as error:
                            urlopen(Request(endpoint, data=data), timeout=1)
                        self.assertEqual(error.exception.code, 401)
                        error.exception.close()
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("HTTP server did not start")
                with urlopen(Request(endpoint, data=data,
                                     headers={"Authorization": "Bearer test-token"}), timeout=5) as response:
                    result = json.load(response)
                value = json.loads(result["result"]["content"][0]["text"])
                draft = json.loads((Path(directory) / "drafts" /
                                    f"{value['export_id']}.json").read_text())
                download = f"http://127.0.0.1:{port}/downloads/{value['export_id']}/{draft['download_token']}"
                with urlopen(download, timeout=5) as response:
                    self.assertEqual(response.headers["Content-Type"], "application/zip")
                    self.assertTrue(response.read().startswith(b"PK"))
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

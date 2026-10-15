const os = require("node:os");
const path = require("node:path");

module.exports = {
  apps: [
    {
      name: "export-post-mcp",
      cwd: __dirname,
      script: "server.py",
      interpreter: "python3",
      args: ["--http", "--host", "127.0.0.1", "--port", "8765"],
      env: {
        PYTHONUNBUFFERED: "1",
      },
      autorestart: true,
      restart_delay: 3000,
      watch: false,
      time: true,
    },
    {
      name: "export-post-tunnel",
      cwd: __dirname,
      script: "cloudflared",
      interpreter: "none",
      args: [
        "tunnel",
        "--config",
        path.join(os.homedir(), ".cloudflared", "config.yml"),
        "run",
        "export-post-mcp",
      ],
      autorestart: true,
      restart_delay: 5000,
      watch: false,
      time: true,
    },
  ],
};
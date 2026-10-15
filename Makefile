# Configuration
PM2        ?= pm2
CONFIG     ?= ecosystem.config.cjs
APP_MCP    ?= export-post-mcp
APP_TUNNEL ?= export-post-tunnel
APP_REGEX  ?= /export-post/
LINES      ?= 50
PYTHON     ?= python3

.PHONY: help \
        start stop restart reload delete status info monit \
        start-mcp stop-mcp restart-mcp \
        start-tunnel stop-tunnel restart-tunnel \
        logs logs-tail logs-mcp logs-tunnel flush \
        save resurrect startup \
        test package

# Default target
help:
	@echo "Available commands:"
	@echo "  Ecosystem targets (controls apps in $(CONFIG)):"
	@echo "    make start          Start all ecosystem apps"
	@echo "    make stop           Stop all ecosystem apps"
	@echo "    make restart        Restart all ecosystem apps"
	@echo "    make reload         Reload all ecosystem apps"
	@echo "    make delete         Delete all ecosystem apps from PM2"
	@echo "    make status         Display PM2 process list and metrics"
	@echo "    make info           Show details for $(APP_MCP) and $(APP_TUNNEL)"
	@echo "    make monit          Launch interactive PM2 terminal monitor"
	@echo ""
	@echo "  Individual app controls:"
	@echo "    make start-mcp      Start $(APP_MCP) only"
	@echo "    make stop-mcp       Stop $(APP_MCP) only"
	@echo "    make restart-mcp    Restart $(APP_MCP) only"
	@echo "    make start-tunnel   Start $(APP_TUNNEL) only"
	@echo "    make stop-tunnel    Stop $(APP_TUNNEL) only"
	@echo "    make restart-tunnel Restart $(APP_TUNNEL) only"
	@echo ""
	@echo "  Logging (override line count with LINES=100):"
	@echo "    make logs           Stream logs for both apps (LINES=$(LINES))"
	@echo "    make logs-tail      Print recent logs without streaming"
	@echo "    make logs-mcp       Stream logs for $(APP_MCP)"
	@echo "    make logs-tunnel    Stream logs for $(APP_TUNNEL)"
	@echo "    make flush          Clear log files for both apps"
	@echo ""
	@echo "  PM2 persistence:"
	@echo "    make save           Save current PM2 process list across reboots"
	@echo "    make resurrect      Restore previously saved PM2 process list"
	@echo "    make startup        Generate PM2 startup hook script"
	@echo ""
	@echo "  Project utilities:"
	@echo "    make test           Run unit tests"
	@echo "    make package        Build export-post-mcp-plugin.zip"

# Ecosystem commands
start:
	$(PM2) start $(CONFIG)

stop:
	$(PM2) stop $(CONFIG)

restart:
	$(PM2) restart $(CONFIG)

reload:
	$(PM2) reload $(CONFIG)

delete:
	$(PM2) delete $(CONFIG)

status:
	$(PM2) status

info:
	@echo "=== $(APP_MCP) ==="
	-$(PM2) describe $(APP_MCP)
	@echo ""
	@echo "=== $(APP_TUNNEL) ==="
	-$(PM2) describe $(APP_TUNNEL)

monit:
	$(PM2) monit

# Individual app commands
start-mcp:
	$(PM2) start $(CONFIG) --only $(APP_MCP)

stop-mcp:
	$(PM2) stop $(APP_MCP)

restart-mcp:
	$(PM2) restart $(APP_MCP)

start-tunnel:
	$(PM2) start $(CONFIG) --only $(APP_TUNNEL)

stop-tunnel:
	$(PM2) stop $(APP_TUNNEL)

restart-tunnel:
	$(PM2) restart $(APP_TUNNEL)

# Logging
logs:
	$(PM2) logs $(APP_REGEX) --lines $(LINES)

logs-tail:
	$(PM2) logs $(APP_REGEX) --lines $(LINES) --nostream

logs-mcp:
	$(PM2) logs $(APP_MCP) --lines $(LINES)

logs-tunnel:
	$(PM2) logs $(APP_TUNNEL) --lines $(LINES)

flush:
	$(PM2) flush $(APP_MCP)
	$(PM2) flush $(APP_TUNNEL)

# Persistence
save:
	$(PM2) save

resurrect:
	$(PM2) resurrect

startup:
	$(PM2) startup

# Project targets
test:
	$(PYTHON) -m unittest discover -s tests -v

package:
	$(PYTHON) package_plugin.py

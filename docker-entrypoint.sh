#!/bin/bash
#
# Jamf-SnipeIT Suite - Docker Entrypoint
#
# This script handles:
# - Environment variable configuration
# - Startup module execution
# - Interactive "NOW" command support
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Jamf-SnipeIT Suite — Initializing${NC}"

# Change to app directory
cd /app

# Check if config exists
if [ ! -f "/app/config/config.yaml" ]; then
    echo -e "${RED}❌ Configuration file not found at /app/config/config.yaml${NC}"
    echo -e "${YELLOW}   Please mount your config file:${NC}"
    echo "   docker run -v /path/to/config.yaml:/app/config/config.yaml ..."
    exit 1
fi

echo -e "${GREEN}Config: found${NC}"

# Create logs directory if needed
mkdir -p /app/logs

# Verify Python dependencies are installed (built into image)
python -c "import yaml, requests, msal, apscheduler" 2>/dev/null || {
    echo -e "${RED}❌ Missing Python dependencies — rebuild the Docker image${NC}"
    exit 1
}
echo -e "${GREEN}Dependencies: OK${NC}"

# Determine run mode
RUN_MODE=${RUN_MODE:-scheduler}
DRY_RUN=${DRY_RUN:-false}

# Build dry-run flag
DRY_RUN_FLAG=""
if [ "$DRY_RUN" = "true" ] || [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}DRY RUN MODE — no changes will be made${NC}"
    DRY_RUN_FLAG="--dry-run"
fi

case "$RUN_MODE" in
    "scheduler")
        echo -e "${BLUE}Mode: SCHEDULER${NC}"
        
        # Run the Docker scheduler script (includes startup run + scheduler)
        exec python -u /app/src/docker_scheduler.py \
            --config /app/config/config.yaml \
            --log-file /app/logs/scheduler.log \
            $DRY_RUN_FLAG
        ;;
    
    "cli")
        echo -e "${BLUE}Mode: CLI${NC}"
        
        # Run specific command passed as arguments
        if [ $# -gt 0 ]; then
            exec python -u /app/src/main.py "$@"
        else
            echo "Usage: docker run <image> cli <command>"
            echo "Commands: leavers, snipe-to-jamf, user-match, model-sync, reconcile"
            exit 1
        fi
        ;;
    
    "run-once")
        echo -e "${BLUE}Mode: RUN-ONCE${NC}"
        
        # Run all modules once and exit
        exec python -u /app/src/docker_scheduler.py \
            --config /app/config/config.yaml \
            --scheduler-disabled \
            $DRY_RUN_FLAG
        ;;
    
    "shell")
        echo -e "${BLUE}Mode: SHELL${NC}"
        exec /bin/bash
        ;;
    
    *)
        echo -e "${RED}Unknown run mode: $RUN_MODE${NC}"
        echo "Valid modes: scheduler, cli, run-once, shell"
        exit 1
        ;;
esac

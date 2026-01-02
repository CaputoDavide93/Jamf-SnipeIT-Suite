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

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║              Jamf-SnipeIT Suite - Initializing                ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Change to app directory
cd /app

# Check if config exists
if [ ! -f "/app/config/config.yaml" ]; then
    echo -e "${RED}❌ Configuration file not found at /app/config/config.yaml${NC}"
    echo -e "${YELLOW}   Please mount your config file:${NC}"
    echo "   docker run -v /path/to/config.yaml:/app/config/config.yaml ..."
    exit 1
fi

echo -e "${GREEN}✅ Configuration file found${NC}"

# Create logs directory if needed
mkdir -p /app/logs

# Export Python path
export PYTHONPATH=/app/src:$PYTHONPATH
export PYTHONUNBUFFERED=1

# Check Python dependencies
echo -e "${BLUE}📦 Checking dependencies...${NC}"
python -c "import yaml, requests, msal, apscheduler" 2>/dev/null || {
    echo -e "${YELLOW}⚠️  Installing missing dependencies...${NC}"
    pip install -q -r /app/requirements.txt
}
echo -e "${GREEN}✅ Dependencies OK${NC}"

# Determine run mode
RUN_MODE=${RUN_MODE:-scheduler}
DRY_RUN=${DRY_RUN:-false}

# Build dry-run flag
DRY_RUN_FLAG=""
if [ "$DRY_RUN" = "true" ] || [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}🧪 DRY RUN MODE ENABLED - No changes will be made${NC}"
    DRY_RUN_FLAG="--dry-run"
fi

case "$RUN_MODE" in
    "scheduler")
        echo -e "${BLUE}🚀 Starting in SCHEDULER mode${NC}"
        echo ""
        
        # Run the Docker scheduler script (includes startup run + scheduler)
        exec python -u /app/src/docker_scheduler.py \
            --config /app/config/config.yaml \
            --log-file /app/logs/scheduler.log \
            $DRY_RUN_FLAG
        ;;
    
    "cli")
        echo -e "${BLUE}🔧 Starting in CLI mode${NC}"
        echo ""
        
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
        echo -e "${BLUE}🔄 Running all modules once (no scheduler)${NC}"
        echo ""
        
        # Run all modules once and exit
        exec python -u /app/src/docker_scheduler.py \
            --config /app/config/config.yaml \
            --scheduler-disabled \
            $DRY_RUN_FLAG
        ;;
    
    "shell")
        echo -e "${BLUE}🐚 Starting shell${NC}"
        exec /bin/bash
        ;;
    
    *)
        echo -e "${RED}Unknown run mode: $RUN_MODE${NC}"
        echo "Valid modes: scheduler, cli, run-once, shell"
        exit 1
        ;;
esac

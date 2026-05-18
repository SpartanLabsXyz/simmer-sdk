#!/usr/bin/env bash
# add-mcp — Register Simmer MCP server with Claude Code or Claude Desktop
#
# Usage:
#   ./scripts/add-mcp.sh              # register from the simmer-sdk repo
#   ./scripts/add-mcp.sh --global     # register globally (Claude Desktop)
#   ./scripts/add-mcp.sh --npx        # use npx @simmer/mcp (npm install)

set -euo pipefail

MCP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MCP_DIR/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"

SCOPE=""
USE_NPX=false

for arg in "$@"; do
  case $arg in
    --global) SCOPE="--global" ;;
    --npx)    USE_NPX=true ;;
    --help|-h)
      echo "Usage: $0 [--global] [--npx]"
      echo "  --global  Register globally (Claude Desktop + all projects)"
      echo "  --npx     Use npx @simmer/mcp instead of local build"
      exit 0
      ;;
  esac
done

echo "🔮 Simmer MCP — registration"
echo ""

# Verify claude is available
if ! command -v claude &>/dev/null; then
  echo "❌ claude CLI not found. Install Claude Code first:"
  echo "   https://claude.ai/code"
  exit 1
fi

# Verify SIMMER_API_KEY is set (most skills need it)
if [ -z "${SIMMER_API_KEY:-}" ]; then
  echo "⚠️  SIMMER_API_KEY is not set. Most skills require it."
  echo "   Get your key: https://simmer.markets/dashboard"
  echo "   Then: export SIMMER_API_KEY=sk_live_..."
  echo ""
  echo "   Continuing registration — set the key before running skills."
  echo ""
fi

if [ "$USE_NPX" = true ]; then
  echo "📦 Registering via npx @simmer/mcp ..."
  claude mcp add $SCOPE simmer -- npx -y @simmer/mcp
else
  # Build if dist/ doesn't exist or src is newer
  if [ ! -f "$MCP_DIR/dist/index.js" ] || \
     [ "$MCP_DIR/src/index.ts" -nt "$MCP_DIR/dist/index.js" ]; then
    echo "🔧 Building MCP server..."
    if ! command -v npm &>/dev/null; then
      echo "❌ npm not found. Install Node.js 18+ first."
      exit 1
    fi
    cd "$MCP_DIR"
    npm install --silent
    npm run build --silent
    echo "✅ Built successfully"
    echo ""
  fi

  echo "📦 Registering local build with Claude..."
  claude mcp add $SCOPE simmer \
    -e SIMMER_API_KEY="${SIMMER_API_KEY:-}" \
    -e SIMMER_SKILLS_DIR="$SKILLS_DIR" \
    -- node "$MCP_DIR/dist/index.js"
fi

echo ""
echo "✅ Simmer MCP registered as 'simmer'"
echo ""
echo "   Available tools:"
echo "   • simmer_list_skills       — browse all skills"
echo "   • simmer_get_skill_docs    — read a skill's full documentation"
echo "   • simmer_<slug>            — run or query a specific skill"
echo ""
echo "   In Claude, type: 'list all simmer skills' to get started."
echo ""
if [ -z "${SIMMER_API_KEY:-}" ]; then
  echo "⚠️  Remember to set SIMMER_API_KEY for trading skills:"
  echo "   export SIMMER_API_KEY=sk_live_..."
  echo "   Then re-run: $0"
fi

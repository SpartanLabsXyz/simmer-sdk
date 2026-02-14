#!/bin/bash
# Publish a skill to ClawHub from its folder.
# Reads name, version, and published flag from SKILL.md frontmatter.
#
# Usage: ./scripts/publish.sh <skill-folder>
# Example: ./scripts/publish.sh skills/fastloop
#
# Safety:
#   - Only publishes if SKILL.md has `published: true`
#   - Slug comes from `name:` field (no folder name guessing)
#   - Version comes from `version:` field (matches --version flag)
#   - Copies full directory (no missing files)
#   - Checks if version already exists before publishing

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <skill-folder>"
  echo "Example: $0 skills/fastloop"
  exit 1
fi

SKILL_DIR="$1"
SKILL_MD="$SKILL_DIR/SKILL.md"

# For the global skill.md (lowercase), support both
if [ ! -f "$SKILL_MD" ] && [ -f "$SKILL_DIR/skill.md" ]; then
  SKILL_MD="$SKILL_DIR/skill.md"
fi

if [ ! -f "$SKILL_MD" ]; then
  echo "❌ No SKILL.md found in $SKILL_DIR"
  exit 1
fi

# Parse frontmatter (between --- lines)
FRONTMATTER=$(sed -n '/^---$/,/^---$/p' "$SKILL_MD" | sed '1d;$d')

get_field() {
  echo "$FRONTMATTER" | grep "^$1:" | head -1 | sed "s/^$1:[[:space:]]*//" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//' || echo ""
}

NAME=$(get_field "name")
VERSION=$(get_field "version")
PUBLISHED=$(get_field "published")

if [ -z "$NAME" ]; then
  echo "❌ No 'name:' field in $SKILL_MD frontmatter"
  exit 1
fi

if [ -z "$VERSION" ]; then
  echo "❌ No 'version:' field in $SKILL_MD frontmatter"
  exit 1
fi

if [ "$PUBLISHED" != "true" ]; then
  echo "❌ Skill '$NAME' is not marked for publishing (published: $PUBLISHED)"
  echo "   Add 'published: true' to $SKILL_MD frontmatter to enable"
  exit 1
fi

echo "📦 Publishing $NAME@$VERSION"
echo "   Source: $SKILL_DIR"

# Check if version already exists
echo "   Checking ClawHub for existing version..."
if clawhub inspect "$NAME" 2>&1 | grep -q "Latest: $VERSION"; then
  echo "❌ Version $VERSION already exists on ClawHub"
  echo "   Bump the version in $SKILL_MD and try again"
  exit 1
fi

# Copy full directory to temp (using slug as folder name)
TMP_DIR="/tmp/$NAME"
rm -rf "$TMP_DIR"
cp -r "$SKILL_DIR" "$TMP_DIR"

# Clean up unwanted files
rm -rf "$TMP_DIR/__pycache__" "$TMP_DIR"/.* 2>/dev/null || true

FILE_COUNT=$(find "$TMP_DIR" -type f | wc -l)
echo "   Files: $FILE_COUNT"
find "$TMP_DIR" -type f -printf "   - %P (%s bytes)\n"

# Publish
echo "   Publishing..."
OUTPUT=$(clawhub publish "$TMP_DIR" --version "$VERSION" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "✅ Published $NAME@$VERSION"
  echo "   $OUTPUT"
else
  echo "❌ Publish failed:"
  echo "   $OUTPUT"
  exit 1
fi

# Cleanup
rm -rf "$TMP_DIR"

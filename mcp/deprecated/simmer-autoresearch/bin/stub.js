#!/usr/bin/env node
// @ts-check
console.error(
  "\n⚠️  simmer-autoresearch has been renamed to simmer-mcp.\n\n" +
  "Update your installation:\n" +
  "  npm install -g simmer-mcp\n\n" +
  "Update your agent config — replace:\n" +
  '  "command": "simmer-autoresearch"\n' +
  "with:\n" +
  '  "command": "simmer-mcp"\n\n' +
  "All tools, env vars, and behavior are unchanged.\n"
);
process.exit(1);

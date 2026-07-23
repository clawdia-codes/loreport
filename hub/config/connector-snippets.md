# Connector snippets

Per-provider connection snippets for `hub/mcp_server.py` (bridge A, design.md §D15).
Every surface below maps to exactly one `provider/*` branch — never to `main` directly.

---

## ChatGPT — OpenAI Secure MCP Tunnel

1. Start the hub server (localhost only): `python3 hub/mcp_server.py --transport http --port 8765`.
2. Run the tunnel client with `hub/config/tunnel-client.json` (fill in `auth_token` and
   `credential` first — a standard OpenAI Secure MCP Tunnel client setup).
3. In ChatGPT's connector settings, add the tunnel's public endpoint as an MCP connector.
   ChatGPT's requests arrive over the tunnel carrying the `chatgpt` credential, so
   `brain_capture` always lands on `provider/chatgpt`.

Fallback if the tunnel is down: ChatGPT "Tasks" pushes captured blocks to the
capture-inbox on a schedule (bridge B); `inbox_ingest.py chatgpt <file>` files them.

---

## Claude.ai (Projects) — native MCP connector

1. Start the hub server: `python3 hub/mcp_server.py --transport http --port 8765`
   (reachable only from this machine — Claude.ai needs a tunnel or local network
   path of your choosing; the server itself never listens beyond 127.0.0.1).
2. In Claude.ai's connector settings, add a custom MCP connector pointing at your
   reachable endpoint, with the `claude` credential in its auth header.
3. `brain_capture` calls now land on `provider/claude`.

Fallback: paste an emit-grammar block into the capture-inbox by hand; run
`python3 hub/inbox_ingest.py claude <pasted-block-file>`.

---

## Claude Code — MCP config

Add to your Claude Code MCP settings (e.g. `.mcp.json` or the CLI's MCP config):

```json
{
  "mcpServers": {
    "loreport": {
      "command": "python3",
      "args": ["hub/mcp_server.py", "--transport", "stdio", "--credential", "claude-local-dev-token"]
    }
  }
}
```

Claude Code also has direct filesystem + git access, so it may simply commit to
`provider/claude` itself instead of going through the MCP tool — both paths land
on the same branch and are reconciled by the next daily merge.

---

## openclaw — native

openclaw *is* the hub; it needs no bridge. It reads and writes `main` and
`provider/openclaw` directly on the filesystem, runs `brain_merge.py` and
`snapshot_publish.py` from cron (`hub/config/cron.txt`), and can also run
`hub/mcp_server.py --transport stdio --credential openclaw-local-dev-token` if it
prefers to go through the same tool surface as the other providers for auditability.

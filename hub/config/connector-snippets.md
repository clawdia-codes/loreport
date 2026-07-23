# Connector snippets

Per-provider connection snippets for `hub/mcp_server.py` (bridge A, design.md §D15).
Every surface below maps to exactly one `provider/*` branch — never to `main` directly.

---

## ChatGPT — OpenAI Secure MCP Tunnel

1. **Create a tunnel** at `platform.openai.com/settings/organization/tunnels` ("Create
   tunnel"). This gives you a `tunnel_id`.
2. **Get a control-plane API key** — the tunnel control-plane API only accepts an
   account-level **Runtime-class key**, not an ordinary per-project `sk-proj-...` key
   created fresh on the API keys page (that gets `401 invalid_api_key`). Use the Runtime
   key from `platform.openai.com/settings/organization/api-keys` that your account already
   uses for tunnel-client, if one exists.
3. **Configure the tunnel client** (`hub/config/tunnel-client.json` or an equivalent
   `tunnel-client` YAML profile) with that `tunnel_id` + Runtime key, and point its
   `mcp.commands` at the **stdio** transport directly:
   `python3 hub/mcp_server.py --transport stdio --credential <per-provider-token>`
   — `tunnel-client` spawns this as a subprocess itself; you do **not** need to separately
   run `mcp_server.py --transport http` and have the tunnel connect to it.
4. If you run the tunnel client as a systemd (or similar) service, **don't** source a
   shared secrets file that also defines `CONTROL_PLANE_TUNNEL_ID` — that env var
   unconditionally overrides the profile's `tunnel_id`, which will silently point the
   service at the wrong tunnel. Keep `tunnel_id`/`api_key` as literal values in this
   tunnel's own profile and give the service no other env file.
5. In ChatGPT's connector settings, add the tunnel as an MCP connector. ChatGPT's requests
   arrive over the tunnel carrying the `chatgpt` credential, so `brain_capture` always
   lands on `provider/chatgpt`.
   **Known gap:** a "Connectors" entry may not appear in ChatGPT's Settings UI at all for
   every account — this is an OpenAI-side eligibility/rollout gate, not a hub-side problem.
   If it's missing, the tunnel is still correctly provisioned and running; only the final
   in-app linking step is blocked, and only OpenAI can unblock it for that account.

Fallback if the tunnel is down or connector-linking is unavailable: ChatGPT "Tasks" pushes
captured blocks to the capture-inbox on a schedule (bridge B); `inbox_ingest.py chatgpt
<file>` files them.

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

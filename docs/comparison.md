# How Loreport compares

Honest notes on prior art and alternatives, so you don't have to go find them — including
the places where something else is the better choice.

**Nothing found does both tiers.** Every zero-install "portable markdown brain" project
(Karpathy's LLM Wiki and its cottage industry of clones) assumes an agent runtime with
filesystem access — none work pasted into a bare browser chat with nothing installed.
Every multi-provider shared-memory project found stores memory in a database (SQLite, a
hosted store), not human-readable git-tracked markdown, and none use a branch-per-provider
+ scheduled-merge model with a fail-closed secret scrub. That combination is a real gap,
not a marketing claim — but the corollary is that Loreport is also **less proven**:
single-instance-verified, no retrieval-quality benchmark, fewer providers covered out of
the box.

**Closest sibling on the sync side:**
[ai-memory-mcp](https://github.com/alphaonedev/ai-memory-mcp) (Apache 2.0) already bridges
more providers (Claude, ChatGPT, Grok, Gemini, Codex, Cursor, openclaw) via MCP, ships a
retroactive import tool, and has published retrieval benchmarks Loreport doesn't. It
trades git-native, human-readable markdown for a local SQLite store. **Prefer it** if a
wider provider set matters more to you than owning your data as plain files.

**Smoothest casual UX:** [mem0's OpenMemory](https://mem0.ai) browser extension
auto-injects memory into ChatGPT/Perplexity/Grok/Gemini with minimal setup. It's a
real-time bridge rather than a periodic reconciliation, and it doesn't document
secret/PII filtering the way Loreport's three scrub layers do. **Prefer it** if you want
zero maintenance and the data sensitivity is low.

**Prior art adopted:** the index-first, one-file-per-item, wikilinked shape comes from
Andrej Karpathy's ["LLM Wiki" gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
which independently converged on the same pattern — including the explicit decision to
skip embeddings, RAG, and graphs at moderate scale. See ADR-001 and ADR-002 in
[`architecture-decisions.md`](architecture-decisions.md).

# Agent Coordination

This directory is the shared communication bridge between OpenClaw and Codex.

## Files

- `shared_status.md`: single shared file that both agents must read before work and update after work

## Protocol

1. Read `shared_status.md` before starting any monitoring, debugging, or repair work.
2. Append a new timestamped entry instead of rewriting history.
3. When OpenClaw finds an issue it cannot safely resolve, it must record:
   - symptoms
   - suspected root cause
   - commands or files already checked
   - exact blocker
   - requested follow-up from Codex
4. When Codex resolves a blocker, it must record:
   - diagnosis
   - files changed
   - verification result
   - whether OpenClaw should resume monitoring or take a new action
5. If no issue exists, OpenClaw should still leave short heartbeat updates so the file reflects recent monitoring activity.
6. Do not delete prior entries.

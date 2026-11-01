# Prompt log

- `claude_session.md`: the one Claude Code session that built this repo (Claude desktop app, Code tab, Claude Opus
  5.5), from the first prompt to the final submission clean-up. It was converted from the tool's session file
  (`.jsonl`) with no edits other than these:
  - long tool inputs are cut at 4,000 characters and tool outputs at 1,500 (they are mostly document text and
    command output that is already in the repo);
  - harness system reminders are dropped;
  - anything shaped like an API key and the account e-mail address are redacted (there were no keys).
- The model's private reasoning is not saved in the session file, so it is not in the log.
- The context was compacted once when the conversation grew long; the summary the tool wrote then is included where
  it happened.
- The last prompt in the log is a checklist written by a separate AI chat assistant after reviewing a zip of the
  repo; it was pasted in verbatim. That assistant's own conversation is not part of this export.

There were no other sessions.

- `prompt_playbook.md`: prompts I would use to review or extend the harness, written after the build. It is not part
  of the session log and none of its prompts were sent during the build.

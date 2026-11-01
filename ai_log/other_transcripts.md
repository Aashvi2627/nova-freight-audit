# Other transcripts

No AI tool other than the Claude Code session in [`claude_session.md`](claude_session.md) was used for this project.

## Runtime model use (not a transcript)

The harness itself can call Claude in two guarded fallbacks (`harness/nova/llm.py`):
1. transcribing a scanned page when local OCR (`rapidocr-onnxruntime`) can't load;
2. reading one contract parameter from a clause the parsers found but couldn't read. The answer is rejected unless
   the words it quotes are verbatim in the clause and state that number.

No API key was available while the harness was built, so neither path made a live call during development. The
clause fallback was tested with a stubbed client only.

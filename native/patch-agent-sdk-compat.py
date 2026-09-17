from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if "install_legacy_responses" in text:
    raise SystemExit(0)
start = text.find("        # The constrained iOS runtime may carry an older OpenAI SDK without")
end = text.find("        if not agent.quiet_mode:", start)
if start < 0 or end < 0:
    raise SystemExit("OpenAI compatibility block not found")
replacement = '''        # Keep the provider's Responses route on old SDKs by using the
        # bundled raw-SSE transport instead of downgrading GPT-5.x to chat.
        if agent.api_mode == "codex_responses" and not hasattr(agent.client, "responses"):
            from agent.legacy_responses import install as install_legacy_responses
            install_legacy_responses(agent.client)
'''
path.write_text(text[:start] + replacement + text[end:], encoding="utf-8", newline="\n")

from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if "install_legacy_responses" in text:
    raise SystemExit(0)
start = text.find("        # The constrained iOS runtime may carry an older OpenAI SDK without")
end = text.find("        if not agent.quiet_mode:", start)
replacement = '''        # Keep the provider's Responses route on old SDKs by using the
        # bundled raw-SSE transport instead of downgrading GPT-5.x to chat.
        if agent.api_mode == "codex_responses" and not hasattr(agent.client, "responses"):
            from agent.legacy_responses import install as install_legacy_responses
            install_legacy_responses(agent.client)
'''
if start >= 0 and end >= 0:
    text = text[:start] + replacement + text[end:]
else:
    anchor = "        agent.client = agent._create_openai_client(client_kwargs, reason=\"agent_init\", shared=True)\n"
    if anchor not in text:
        raise SystemExit("OpenAI client initialization anchor not found")
    text = text.replace(anchor, anchor + replacement, 1)
path.write_text(text, encoding="utf-8", newline="\n")

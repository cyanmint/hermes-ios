import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
PACKAGED_RUNTIME = os.environ.get("HERMES_IOS_TEST_RUNTIME_ZIP")
AGENT_SOURCE = os.environ.get("HERMES_AGENT_SOURCE")
if PACKAGED_RUNTIME:
    sys.path.insert(0, f"{PACKAGED_RUNTIME}/hermes")
    from agent import legacy_responses
else:
    if AGENT_SOURCE:
        sys.path.insert(0, AGENT_SOURCE)
    ADAPTER_PATH = ROOT / "overlay" / "hermes" / "agent" / "legacy_responses.py"
    SPEC = importlib.util.spec_from_file_location("legacy_responses_under_test", ADAPTER_PATH)
    legacy_responses = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = legacy_responses
    SPEC.loader.exec_module(legacy_responses)


def _assemble_codex_response(events):
    if not (PACKAGED_RUNTIME or AGENT_SOURCE):
        return None
    from agent.codex_runtime import _consume_codex_event_stream

    return _consume_codex_event_stream(iter(events), model="test-model")


def _field(item, name):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


class FakeClient:
    pass


class LegacyResponsesStreamingTests(unittest.TestCase):
    def test_streaming_create_preserves_sse_text_when_terminal_output_is_null(self):
        frames = [
            {"type": "response.output_text.delta", "delta": "HERMES_IOS_CHAT_OK"},
            {"type": "response.completed", "response": {
                "id": "resp_test", "status": "completed", "output": None,
                "usage": {"output_tokens": 3},
            }},
        ]
        wire = "".join(
            f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames
        )
        captured = {}

        def handle(request):
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=wire
            )

        transport = httpx.Client(transport=httpx.MockTransport(handle))
        client = FakeClient()
        client.base_url = "https://api.example.test/v1"
        client.default_headers = {}
        client._custom_headers = {}
        client.api_key = "test-only-not-a-secret"
        client._client = transport
        legacy_responses.install(client)
        try:
            events = list(client.responses.create(
                model="test-model", input=[], tools=[], stream=True
            ))
        finally:
            transport.close()

        self.assertEqual(captured["url"], "https://api.example.test/v1/responses")
        self.assertTrue(captured["payload"]["stream"])
        self.assertIn("text/event-stream", captured["headers"].get("accept", ""))
        self.assertEqual(events, frames)
        assembled = _assemble_codex_response(events)
        if assembled is not None:
            self.assertEqual(assembled.output_text, "HERMES_IOS_CHAT_OK")

    def test_streaming_create_preserves_tool_call_arguments_events(self):
        frames = [
            {"type": "response.output_item.added", "output_index": 0, "item": {
                "id": "fc_1", "type": "function_call", "call_id": "call_1",
                "name": "terminal", "arguments": "",
            }},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1",
             "delta": '{"command":"printf HERMES_IOS_TOOL_OK"}'},
            {"type": "response.function_call_arguments.done", "item_id": "fc_1",
             "arguments": '{"command":"printf HERMES_IOS_TOOL_OK"}'},
            {"type": "response.output_item.done", "output_index": 0, "item": {
                "id": "fc_1", "type": "function_call", "call_id": "call_1",
                "name": "terminal", "arguments": '{"command":"printf HERMES_IOS_TOOL_OK"}',
            }},
            {"type": "response.completed", "response": {
                "id": "resp_tool", "status": "completed", "output": None,
            }},
        ]
        wire = "".join(
            f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames
        )

        def handle(request):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=wire
            )

        transport = httpx.Client(transport=httpx.MockTransport(handle))
        client = FakeClient()
        client.base_url = "https://api.example.test/v1"
        client.default_headers = {}
        client._custom_headers = {}
        client.api_key = "test-only-not-a-secret"
        client._client = transport
        legacy_responses.install(client)
        try:
            events = list(client.responses.create(
                model="test-model", input=[], tools=[], stream=True
            ))
        finally:
            transport.close()

        self.assertEqual(events, frames)
        call_deltas = [event for event in events if event["type"] == "response.function_call_arguments.delta"]
        self.assertEqual(call_deltas[0]["delta"], '{"command":"printf HERMES_IOS_TOOL_OK"}')
        assembled = _assemble_codex_response(events)
        if assembled is not None:
            tool_calls = [item for item in assembled.output if _field(item, "type") == "function_call"]
            self.assertEqual(len(tool_calls), 1)
            self.assertEqual(_field(tool_calls[0], "name"), "terminal")
            self.assertEqual(_field(tool_calls[0], "call_id"), "call_1")
            self.assertEqual(_field(tool_calls[0], "arguments"), '{"command":"printf HERMES_IOS_TOOL_OK"}')


if __name__ == "__main__":
    unittest.main()

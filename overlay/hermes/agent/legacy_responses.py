import json
import threading
from types import SimpleNamespace


_HTTP_LOCK = threading.RLock()


class _ResponseStream:
    def __init__(self, events):
        self._events = events

    def __iter__(self):
        yield from self._events

    def close(self):
        return None


class _ResponsesCompat:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        import httpx
        payload = dict(kwargs)
        extra = payload.pop("extra_body", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                payload.setdefault(key, value)
        payload.pop("stream_options", None)
        timeout = payload.pop("timeout", None)
        payload.pop("stream", None)
        base = str(getattr(self._client, "base_url", "")).rstrip("/")
        url = base + "/responses"
        headers = dict(getattr(self._client, "default_headers", {}) or {})
        headers.update(getattr(self._client, "_custom_headers", {}) or {})
        headers = {key: value for key, value in headers.items() if isinstance(value, (str, bytes))}
        api_key = getattr(self._client, "api_key", None)
        if api_key and "Authorization" not in headers:
            headers["Authorization"] = "Bearer " + api_key
        headers.setdefault("Content-Type", "application/json")
        transport = getattr(self._client, "_client", None)
        if transport is None:
            transport = httpx.Client()
        with _HTTP_LOCK:
            response = transport.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code >= 400:
            body = response.text
            raise RuntimeError("HTTP %s: %s" % (response.status_code, body))
        result = response.json()
        events = []
        for item in result.get("output") or []:
            for part in item.get("content") or []:
                text = part.get("text") if isinstance(part, dict) else None
                if text:
                    events.append({"type": "response.output_text.delta", "delta": text})
        events.append({"type": "response.completed", "response": result})
        return _ResponseStream(events)


def install(client):
    cls = type(client)
    if not hasattr(cls, "responses"):
        setattr(cls, "responses", property(lambda instance: _ResponsesCompat(instance)))
    return client

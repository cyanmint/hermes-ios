import json
import logging
import threading


_LOGGER = logging.getLogger(__name__)
_HTTP_LOCK = threading.RLock()


class _ResponseStream:
    def __init__(self, events):
        self._events = events

    def __iter__(self):
        yield from self._events

    def close(self):
        close = getattr(self._events, "close", None)
        if callable(close):
            close()


def _log_sse_event_summary(event):
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    response = event.get("response") if isinstance(event.get("response"), dict) else {}
    output = response.get("output")
    output_items = output if isinstance(output, list) else []
    argument = item.get("arguments")
    delta = event.get("delta")
    _LOGGER.debug(
        "iOS Responses SSE event type=%s item_type=%s item_id=%s delta_chars=%s arguments_chars=%s "
        "terminal_output_type=%s terminal_output_items=%s terminal_item_types=%s terminal_argument_chars=%s",
        event.get("type"), item.get("type"), bool(event.get("item_id")),
        len(delta) if isinstance(delta, str) else None,
        len(argument) if isinstance(argument, str) else None,
        type(output).__name__ if output is not None else "None",
        len(output_items),
        [entry.get("type") for entry in output_items if isinstance(entry, dict)],
        [len(entry["arguments"]) for entry in output_items
         if isinstance(entry, dict) and isinstance(entry.get("arguments"), str)],
    )


def _iter_sse_events(response):
    event_name = None
    data_lines = []

    def take_event():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        raw_data = "\n".join(data_lines)
        named_event = event_name
        event_name, data_lines = None, []
        if raw_data == "[DONE]":
            return None
        event = json.loads(raw_data)
        if not isinstance(event, dict):
            raise ValueError("Responses SSE data must be a JSON object")
        if named_event and not event.get("type"):
            event["type"] = named_event
        _log_sse_event_summary(event)
        return event

    for line in response.iter_lines():
        if not line:
            event = take_event()
            if event is not None:
                yield event
        elif line.startswith(":"):
            continue
        else:
            field, separator, value = line.partition(":")
            if not separator:
                continue
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)
    event = take_event()
    if event is not None:
        yield event


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
        stream = bool(payload.pop("stream", False))
        timeout = payload.pop("timeout", None)
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
        owns_transport = transport is None
        if owns_transport:
            transport = httpx.Client()

        request_kwargs = {"headers": headers, "json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        if stream:
            payload["stream"] = True
            headers["Accept"] = "text/event-stream"
            tools = payload.get("tools")
            tool_summary = [
                {
                    "name": tool.get("name"),
                    "properties": sorted((tool.get("parameters") or {}).get("properties", {})),
                    "required": sorted((tool.get("parameters") or {}).get("required", [])),
                }
                for tool in tools if isinstance(tool, dict)
            ] if isinstance(tools, list) else []
            _LOGGER.debug(
                "iOS Responses SSE request stream=%s tool_choice=%s tools=%s",
                payload.get("stream"), payload.get("tool_choice"), tool_summary,
            )

            def events():
                try:
                    with _HTTP_LOCK:
                        with transport.stream("POST", url, **request_kwargs) as response:
                            if response.status_code >= 400:
                                body = response.read().decode("utf-8", errors="replace")
                                raise RuntimeError("HTTP %s: %s" % (response.status_code, body))
                            yield from _iter_sse_events(response)
                finally:
                    if owns_transport:
                        transport.close()

            return _ResponseStream(events())

        try:
            with _HTTP_LOCK:
                response = transport.post(url, **request_kwargs)
                if response.status_code >= 400:
                    body = response.text
                    raise RuntimeError("HTTP %s: %s" % (response.status_code, body))
                result = response.json()
        finally:
            if owns_transport:
                transport.close()
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

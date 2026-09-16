#!/usr/bin/env python3
"""a-Shell native loader and network broker for a WASI Hermes command.

The loader owns the native Python network stack. Hermes communicates with it
through length-prefixed JSON frames on the WASI child's stdin/stdout. Child
stderr is forwarded to the loader's stderr and never enters the RPC stream.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, BinaryIO

# JSON framing includes base64 expansion; keep this larger than the bounded
# request/response bodies while retaining a hard protocol allocation limit.
MAX_FRAME = 16 * 1024 * 1024
MAX_REQUEST_BODY = 2 * 1024 * 1024
MAX_RESPONSE_BODY = 8 * 1024 * 1024
MAX_SOCKET_BUFFER = 1024 * 1024


class LoaderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message



def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise LoaderError("MALFORMED_FRAME", "truncated frame header")
    size = int.from_bytes(header, "big")
    if size > MAX_FRAME:
        raise LoaderError("FRAME_TOO_LARGE", "request frame is too large")
    payload = stream.read(size)
    if len(payload) != size:
        raise LoaderError("MALFORMED_FRAME", "truncated frame payload")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoaderError("MALFORMED_FRAME", "request is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LoaderError("MALFORMED_FRAME", "request must be a JSON object")
    return value


def write_frame(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise LoaderError("FRAME_TOO_LARGE", "response frame is too large")
    stream.write(len(payload).to_bytes(4, "big"))
    stream.write(payload)
    stream.flush()


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "type": "response",
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _request(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url")
    if not isinstance(url, str) or len(url) > 4096:
        return _error(request_id, "INVALID_ARGUMENT", "url is invalid")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _error(request_id, "NETWORK_POLICY", "only http/https URLs are allowed")
    if parsed.username or parsed.password or parsed.fragment:
        return _error(request_id, "NETWORK_POLICY", "URL credentials/fragments are not allowed")
    method = params.get("method", "GET")
    if not isinstance(method, str) or not method.isalpha() or len(method) > 16:
        return _error(request_id, "INVALID_ARGUMENT", "method is invalid")
    headers = params.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        return _error(request_id, "INVALID_ARGUMENT", "headers must be a string map")
    body = params.get("body")
    if body is None:
        data = None
    elif isinstance(body, str):
        data = body.encode("utf-8")
    elif isinstance(body, dict) and body.get("encoding") == "base64" and isinstance(body.get("data"), str):
        try:
            data = base64.b64decode(body["data"], validate=True)
        except ValueError:
            return _error(request_id, "INVALID_ARGUMENT", "body is not valid base64")
    else:
        return _error(request_id, "INVALID_ARGUMENT", "body must be UTF-8 text or base64")
    if data is not None and len(data) > MAX_REQUEST_BODY:
        return _error(request_id, "REQUEST_TOO_LARGE", "request body is too large")
    try:
        timeout = float(params.get("timeout", 30))
    except (TypeError, ValueError):
        return _error(request_id, "INVALID_ARGUMENT", "timeout is invalid")
    if not 0 < timeout <= 300:
        return _error(request_id, "INVALID_ARGUMENT", "timeout must be 0 < timeout <= 300")

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BODY + 1)
            if len(payload) > MAX_RESPONSE_BODY:
                return _error(request_id, "RESPONSE_TOO_LARGE", "response body is too large")
            return {
                "type": "response",
                "id": request_id,
                "ok": True,
                "result": {
                    "status": int(response.status),
                    "headers": {str(k): str(v) for k, v in response.headers.items()},
                    "body": {"encoding": "base64", "data": base64.b64encode(payload).decode("ascii")},
                },
            }
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BODY)
        return {
            "type": "response",
            "id": request_id,
            "ok": True,
            "result": {
                "status": int(exc.code),
                "headers": {str(k): str(v) for k, v in exc.headers.items()},
                "body": {"encoding": "base64", "data": base64.b64encode(payload).decode("ascii")},
            },
        }
    except (urllib.error.URLError, TimeoutError, OSError):
        return _error(request_id, "NETWORK_ERROR", "native a-Shell network request failed")


def _socket_dispatch(sockets: dict[int, socket.socket], params: dict[str, Any]) -> dict[str, Any]:
    method = params.get("method")
    sid = params.get("id")
    if os.environ.get("HERMES_SOCKET_VERBOSE") == "1":
        print(f"SOCKETBROKER_REQUEST method={method!r} id={sid!r} params={params!r}", file=sys.stderr, flush=True)
    if method == "socket.hostname":
        return {"name": socket.gethostname()}
    if method == "socket.resolve":
        addresses = socket.getaddrinfo(params.get("host"), params.get("port"), params.get("family", 0), params.get("type", 0), params.get("proto", 0), params.get("flags", 0))
        return {"addresses": [list(item) for item in addresses]}
    if method == "socket.open":
        sock = socket.socket(params.get("family", socket.AF_INET), params.get("type", socket.SOCK_STREAM), params.get("proto", 0))
        sid = max(sockets, default=0) + 1
        sockets[sid] = sock
        return {"id": sid}
    if not isinstance(sid, int) or sid not in sockets:
        raise LoaderError("SOCKET_ERROR", "unknown socket id")
    sock = sockets[sid]
    if method == "socket.connect":
        sock.settimeout(params.get("timeout")); sock.connect((params["host"], params["port"])); return {}
    if method == "socket.bind":
        sock.bind((params["host"], params["port"])); return {}
    if method == "socket.listen":
        sock.listen(int(params.get("backlog", 0))); return {}
    if method == "socket.accept":
        sock.settimeout(params.get("timeout"))
        connection, address = sock.accept()
        accepted_id = max(sockets, default=0) + 1
        sockets[accepted_id] = connection
        return {"id": accepted_id, "address": list(address)}
    if method in {"socket.send", "socket.sendall"}:
        data = base64.b64decode(params["data"]["data"], validate=True)
        if len(data) > MAX_SOCKET_BUFFER: raise LoaderError("REQUEST_TOO_LARGE", "socket write is too large")
        if method == "socket.sendall": sock.sendall(data); return {"sent": len(data)}
        return {"sent": sock.send(data, params.get("flags", 0))}
    if method == "socket.recv":
        size = int(params.get("size", 0));
        if not 0 < size <= MAX_SOCKET_BUFFER: raise LoaderError("INVALID_ARGUMENT", "invalid recv size")
        sock.settimeout(params.get("timeout")); return {"data": base64.b64encode(sock.recv(size, params.get("flags", 0))).decode("ascii")}
    if method == "socket.timeout": sock.settimeout(params.get("timeout")); return {}
    if method == "socket.getopt": return {"value": sock.getsockopt(params["level"], params["option"])}
    if method == "socket.setopt": sock.setsockopt(params["level"], params["option"], params["value"]); return {}
    if method == "socket.name": return {"address": list(sock.getsockname())}
    if method == "socket.peer": return {"address": list(sock.getpeername())}
    if method == "socket.shutdown": sock.shutdown(params["how"]); return {}
    if method == "socket.start_tls":
        context = ssl.create_default_context() if params.get("check_hostname") else ssl._create_unverified_context()
        context.check_hostname = bool(params.get("check_hostname")); context.verify_mode = params.get("verify_mode", ssl.CERT_NONE)
        sockets[sid] = context.wrap_socket(sock, server_hostname=params.get("server_hostname"), do_handshake_on_connect=True)
        return {}
    if method == "socket.close": sock.close(); sockets.pop(sid, None); return {}
    raise LoaderError("CAPABILITY_UNAVAILABLE", "unsupported socket capability")


def _dispatch(frame: dict[str, Any], sockets: dict[int, socket.socket]) -> dict[str, Any] | None:
    request_id = frame.get("id")

    if frame.get("type") != "request": return _error(request_id, "MALFORMED_REQUEST", "type must be request")
    if frame.get("method") == "handshake":
        return {"type": "response", "id": request_id, "ok": True, "result": {"protocol": 2, "capabilities": ["stdio.output", "socket", "ssl", "net.request"]}}
    if frame.get("method", "").startswith("socket."):
        try: return {"type": "response", "id": request_id, "ok": True, "result": _socket_dispatch(sockets, {"method": frame["method"], **(frame.get("params") or {})})}
        except LoaderError as exc: return _error(request_id, exc.code, exc.message)
        except (OSError, ValueError, KeyError) as exc: return _error(request_id, "SOCKET_ERROR", str(exc))
    if frame.get("method") == "crypto.random" and isinstance(frame.get("params"), dict):
        size = int(frame["params"].get("size", 0))
        if not 0 <= size <= 1024 * 1024:
            return _error(request_id, "INVALID_ARGUMENT", "invalid random size")
        return {"type": "response", "id": request_id, "ok": True, "result": {"data": base64.b64encode(os.urandom(size)).decode("ascii")}}
    if frame.get("method") == "net.request" and isinstance(frame.get("params"), dict): return _request(request_id, frame["params"])
    return _error(request_id, "CAPABILITY_UNAVAILABLE", "unsupported capability")


def _reap_child(process: subprocess.Popen[bytes]) -> int:
    """Stop a child that lost its RPC pipe and always reap it promptly."""
    if process.poll() is None:
        process.terminate()
    try:
        return process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def serve_child(
    process: subprocess.Popen[bytes],
    *,
    write_lock: threading.Lock | None = None,
    input_stop: threading.Event | None = None,
) -> int:
    assert process.stdin is not None and process.stdout is not None
    sockets: dict[int, socket.socket] = {}
    while True:
        try:
            frame = read_frame(process.stdout)
            if frame is None:
                if process.poll() is None:
                    # The a-Shell shell-dispatch wrapper can close the WASM
                    # stdout before the wrapper itself exits.  Reap it here;
                    # waiting unconditionally below would leave loader.py
                    # hung after already routing the final output bytes.
                    returncode = _reap_child(process)
                else:
                    returncode = process.wait()
                break
            if frame.get("type") == "event":
                # Accept the old internal name from already-built WASM images,
                # then route decoded bytes to the matching host stream instead
                # of leaking the internal JSON frame to loader stdout.
                if frame.get("event") == "io.write":
                    frame = {**frame, "event": "stdio.output"}
                try:
                    _route_output_event(frame)
                except LoaderError as exc:
                    print(f"loader: WASM protocol error [{exc.code}]: {exc.message}", file=sys.stderr, flush=True)
                    returncode = _reap_child(process)
                    break
                continue
            response = _dispatch(frame, sockets)
            if response is not None:
                if write_lock is None:
                    write_frame(process.stdin, response)
                else:
                    with write_lock:
                        write_frame(process.stdin, response)
        except LoaderError as exc:
            print(f"loader: WASM protocol error [{exc.code}]: {exc.message}", file=sys.stderr, flush=True)
            returncode = _reap_child(process)
            break
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
            print(f"loader: WASM RPC pipe broken: {exc}", file=sys.stderr, flush=True)
            returncode = _reap_child(process)
            break
    for sock in sockets.values():
        sock.close()
    if input_stop is not None:
        input_stop.set()
    if "returncode" in locals():
        return returncode if returncode != 0 else 1
    return process.wait()


def forward_stderr(stream: BinaryIO) -> None:
    while data := stream.read(4096):
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()


def forward_input(process: subprocess.Popen[bytes], write_lock: threading.Lock, stop: threading.Event, *, interactive: bool = False) -> None:
    """Forward formatted host input frames to the direct WASM child."""
    assert process.stdin is not None
    try:
        while not stop.is_set():
            if interactive:
                ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
                if not ready:
                    continue
                data = sys.stdin.buffer.read(4096)
                frame = {"type": "input", "stream": "stdin", "data": {"encoding": "base64", "data": base64.b64encode(data).decode("ascii")}}
                if not data:
                    with write_lock:
                        write_frame(process.stdin, frame)
                    break
            else:
                ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
                if not ready:
                    continue
                frame = read_frame(sys.stdin.buffer)
                if frame is None:
                    break
            with write_lock:
                write_frame(process.stdin, frame)
    except (LoaderError, BrokenPipeError, OSError) as exc:
        print(f"loader: input protocol error: {exc}", file=sys.stderr, flush=True)
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass


def _route_output_event(frame: dict[str, Any]) -> None:
    """Decode a WASM output event and route it to the matching host stream."""
    event = frame.get("event")
    if event != "stdio.output":
        raise LoaderError("UNEXPECTED_EVENT", f"unsupported WASM event: {event}")
    stream = frame.get("stream")
    if stream not in {"stdout", "stderr"}:
        raise LoaderError("MALFORMED_EVENT", "stdio.output stream is invalid")
    data = frame.get("data")
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("data"), str):
        raise LoaderError("MALFORMED_EVENT", "stdio.output data must be base64")
    try:
        payload = base64.b64decode(data["data"], validate=True)
    except (ValueError, TypeError) as exc:
        raise LoaderError("MALFORMED_EVENT", "stdio.output data is not valid base64") from exc
    target = sys.stdout.buffer if stream == "stdout" else sys.stderr.buffer
    target.write(payload)
    target.flush()


def _find_artifact() -> Path:
    """Find the direct WASM delivery artifact."""
    candidates: list[Path] = []
    for start in (Path(__file__).parent, Path.cwd()):
        current = start.resolve()
        for directory in (current, *current.parents):
            candidate = directory / "hermes.wasm"
            if candidate not in candidates:
                candidates.append(candidate)
    configured = os.environ.get("HERMES_ARTIFACT")
    if configured:
        candidates.insert(0, Path(configured).expanduser())
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise LoaderError("ARTIFACT_NOT_FOUND", f"Hermes runtime not found; searched: {searched}")


def _find_runtime_root(artifact: Path) -> Path:
    configured = os.environ.get("HERMES_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    adjacent = artifact.parent / "hermes-runtime"
    if adjacent.is_dir():
        return adjacent.resolve()
    return artifact.parent.resolve()


def _find_wasm_command() -> str:
    """Find a-Shell's wasm launcher, with Wasmtime as a desktop fallback."""
    configured = os.environ.get("HERMES_WASM_COMMAND") or os.environ.get("WASM_COMMAND")
    if configured:
        return str(Path(configured).expanduser())
    for name in ("wasm", "wasmtime"):
        found = shutil.which(name)
        if found:
            # a-Shell exposes `wasm` as an app-dispatched command.  Its PATH
            # entry is a zero-byte placeholder, not an executable that can be
            # passed as an absolute path to Popen.
            if name == "wasm":
                try:
                    if Path(found).stat().st_size == 0:
                        return "__ashell_direct_wasm__"
                except OSError:
                    pass
            return found
    # a-Shell does not put its bundled launcher on PATH.  The application UUID
    # changes between installs, so discover it rather than hard-coding one.
    bundle_candidates = sorted(Path("/private/var/containers/Bundle/Application").glob("*/a-Shell.app/bin/wasm"))
    if bundle_candidates:
        try:
            if bundle_candidates[0].stat().st_size == 0:
                return "__ashell_direct_wasm__"
        except OSError:
            pass
        return str(bundle_candidates[0])
    raise LoaderError(
        "WASM_COMMAND_NOT_FOUND",
        "could not find a-Shell wasm launcher or Wasmtime (set HERMES_WASM_COMMAND only for debugging)",
    )


def _build_command(artifact: Path, args: list[str]) -> list[str]:
    wasm_command = _find_wasm_command()
    if Path(wasm_command).name == "wasmtime":
        root = str(_find_runtime_root(artifact))
        home = os.environ.get("HOME") or "/"
        command = [
            wasm_command, "run",
            "--env", f"HOME={home}",
            "--dir", f"{root}::/",
        ]
    else:
        command = [wasm_command]
    if wasm_command == "__ashell_direct_wasm__":
        # a-Shell dispatches a path ending in .wasm directly; invoking its
        # zero-byte `wasm` placeholder through Popen can block indefinitely.
        return [f"./{artifact.name}", *args]
    # hermes.wasm owns the module dispatch and formatted stdio contract.
    # loader.py only selects the host runner and forwards user arguments.
    return [*command, artifact.name, *args]


def main() -> int:
    # loader.py is the host-side frame broker; hermes.wasm is the direct WASM
    # delivery artifact and never gets replaced by a shell launcher.
    args = sys.argv[1:]

    try:
        artifact = _find_artifact()
        command = _build_command(artifact, args)
    except LoaderError as exc:
        print(f"loader: {exc.message}", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    # These values are interpreted inside the WASI guest, not by the host.
    # Host paths such as W:/... or /mnt/w/... are invisible after --dir maps
    # the runtime tree to guest /.  Keep the guest paths deterministic so the
    # interpreter can find encodings before sitecustomize runs.
    environment["PYTHONHOME"] = "/"
    runtime_archive = os.environ.get("HERMES_RUNTIME_ARCHIVE", "hermesrt.zip")
    environment["PYTHONPATH"] = os.pathsep.join(
        (f"./{runtime_archive}", f"{runtime_archive}", f"/{runtime_archive}")
    )
    # a-Shell exposes the directory containing the dispatched WASM as the
    # writable working directory.  The inherited HOME points at the app
    # container, which is not writable by the shell process; every Hermes
    # command (not only WebUI) must use a relative writable state directory.
    environment.setdefault("HERMES_HOME", "hermes-home")
    # The a-Shell WASI process has a small thread budget.  Keep optional
    # startup maintenance synchronous/disabled so the interactive CLI does
    # not fail before it can accept a command.
    environment.setdefault("HERMES_DISABLE_BACKGROUND_DISCOVERY", "1")
    environment.setdefault("HERMES_DISABLE_BACKGROUND_CHECKPOINTS", "1")
    environment.setdefault("HERMES_DISABLE_STARTUP_PREWARM", "1")
    environment.setdefault("HERMES_DEFER_AGENT_STARTUP", "1")
    environment.setdefault("HERMES_DISABLE_TUI_SPINNER", "1")
    environment.setdefault("HERMES_DISABLE_TUI_THREADS", "1")
    if args and args[0] == "webui":
        environment.setdefault("HERMES_WEBUI_DEFAULT_WORKSPACE", "workspace")
        environment.setdefault("HERMES_WEBUI_STATE_DIR", "webui-state")
    if args and args[0] == "sockettest":
        environment.setdefault("HERMES_SOCKET_VERBOSE", "1")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        environment["HERMES_INTERACTIVE"] = "1"
    ashell_shell_dispatch = bool(command and command[0].startswith("./") and command[0].endswith(".wasm"))
    spawn_command: str | list[str] = shlex.join(command) if ashell_shell_dispatch else command
    child = subprocess.Popen(
        spawn_command,
        cwd=str(artifact.parent),
        shell=ashell_shell_dispatch,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stderr is not None
    stderr_thread = threading.Thread(target=forward_stderr, args=(child.stderr,), daemon=False)
    stderr_thread.start()
    write_lock = threading.Lock()
    input_stop = threading.Event()
    # Version queries are self-contained.  Closing stdin lets the a-Shell
    # WASM dispatcher exit instead of waiting for a framed request that can
    # never arrive while the host is only asking for metadata.
    input_thread: threading.Thread | None = None
    needs_input = args not in (["--version"], ["-V"])
    if needs_input and interactive:
        input_thread = threading.Thread(
            target=forward_input,
            args=(child, write_lock, input_stop),
            kwargs={"interactive": interactive},
            daemon=True,
        )
        input_thread.start()
    elif needs_input:
        # Every non-version runtime command may use the same stdin pipe for
        # capability-broker responses (socket/TLS/network).  Keep it open;
        # only a real TTY gets a separate host-input forwarding thread.
        pass
    elif args in (["--version"], ["-V"]):
        assert child.stdin is not None
        child.stdin.close()
    else:
        # Version queries close stdin above; all other commands keep this pipe
        # open for capability-broker responses.
        pass
    try:
        returncode = serve_child(child, write_lock=write_lock, input_stop=input_stop)
        if args and args[0] == "webui":
            if returncode != 0:
                print(f"loader: WebUI WASM exited with status {returncode}", file=sys.stderr, flush=True)
            else:
                print("loader: WebUI WASM exited before serving", file=sys.stderr, flush=True)
        return returncode
    except KeyboardInterrupt:
        print("loader: interrupted; stopping WASM", file=sys.stderr, flush=True)
        return _reap_child(child)
    finally:
        input_stop.set()

        if input_thread is not None:
            input_thread.join(timeout=2)
        stderr_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())

# Overlay layout

- `overlay/python/`: files copied into the CPython WASI `lib/python3.13/`
  tree during the build. It contains the framed transport, stdio bootstrap,
  socket/TLS facades, and host capability helpers.
- `overlay/hermes/`: files copied over the build-time Hermes Agent checkout.
- `overlay/webui/`: files copied over the build-time Hermes WebUI checkout.

Nothing in these directories is fetched from or written back to the upstream
repositories. The build owns the copy step and fails if an overlay target does
not exist.

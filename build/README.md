# Build-time source downloads

`hermes-agent` and `hermes-webui` are not repository submodules. Run
`./build/fetch-sources.sh` to download the pinned commits into
`build/sources/`. Override `HERMES_AGENT_COMMIT` or `HERMES_WEBUI_COMMIT` when
intentionally changing the source pins.

The checked-in `overlay/` tree contains only files owned by this project:
Python/WASI runtime overlays in `overlay/python`, Hermes Agent patches in
`overlay/hermes`, and WebUI patches in `overlay/webui`.

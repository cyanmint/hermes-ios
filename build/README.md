# Build-time source downloads

`hermes-agent` and `hermes-webui` are not repository submodules. Run
`./build/fetch-sources.sh` to download the pinned commits into
`build/external/`. Override `HERMES_AGENT_COMMIT` or `HERMES_WEBUI_COMMIT` when
intentionally changing the source pins.

The checked-in `overlay/` tree contains only files owned by this project:
Python/WASI runtime overlays in `overlay/python`, Hermes Agent patches in
`overlay/hermes`, and WebUI patches in `overlay/webui`.

Run `package-hermes.sh` after the WASI build to create the two runtime entrypoints:

- `hermes.wasm`: the raw WASM module, never a shell launcher
- `loader.py`: the host frame and capability broker

The generated `hermes-runtime/` directory contains CPython support files used by
loader.py and is ignored as a local build output.

The WASI toolchain is the a-Shell fork, not the generic upstream SDK. The
build obtains tag `wasi-sdk-aShell-22` from
`https://github.com/holzschu/wasi-sdk.git`, verifies commit
`3d2154daab00d4479d855a661355566b2e393702`, and installs it under
`/root/hermes-build/wasi-sdk-ashell-22`. Override `WASI_SDK_PATH` only when
pointing at another already-built copy of that a-Shell SDK.

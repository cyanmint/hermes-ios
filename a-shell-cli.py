#!/usr/bin/env python3
"""Small a-Shell-safe Hermes CLI for provider configuration.

The full Hermes CLI imports Rich and provider-specific optional modules while
building every parser. a-Shell intentionally ships a smaller Python runtime,
so this command keeps the provider/configuration path dependency-light.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("HERMES_HOME", ".")).expanduser()
CONFIG = HOME / "config.yaml"
ENV = HOME / ".env"

PROVIDER_ENV = {
    "openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "xai": "XAI_API_KEY",
    "zai": "ZAI_API_KEY", "glm": "GLM_API_KEY",
    "minimax": "MINIMAX_API_KEY", "kimi": "KIMI_API_KEY",
    "alibaba": "DASHSCOPE_API_KEY", "huggingface": "HF_TOKEN",
}


def load() -> dict:
    if not CONFIG.exists():
        return {}
    with CONFIG.open(encoding="utf-8") as f:
        value = yaml.safe_load(f) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"Invalid config root: {CONFIG}")
    return value


def save(value: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix(".yaml.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(value, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    temp.replace(CONFIG)


def parts(key: str) -> list[str]:
    result = [p for p in key.split(".") if p]
    if not result:
        raise SystemExit("Configuration key cannot be empty")
    return result


def get_value(value: dict, key: str):
    current = value
    for part in parts(key):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def set_value(value: dict, key: str, raw: str) -> None:
    current = value
    path = parts(key)
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        parsed = raw
    current[path[-1]] = raw if parsed is None and raw else parsed


def unset_value(value: dict, key: str) -> None:
    current = value
    path = parts(key)
    for part in path[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(path[-1], None)


def print_value(key: str) -> None:
    value = get_value(load(), key)
    if isinstance(value, (dict, list)):
        print(yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip())
    elif value is None:
        print("")
    else:
        print(value)


def env_set(name: str, secret: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    replacement = f"{name}={secret}"
    found = False
    output = []
    for line in lines:
        if line.startswith(name + "="):
            output.append(replacement)
            found = True
        else:
            output.append(line)
    if not found:
        output.append(replacement)
    ENV.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    try:
        ENV.chmod(0o600)
    except OSError:
        pass


def configure(provider: str | None = None, model: str | None = None, base_url: str | None = None) -> int:
    cfg = load()
    provider = provider or input("Provider: ").strip()
    model = model or input("Model: ").strip()
    set_value(cfg, "model.provider", provider)
    if model:
        set_value(cfg, "model.default", model)
    if base_url is None and provider == "custom":
        base_url = input("Base URL (optional): ").strip()
    if base_url:
        set_value(cfg, "model.base_url", base_url)
    save(cfg)
    env_name = PROVIDER_ENV.get(provider.lower())
    if env_name:
        secret = getpass.getpass(f"{env_name} (Enter to keep current): ")
        if secret:
            env_set(env_name, secret)
    print(f"Saved provider configuration to {CONFIG}")
    print(f"provider: {provider}")
    if model:
        print(f"model: {model}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    config = sub.add_parser("config", help="View and edit provider configuration")
    config_sub = config.add_subparsers(dest="action", required=True)
    config_sub.add_parser("path").set_defaults(action_fn=lambda _: print(CONFIG))
    get = config_sub.add_parser("get"); get.add_argument("key"); get.set_defaults(action_fn=lambda a: print_value(a.key))
    setp = config_sub.add_parser("set"); setp.add_argument("key"); setp.add_argument("value"); setp.set_defaults(action_fn=lambda a: (set_value((cfg := load()), a.key, a.value), save(cfg), print(f"Saved {a.key}")))
    unset = config_sub.add_parser("unset"); unset.add_argument("key"); unset.set_defaults(action_fn=lambda a: (unset_value((cfg := load()), a.key), save(cfg), print(f"Unset {a.key}")))
    provider = sub.add_parser("provider", help="Configure provider, model, base URL and API key")
    provider.add_argument("name", nargs="?"); provider.add_argument("--model"); provider.add_argument("--base-url")
    sub.add_parser("model", help="Configure provider and default model").set_defaults(configure=True)
    setup = sub.add_parser("setup", help="Provider setup"); setup.add_argument("section", nargs="?"); setup.set_defaults(configure=True)
    args = parser.parse_args(argv)
    if hasattr(args, "action_fn"):
        args.action_fn(args); return 0
    if getattr(args, "configure", False):
        return configure()
    if args.command == "provider":
        return configure(args.name, args.model, args.base_url)
    parser.print_help(); return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

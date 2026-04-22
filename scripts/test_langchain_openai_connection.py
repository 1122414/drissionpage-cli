from __future__ import annotations

import argparse
import os
import sys


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "kimi-k2.5"
DEFAULT_PROMPT = "Reply with exactly: ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="test_langchain_openai_connection")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""), help="API key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # if not args.api_key:
    #     raise SystemExit("Missing API key. Pass --api-key or set OPENAI_API_KEY.")

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'langchain_openai'. Install it in the dp-cli environment first, for example: "
            "pip install langchain-openai"
        ) from exc

    llm = ChatOpenAI(
        # api_key=args.api_key,
        api_key="sk-098796cc7d5e46588c56a4d582cfc9b6",
        base_url=args.base_url,
        model=args.model,
        temperature=0,
        timeout=60,
    )

    response = llm.invoke(args.prompt)
    content = getattr(response, "content", response)
    print("Connection succeeded.")
    print(f"model={args.model}")
    print(f"base_url={args.base_url}")
    print("response:")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

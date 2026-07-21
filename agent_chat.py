"""
agent_chat.py — Interactive chat with the UI test agent.

Usage:
    python agent_chat.py [--headless] [--output NAME]

Each message you send becomes a new agent task that runs against the same live
browser. When you are happy with the interaction, use /save to persist the
trace, then /quit. Generate a Playwright test with script_generator.py.
"""

import argparse
import asyncio

from agent.chat_session import chat_session


def main():
    parser = argparse.ArgumentParser(description="Chat interactively with the UI test agent")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: visible so you can watch)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="chat",
        help="Default trace output name (default: chat)",
    )
    args = parser.parse_args()

    saved = asyncio.run(chat_session(headless=args.headless, output_name=args.output))
    if saved:
        print(f"\nNext step: python script_generator.py -i {saved} -o tests/generated/test_{args.output}.py")


if __name__ == "__main__":
    main()

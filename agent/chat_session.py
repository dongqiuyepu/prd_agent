"""
Chat-based UI automation assistant.

The assistant talks to you directly. When a request needs a browser, it invokes
the browser-use agent as a tool, records the trace, and reports the result.
General questions are answered by the LLM without opening a browser.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from browser_use import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.llm.messages import AssistantMessage, SystemMessage, UserMessage

from agent.recorder import create_agent, create_llm, _extract_trace, GENERATED_DIR


class RouterOutput(BaseModel):
    """LLM decision about whether a user message needs a browser."""

    needs_browser: bool = Field(
        description="True if the user wants to perform actions on a website or get info only available by browsing."
    )
    direct_answer: str = Field(
        default="",
        description="Concise answer when needs_browser is False.",
    )
    browser_task: Optional[str] = Field(
        default=None,
        description="When needs_browser is True, a concise task string for the browser agent, including the URL if mentioned.",
    )


COMMANDS = {
    "/help": "show this help message",
    "/quit": "exit the chat (you will be prompted to save)",
    "/save [name]": "save the recorded trace as generated_scripts/<name>.json",
    "/generate <trace> [output]": "generate a Playwright test from a saved trace",
}

ROUTER_SYSTEM = SystemMessage(
    content=(
        "You are the routing brain of a UI-test assistant. "
        "Decide whether the user's latest message requires a web browser. "
        "If it asks about files, code, configuration, or anything that does not involve "
        "interacting with a web page, set needs_browser=false and answer directly. "
        "If it asks to navigate, click, fill forms, verify text, or any browser action, "
        "set needs_browser=true and provide a clear, self-contained browser_task."
    )
)

CHAT_SYSTEM = SystemMessage(
    content="You are a helpful assistant for a UI test automation project. Answer briefly and accurately."
)


async def _route_message(messages: list) -> RouterOutput:
    llm = create_llm()
    response = await llm.ainvoke(messages, output_format=RouterOutput)
    return response.completion


async def _answer_directly(messages: list) -> str:
    llm = create_llm()
    response = await llm.ainvoke(messages, output_format=None)
    return str(response.completion)


async def _run_browser_task(task: str, headless: bool) -> tuple[list[dict], str]:
    """Run the browser-use agent as a tool and return the recorded trace + final result."""
    from agent.recorder import _make_step_end_handler

    agent: Agent = create_agent(task=task, headless=headless)
    search_elements: dict[int, dict] = {}
    history: AgentHistoryList = await agent.run(on_step_end=await _make_step_end_handler(search_elements))
    actions = _extract_trace(history, search_elements)
    final_result = history.final_result() or ""
    return actions, final_result


def _save_trace(actions: list[dict], task: str, final_result: str, output_name: str) -> Path:
    """Persist accumulated actions as a trace JSON."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    clean_name = output_name.removesuffix(".json") if output_name.endswith(".json") else output_name
    output_path = GENERATED_DIR / f"{clean_name}.json"
    payload = {
        "scenario": output_name,
        "task": task,
        "final_result": final_result,
        "actions": actions,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return output_path


async def chat_session(
    headless: bool = False,
    output_name: str = "chat",
) -> Optional[Path]:
    """
    Start a chat session with the UI automation assistant.

    General questions are answered directly. Browser work is done on demand and
    the resulting trace can be saved with /save.
    """
    all_actions: list[dict] = []
    last_final_result = ""
    saved_path: Optional[Path] = None

    # Keep a short conversation history for direct answers.
    history: list = [CHAT_SYSTEM]

    print("\nAssistant ready. Type /help for commands.")

    try:
        while True:
            user_input = (await asyncio.to_thread(input, "> ")).strip()
            if not user_input:
                continue

            if user_input == "/help":
                for cmd, desc in COMMANDS.items():
                    print(f"  {cmd:<18} {desc}")
                continue

            if user_input == "/quit":
                break

            if user_input.startswith("/save"):
                parts = user_input.split(None, 1)
                name = parts[1].strip() if len(parts) > 1 else output_name
                if not all_actions:
                    print("No browser actions recorded yet.")
                    continue
                saved_path = _save_trace(all_actions, "interactive chat", last_final_result, name)
                print(f"Saved {len(all_actions)} actions to: {saved_path}")
                continue

            if user_input.startswith("/generate"):
                parts = [p.strip() for p in user_input.split(None, 2)]
                if len(parts) < 2:
                    print("Usage: /generate <trace_name> [output_path]")
                    continue

                trace_arg = parts[1]
                trace_path = Path(trace_arg)
                if not trace_path.is_absolute() and str(trace_path.parent) == ".":
                    trace_path = GENERATED_DIR / trace_path

                if not trace_path.exists():
                    alt = GENERATED_DIR / f"{trace_arg}.json"
                    if alt.exists():
                        trace_path = alt
                    else:
                        print(f"Trace not found: {trace_path}")
                        continue

                if len(parts) > 2:
                    output_path = Path(parts[2])
                else:
                    output_path = Path("tests/generated") / f"test_{Path(trace_arg).stem}.py"

                output_path.parent.mkdir(parents=True, exist_ok=True)
                import script_generator

                script_generator.generate_script(trace_path=trace_path, output_path=output_path)
                print(f"Generated Playwright test: {output_path}")
                continue

            # Router: ask the LLM whether a browser is needed.
            router_messages = [ROUTER_SYSTEM, *history[-6:], UserMessage(content=user_input)]
            decision = await _route_message(router_messages)

            if not decision.needs_browser:
                answer = decision.direct_answer or await _answer_directly(
                    [CHAT_SYSTEM, *history[-6:], UserMessage(content=user_input)]
                )
                print(answer)
                history.append(UserMessage(content=user_input))
                history.append(AssistantMessage(content=answer))
                continue

            # Run the browser agent as a tool.
            browser_task = decision.browser_task or user_input
            print("Opening browser and running task...")
            actions, final_result = await _run_browser_task(browser_task, headless=headless)
            all_actions.extend(actions)
            last_final_result = final_result or last_final_result
            print(f"Done. Recorded {len(actions)} actions.")
            if final_result:
                print(final_result.strip())

            history.append(UserMessage(content=user_input))
            history.append(AssistantMessage(content=final_result or "Task completed."))
    except KeyboardInterrupt:
        print("\nInterrupted.")

    if not saved_path and all_actions:
        save = await asyncio.to_thread(input, "\nSave recorded trace before quitting? [y/N]: ")
        if save.strip().lower() in ("y", "yes"):
            saved_path = _save_trace(all_actions, "interactive chat", last_final_result, output_name)
            print(f"Saved to: {saved_path}")

    return saved_path

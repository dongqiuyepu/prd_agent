"""
UI Test Agent — Recorder

Phase 1 of the two-phase workflow:
  1. (THIS FILE)  Run the AI agent against the live app.
                  It observes the UI and performs actions.
                  All actions are saved as a structured trace JSON.

  2. (script_generator.py)  Read the trace JSON and emit a
                  deterministic Playwright pytest script that
                  can be re-run without any LLM.
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import TypeVar
from dotenv import load_dotenv
from browser_use import Agent, BrowserProfile
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.events import NavigateToUrlEvent
from browser_use.browser.session import BrowserSession
from browser_use.llm.openai.chat import ChatOpenAI, OpenAIMessageSerializer
from browser_use.llm.views import ChatInvokeCompletion
from browser_use.llm.messages import BaseMessage

load_dotenv()

GENERATED_DIR = Path(__file__).parent.parent / "generated_scripts"

T = TypeVar("T")

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_to_json(text: str) -> str:
    """
    Extract the first JSON object/array from a string that may contain:
      - markdown code fences  (```json ... ```)
      - a prose preamble before the JSON
      - trailing text after the closing brace/bracket
    """
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is not None:
        text = text[start:]

    return text.strip()


class DeepSeekChatOpenAI(ChatOpenAI):
    """
    ChatOpenAI subclass for DeepSeek that strips markdown fences and prose
    preambles from the model's output before Pydantic structured parsing.

    DeepSeek frequently wraps its JSON in ```json ... ``` blocks or prefixes
    it with a reasoning paragraph, which causes model_validate_json() to fail.

    Strategy: call super().ainvoke() and if it raises a ValidationError
    (Pydantic parse failure), fetch the raw content directly and re-parse
    after stripping fences.
    """

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        if output_format is None:
            return await super().ainvoke(messages, output_format, **kwargs)

        try:
            return await super().ainvoke(messages, output_format, **kwargs)
        except Exception as exc:
            if "json_invalid" not in str(exc) and "Invalid JSON" not in str(exc):
                raise

            openai_messages = OpenAIMessageSerializer.serialize_messages(messages)
            client = self.get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                temperature=self.temperature,
            )
            raw_content = response.choices[0].message.content or ""
            cleaned = _strip_to_json(raw_content)
            parsed = output_format.model_validate_json(cleaned)
            usage = self._get_usage(response)
            return ChatInvokeCompletion(
                completion=parsed,
                usage=usage,
                stop_reason=response.choices[0].finish_reason,
            )


def create_llm() -> DeepSeekChatOpenAI:
    """Create a DeepSeek-backed LLM with markdown-fence stripping.

    DeepSeek does not support response_format / structured output JSON schema,
    so we disable it and inject the schema into the system prompt instead.
    DeepSeek also wraps JSON in markdown fences; DeepSeekChatOpenAI strips those.
    """
    return DeepSeekChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        dont_force_structured_output=True,
        add_schema_to_system_prompt=True,
    )


class FastBrowserSession(BrowserSession):
    """Browser session that waits only for DOMContentLoaded by default.

    browser-use's default page-load strategy is ``wait_until='load'``,
    which blocks until every image, stylesheet, and font finishes loading.
    That is often far longer than necessary for UI automation and makes the
    agent feel slow. This subclass overrides the default for navigations to
    ``domcontentloaded``. The strategy is read from ``AGENT_PAGE_LOAD_WAIT`` so
    it can be tuned or reverted without code changes.
    """

    async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
        wait_mode = os.getenv("AGENT_PAGE_LOAD_WAIT", "domcontentloaded")
        if wait_mode in ("commit", "domcontentloaded", "load", "networkidle"):
            event.wait_until = wait_mode  # type: ignore[assignment]
        return await super().on_NavigateToUrlEvent(event)


def create_browser_session(
    headless: bool,
    keep_alive: bool = False,
    cdp_url: str | None = None,
) -> BrowserSession:
    """Create a browser session with reduced page-load waiting.

    Pass ``cdp_url`` to reconnect to an already-running browser process instead
    of launching a new one. This is used by the interactive chat mode to keep
    the same browser window across turns.
    """
    profile = BrowserProfile(
        headless=headless,
        keep_alive=keep_alive,
        minimum_wait_page_load_time=float(os.getenv("AGENT_MIN_PAGE_LOAD_S", "0.1")),
        wait_for_network_idle_page_load_time=float(os.getenv("AGENT_NETWORK_IDLE_S", "0.1")),
    )
    kwargs: dict[str, object] = {"browser_profile": profile}
    if cdp_url:
        kwargs["cdp_url"] = cdp_url
    return FastBrowserSession(**kwargs)


def create_agent(
    task: str,
    headless: bool = False,
    browser_session: BrowserSession | None = None,
) -> Agent:
    """
    Create a browser-use Agent for the given task.

    Args:
        task: Natural language description of what the agent should do.
        headless: Run browser headlessly (default False so you can watch it).
        browser_session: Optional browser session to reuse. If omitted, a new
            fast-startup session is created.

    Returns:
        Configured browser-use Agent ready to run.
    """
    llm = create_llm()
    if browser_session is None:
        browser_session = create_browser_session(headless=headless)
    return Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
    )


def _element_selector(el: dict | None) -> dict | None:
    """
    Extract the most useful selector fields from a browser-use interacted_element.

    Priority: id > css_selector > xpath > (label, placeholder, tag, text).
    All fields are kept so the script generator can choose the best strategy.

    For unclean HTML without ids, we also capture class, role, data-* attrs,
    for, and value so the generator has stable fallback options.
    """
    if not el:
        return None
    # browser-use 0.12.x returns a DOMInteractedElement dataclass, not a plain
    # dict. Convert it and normalise field names to the canonical keys the rest
    # of this function and script_generator.py expect.
    if not isinstance(el, dict):
        raw = el.to_dict() if hasattr(el, "to_dict") else vars(el)
        el = {
            "id":           raw.get("attributes", {}).get("id") if isinstance(raw.get("attributes"), dict) else None,
            "xpath":        raw.get("x_path") or raw.get("xpath"),
            "tag_name":     raw.get("node_name") or raw.get("tag_name"),
            "attributes":   raw.get("attributes") or {},
            "text":         raw.get("ax_name") or raw.get("node_value") or raw.get("text"),
        }
    kept = {}
    for key in ("id", "css_selector", "xpath", "tag_name", "attributes"):
        val = el.get(key)
        if val:
            kept[key] = val
    attrs = el.get("attributes") or {}
    useful_attrs = ("placeholder", "name", "type", "aria-label",
                    "role", "class", "for", "value", "title")
    for attr in useful_attrs:
        if attrs.get(attr):
            kept.setdefault("attributes", {})[attr] = attrs[attr]
    for attr, val in attrs.items():
        if attr.startswith("data-") and val:
            kept.setdefault("attributes", {})[attr] = val
    text = el.get("text") or el.get("inner_text")
    if text:
        kept["text"] = text[:80]
    label = el.get("label")
    if label:
        kept["label"] = label
    return kept or None


async def _capture_search_element(agent: Agent, action: dict) -> dict | None:
    """When the agent runs a search/find/verify action, locate the matching DOM node
    in the current page and return element selector metadata so the generated
    test can assert against a specific field instead of scanning the whole body.
    """
    action_name = next((k for k in action if k != "element"), None)
    if action_name not in ("search_page", "find_text", "verify_text"):
        return None

    params = action.get(action_name, {})
    pattern = params.get("pattern")
    if not pattern:
        return None

    browser_session = getattr(agent, "browser_session", None)
    if not browser_session:
        return None

    try:
        page = await browser_session.get_current_page()
        if not page:
            return None
    except Exception:
        return None

    find_js = r"""
    (pattern) => {
        function getPath(el) {
            if (!el) return '';
            const parts = [];
            while (el && el.nodeType === Node.ELEMENT_NODE) {
                let name = el.tagName.toLowerCase();
                if (el.id) {
                    parts.unshift(name + '#' + el.id);
                    break;
                }
                let sib = el, nth = 1;
                while (sib.previousElementSibling) {
                    sib = sib.previousElementSibling;
                    if (sib.tagName.toLowerCase() === name) nth++;
                }
                if (nth > 1) name += '[' + nth + ']';
                parts.unshift(name);
                el = el.parentElement;
            }
            return '/' + parts.join('/');
        }

        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
        let node;
        const lower = pattern.toLowerCase();
        while ((node = walker.nextNode())) {
            if (node.textContent.toLowerCase().includes(lower)) {
                const el = node.parentElement;
                const attrs = {};
                for (const a of el.attributes) {
                    attrs[a.name] = a.value;
                }
                return JSON.stringify({
                    tag_name: el.tagName,
                    text: el.textContent.trim().slice(0, 120),
                    xpath: getPath(el),
                    attributes: attrs,
                });
            }
        }
        return null;
    }
    """

    try:
        result = await page.evaluate(find_js, pattern)
        if not result:
            return None
        data = json.loads(result) if isinstance(result, str) else result
        return _element_selector(data)
    except Exception:
        return None


async def _make_step_end_handler(search_elements: dict):
    """Return an on_step_end hook that captures element metadata for search/find actions."""
    async def on_step_end(agent: Agent):
        actions = agent.history.model_actions()
        if not actions:
            return
        idx = len(actions) - 1
        if idx in search_elements:
            return
        el = await _capture_search_element(agent, actions[idx])
        if el:
            search_elements[idx] = el

    return on_step_end


def _extract_trace(history: AgentHistoryList, search_elements: dict | None = None) -> list[dict]:
    """
    Extract a clean list of recorded actions from the agent's history.

    Each entry has the shape:
      { "action_name": {...params...}, "element": {selector metadata} }

    The "element" dict is derived from browser-use's interacted_element and
    contains id, xpath, css_selector, placeholder, etc. so that the script
    generator can produce robust locators even for messy HTML without ids.

    Consecutive duplicate actions (same action name + params) produced by
    agent retry loops are collapsed into a single entry.
    """
    raw_actions = history.model_actions()
    trace = []
    search_elements = search_elements or {}
    for idx, raw in enumerate(raw_actions):
        # model_actions() may return dicts or ActionModel objects
        if isinstance(raw, dict):
            interacted = raw.get("interacted_element")
            entry = {k: v for k, v in raw.items() if k != "interacted_element"}
        else:
            interacted = getattr(raw, "interacted_element", None)
            entry = raw.model_dump(exclude={"interacted_element"}) if hasattr(raw, "model_dump") else vars(raw)
        el_meta = _element_selector(interacted) or search_elements.get(idx)
        if not entry:
            continue
        if el_meta:
            entry["element"] = el_meta

        action_name = next((k for k in entry if k != "element"), None)
        if action_name and trace:
            prev = trace[-1]
            prev_name = next((k for k in prev if k != "element"), None)
            if prev_name == action_name and prev.get(action_name) == entry.get(action_name):
                continue

        trace.append(entry)
    return trace


async def record_scenario(
    task: str,
    output_name: str,
    headless: bool = False,
) -> Path:
    """
    Run the AI agent for the given natural-language task, record every
    browser action it performs, and save the trace to a JSON file.

    Args:
        task:        Natural-language description of the test scenario.
        output_name: Base name for the output file (no extension).
        headless:    Run browser headlessly.

    Returns:
        Path to the saved trace JSON file.
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / f"{output_name}.json"

    browser_session = create_browser_session(headless=headless)
    agent = create_agent(task=task, headless=headless, browser_session=browser_session)

    search_elements: dict[int, dict] = {}
    history: AgentHistoryList = await agent.run(on_step_end=await _make_step_end_handler(search_elements))

    trace = _extract_trace(history, search_elements)
    final_result = history.final_result() or ""

    payload = {
        "scenario": output_name,
        "task": task,
        "final_result": final_result,
        "actions": trace,
    }

    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n[recorder] Saved {len(trace)} actions to: {output_path}")
    print(f"[recorder] Final result: {final_result}")
    return output_path

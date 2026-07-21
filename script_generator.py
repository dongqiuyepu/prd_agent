"""
script_generator.py — Phase 2: Convert a recorded trace JSON into a
deterministic Playwright pytest script.

The generated script:
  - Uses pure Playwright (no LLM, no browser-use at runtime)
  - Is parameterized: data comes from tests/test_data.py
  - Can be run repeatedly: pytest tests/generated/test_<name>.py

Usage:
    python script_generator.py \\
        -i generated_scripts/standard_30_360_calculation.json \\
        -o tests/generated/test_standard_30_360.py \\
        -n test_standard_30_360_calculation

The generator maps browser-use action types to Playwright calls:
  go_to_url          → page.goto(url)
  input_text         → page.locator(...).fill(value)
  click_element      → page.locator(...).click()
  select_dropdown    → page.locator(...).select_option(value)
  scroll_down        → page.evaluate("window.scrollBy(0, 500)")
  wait               → page.wait_for_timeout(ms)
"""

import argparse
import json
import re
import textwrap
from pathlib import Path

try:
    import tomllib  # Python >= 3.11
except ImportError:  # pragma: no cover
    tomllib = None


ROOT_DIR = Path(__file__).resolve().parent


def _load_app_url() -> str:
    """Load the configured app URL from pyproject.toml, with a sensible default."""
    default = "http://localhost:5001"
    if tomllib is None:
        return default
    pyproject = ROOT_DIR / "pyproject.toml"
    if not pyproject.exists():
        return default
    try:
        with open(pyproject, "rb") as f:
            config = tomllib.load(f)
        return config.get("tool", {}).get("ui_automation", {}).get("app_url", default)
    except Exception:
        return default


def _is_local_url(url: str, app_url: str) -> bool:
    """Return True if the URL is the configured local app URL."""
    return bool(url and url.startswith(app_url))


def _action_element_identifier(action: dict) -> str:
    """Return a lowercase identifier string for the action's element, if any."""
    el = action.get("element") or {}
    attrs = el.get("attributes") or {}
    testid = attrs.get("data-testid", "")
    cls = attrs.get("class", "")
    text = (el.get("text") or "").lower()
    return f"{testid} {cls} {text}".strip().lower()


def _is_submit_click(action: dict) -> bool:
    if not isinstance(action, dict):
        return False
    name = next((k for k in action if k != "element"), None)
    if name != "click":
        return False
    ident = _action_element_identifier(action)
    return any(s in ident for s in ("submit", "btn-submit"))


def _is_search_result_click(action: dict) -> bool:
    if not isinstance(action, dict):
        return False
    name = next((k for k in action if k != "element"), None)
    if name != "click":
        return False
    ident = _action_element_identifier(action)
    return "search-result" in ident


def _is_recipient_input(action: dict) -> bool:
    """Return True if the action is an input into a recipient/account field."""
    if not isinstance(action, dict):
        return False
    name = next((k for k in action if k != "element"), None)
    if name not in ("input", "input_text"):
        return False
    el = action.get("element") or {}
    attrs = el.get("attributes") or {}
    ident = _action_element_identifier(action)
    return any(s in ident for s in ("recipient", "account", "payee", "to"))


def _normalize_action_order(actions: list) -> list:
    """
    Fix common non-deterministic action orderings.

    Example 1: the agent sometimes clicks the form submit button before clicking
    the recipient autocomplete suggestion. We swap those so the suggestion is
    selected before the submit.

    Example 2: the agent recorded the autocomplete selection at the end of the
    form fill (e.g. after amount/description). For replay we need it right
    after the recipient/account input that triggered the dropdown.
    """
    actions = list(actions)

    # 1. Swap adjacent submit → search-result clicks.
    for i in range(1, len(actions)):
        if _is_submit_click(actions[i - 1]) and _is_search_result_click(actions[i]):
            actions[i - 1], actions[i] = actions[i], actions[i - 1]

    # 2. Move any search-result click to immediately after the recipient input.
    for i, action in enumerate(actions):
        if not _is_search_result_click(action):
            continue
        for j in range(i - 1, -1, -1):
            if _is_recipient_input(actions[j]):
                # Move action from i to right after j.
                item = actions.pop(i)
                actions.insert(j + 1, item)
                break
        # Only fix the first one; restart if more exist.
        if _is_search_result_click(action):
            break

    return actions


def _extract_expected_texts(final_result: str, actions: list) -> list[str]:
    """Find verification strings in the agent's final answer and search actions."""
    texts = []
    seen = set()

    if final_result:
        # Quoted strings in the final result are often exact text the agent verified.
        # (Backticked inline code is usually credentials/commands, not UI text.)
        for quote in re.findall(r'"([^"]{3,})"', final_result):
            if quote not in seen:
                seen.add(quote)
                texts.append(quote)

        # Bold values like **$14,900.00** are strong verification targets.
        # Skip "label: value" combinations because they don't match a single element.
        for bold in re.findall(r"\*\*([^*]{2,})\*\*", final_result):
            if ":" in bold:
                continue
            if bold not in seen and (bold.startswith("$") or any(ch.isdigit() for ch in bold)):
                seen.add(bold)
                texts.append(bold)

        # Dollar amounts anywhere in the summary (e.g. $14,900.00) are good verification targets.
        for amount in re.findall(r"\$\d[\d,]*(?:\.\d+)?", final_result):
            if amount not in seen:
                seen.add(amount)
                texts.append(amount)

        # Markdown bullet values like "- **Total Balance:** $14,900.00 ..."
        # capture the first value token so we can assert on amounts/state.
        for value in re.findall(r"-\s*\*\*[^*]+(?::\*\*|\*\*:)\s*(\S+)", final_result):
            cleaned = value.strip(",.;:!?)")
            if cleaned and cleaned not in seen and (cleaned.startswith("$") or cleaned.replace(",", "").replace(".", "").isdigit()):
                seen.add(cleaned)
                texts.append(cleaned)

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = next((k for k in action if k != "element"), None)
        params = action.get(action_name, {}) if action_name else {}
        if action_name in ("search_page", "find_text", "verify_text") and "pattern" in params:
            pattern = params["pattern"]
            if not pattern or pattern in seen:
                continue
            # Ignore generic alphabetic-only search keywords; keep exact phrases, numbers, amounts.
            if pattern.isalpha():
                continue
            seen.add(pattern)
            texts.append(pattern)

    return texts


# ---------------------------------------------------------------------------
# Action → Playwright code mappings
# ---------------------------------------------------------------------------

def _best_locator(element: dict | None, index: int | None, action_type: str = "fill") -> str:
    """
    Build the most robust Playwright locator from element metadata.

    Priority (most stable first):
      1.  #id                  - explicit unique attribute
      2.  data-testid          - get_by_test_id()   (React/Vue/Angular convention)
      3.  aria-label           - get_by_label()
      4.  placeholder          - get_by_placeholder()  (fill actions only)
      5.  name attribute       - locator('[name=...]')
      6.  role + visible text  - get_by_role(..., name=...)
      7.  title attribute      - get_by_title()
      8.  other data-* attrs   - locator('[data-x=...]')
      9.  visible text         - get_by_text()  (click actions only)
     10.  tag + class          - locator('tag.class')  (skip hashed class names)
     11.  css_selector         - locator(css)    (structural, fragile)
     12.  xpath                - locator('xpath=...')  (last resort)
     13.  data-index           - synthetic runtime attr, only valid during recording
    """
    # Tags that carry an implicit ARIA role usable with get_by_role()
    tag_to_role = {
        "button": "button", "a": "link", "select": "combobox",
        "textarea": "textbox",
        "h1": "heading", "h2": "heading", "h3": "heading",
        "h4": "heading", "h5": "heading", "h6": "heading",
    }
    input_type_to_role = {
        "checkbox": "checkbox", "radio": "radio",
        "button": "button", "submit": "button", "reset": "button",
    }

    if element:
        # 1. id
        el_id = element.get("id")
        if el_id:
            return f'page.locator("#{el_id}")'

        attrs = element.get("attributes") or {}

        # 2. data-testid
        testid = attrs.get("data-testid")
        if testid:
            escaped = testid.replace('"', '\\"')
            return f'page.get_by_test_id("{escaped}")'

        # 3. aria-label
        aria_label = attrs.get("aria-label")
        if aria_label:
            escaped = aria_label.replace('"', '\\"')
            return f'page.get_by_label("{escaped}")'

        # 4. placeholder (fill/type only)
        placeholder = attrs.get("placeholder")
        if placeholder and action_type in ("fill",):
            escaped = placeholder.replace('"', '\\"').replace("'", "\\'")
            return f'page.get_by_placeholder("{escaped}")'

        # 5. name attribute
        name = attrs.get("name")
        if name:
            escaped = name.replace('"', '\\"')
            return f'page.locator("[name=\\"{escaped}\\"]")'

        # 6. role + accessible name
        text_val = (element.get("text") or element.get("label") or "").strip()[:40]
        role = attrs.get("role")
        if not role:
            tag_lower = (element.get("tag_name") or "").lower()
            role = tag_to_role.get(tag_lower)
            if tag_lower == "input":
                role = input_type_to_role.get(
                    attrs.get("type", "text").lower(), "textbox"
                )
        if role and text_val:
            escaped_text = text_val.replace('"', '\\"')
            return f'page.get_by_role("{role}", name="{escaped_text}")'

        # 7. title attribute
        title = attrs.get("title")
        if title:
            escaped = title.replace('"', '\\"')
            return f'page.get_by_title("{escaped}")'

        # 8. other data-* attributes
        for attr, val in attrs.items():
            if attr.startswith("data-") and attr not in ("data-index", "data-testid") and val:
                escaped_val = val.replace('"', '\\"')
                return f'page.locator("[{attr}=\\"{escaped_val}\\"]")'

        # 9. visible text for click actions
        if text_val and action_type == "click":
            escaped = text_val.replace('"', '\\"')
            return f'page.get_by_text("{escaped}")'

        # 10. tag + first CSS class (skip hashed/generated names like sc-abc123, css-1x2y)
        css_class = attrs.get("class")
        tag = element.get("tag_name", "")
        if css_class and tag:
            first_class = css_class.split()[0]
            looks_hashed = (any(c.isdigit() for c in first_class)
                            or first_class.startswith(("sc-", "css-", "_")))
            if not looks_hashed:
                escaped = first_class.replace('"', '\\"')
                return f'page.locator("{tag}.{escaped}")'

        # 11. css_selector - structural, breaks on DOM changes
        css = element.get("css_selector")
        if css and css not in ("body", ""):
            escaped = css.replace('"', '\\"')
            return f'page.locator("{escaped}")'

        # 12. xpath - last resort, absolute paths from html/body are very fragile
        xpath = element.get("xpath")
        if xpath:
            escaped = xpath.replace('"', '\\"')
            return f'page.locator("xpath={escaped}")'

    # 13. runtime data-index - only valid during the recording browser session
    if index is not None:
        return f'page.locator("[data-index=\'{index}\']")'

    return 'page.locator("unknown")'


def _action_to_playwright(action: dict) -> str | None:
    """
    Convert a single browser-use action dict to a Playwright Python statement.

    Uses element metadata (id, xpath, css, placeholder, aria-label, name)
    for robust selectors; falls back to data-index only as last resort.

    browser-use 0.12.x action shapes:
      {"navigate": {"url": "http://..."}}
      {"input":    {"index": 3, "text": "hello", "clear": true}, "element": {...}}
      {"click":    {"index": 5}, "element": {...}}
      {"select_dropdown": {"index": 2, "text": "male"}, "element": {...}}
      {"scroll":   {"down": true, "pages": 2.0}}
      {"done":     {"text": "...", "success": true}}
    """
    if not action:
        return None

    # element metadata is stored at the top level alongside the action key
    element = action.get("element")

    action_name = next((k for k in action if k not in ("element",)), None)
    params = action.get(action_name, {}) if action_name else {}

    if action_name in ("navigate", "go_to_url"):
        url = params.get("url", "")
        return f'    page.goto("{url}")'

    if action_name in ("input", "input_text"):
        text = params.get("text", "")
        index = params.get("index")
        escaped = text.replace('"', '\\"')
        locator = _best_locator(element, index, action_type="fill")
        comment = f"  # index={index}" if index is not None else ""
        return f'    {locator}.fill("{escaped}"){comment}'

    if action_name in ("click", "click_element", "click_element_by_index"):
        index = params.get("index")
        locator = _best_locator(element, index, action_type="click")
        comment = f"  # index={index}" if index is not None else ""
        return f'    {locator}.click(){comment}'

    if action_name in ("select_dropdown", "select_dropdown_option"):
        index = params.get("index")
        text = params.get("text", "")
        escaped = text.replace('"', '\\"')
        locator = _best_locator(element, index, action_type="select")
        comment = f"  # index={index}" if index is not None else ""
        return f'    {locator}.select_option(label="{escaped}"){comment}'

    if action_name == "scroll":
        if params.get("down", True):
            px = int(params.get("pages", 1) * 800)
            return f"    page.evaluate('window.scrollBy(0, {px})')"
        else:
            px = int(params.get("pages", 1) * 800)
            return f"    page.evaluate('window.scrollBy(0, -{px})')"

    if action_name == "scroll_down":
        amount = params.get("amount", 500)
        return f"    page.evaluate('window.scrollBy(0, {amount})')"

    if action_name == "scroll_up":
        amount = params.get("amount", 500)
        return f"    page.evaluate('window.scrollBy(0, -{amount})')"

    if action_name == "wait":
        ms = int(params.get("seconds", 1) * 1000)
        return f"    page.wait_for_timeout({ms})"

    if action_name == "evaluate":
        code = params.get("code", "")
        if not code:
            return None
        return f"    page.evaluate({json.dumps(code)})"

    if action_name in ("done", "search_page", "find_text", "verify_text"):
        return None

    return f"    # [UNHANDLED action: {action_name}] params={params}"


# ---------------------------------------------------------------------------
# Script template
# ---------------------------------------------------------------------------

SCRIPT_TEMPLATE = '''\
"""
{test_name}

Auto-generated by script_generator.py from trace: {trace_file}
Scenario: {scenario}

DO NOT EDIT the action sequence manually.
To re-record: python agent_recorder.py --task "..." --output {scenario}
To regenerate: python script_generator.py -i {trace_file} -o {output_file}

Data is injected from tests/test_data.py — edit that file to change inputs.
"""

import pytest
from playwright.sync_api import Page, expect


def test_{func_name}({signature}):
    """
    {docstring}

    Recorded task:
        {task}
    """

    # ---- Recorded action sequence (generated) ----
{action_lines}
    # ---- Assertions ----
{assertion_lines}
'''


def _build_assertions(final_result: str, scenario_id: str, actions: list) -> str:
    """
    Build verification assertions from the agent's final result and any
    explicit search_page/find_text actions. Falls back to generic stubs
    when no verification text can be extracted.
    """
    texts = _extract_expected_texts(final_result, actions)

    lines = ["    # Verification assertions derived from the agent's final result"]
    seen_assertions: set[str] = set()

    # Field-specific assertions from search/find actions that captured the matched element.
    for action in actions:
        if not isinstance(action, dict):
            continue
        name = next((k for k in action if k != "element"), None)
        if name not in ("search_page", "find_text", "verify_text"):
            continue
        params = action.get(name, {})
        pattern = params.get("pattern", "")
        if not pattern:
            continue
        element = action.get("element")
        if element:
            locator = _best_locator(element, None, action_type="click")
            key = f"{locator}:{pattern}"
            if key in seen_assertions:
                continue
            seen_assertions.add(key)
            escaped_pattern = pattern.replace('"', '\\"')
            lines.append(f'    expect({locator}).to_contain_text("{escaped_pattern}")')

    # General assertions from the final result text/amounts.
    for text in texts:
        if text in seen_assertions:
            continue
        key = text
        if key in seen_assertions:
            continue
        seen_assertions.add(key)
        escaped = text.replace('"', '\\"')
        lines.append(f'    expect(page.get_by_text("{escaped}").first).to_be_visible()')

    if len(lines) == 1:
        lines.extend([
            "    # TODO: add scenario-specific assertions here.",
            "    # Example: expect(page.locator('#result')).to_be_visible()",
            "    pass",
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def _resolve_trace_path(trace_path: Path) -> Path:
    """Return an existing trace file path, tolerating a duplicate .json extension."""
    if trace_path.exists():
        return trace_path

    # Tolerate .json.json created by older /save behaviour
    duplicate_json = trace_path.with_suffix(trace_path.suffix + ".json")
    if duplicate_json.exists():
        return duplicate_json

    if trace_path.suffix == ".json":
        no_extension = trace_path.with_suffix("")
        if no_extension.exists():
            return no_extension

    raise FileNotFoundError(
        f"Trace file not found: {trace_path} (also tried {duplicate_json})"
    )


def generate_script(
    trace_path: Path,
    output_path: Path,
    test_name: str | None = None,
) -> None:
    """Read a trace JSON and write a Playwright pytest file from recorded actions."""
    trace_path = _resolve_trace_path(trace_path)
    with open(trace_path, encoding="utf-8") as f:
        payload = json.load(f)

    scenario = payload.get("scenario", "unnamed")
    task = payload.get("task", "")
    final_result = payload.get("final_result", "")
    actions = _normalize_action_order(payload.get("actions", []))

    func_name = re.sub(r"[^a-zA-Z0-9_]", "_", scenario).lower().strip("_")
    if not func_name:
        func_name = "generated"
    test_name = test_name or f"test_{func_name}"

    action_lines = []
    skipped = 0
    for action in actions:
        line = _action_to_playwright(action)
        if line is not None:
            action_lines.append(line)
        else:
            skipped += 1
    if skipped:
        print(f"[generator] Skipped {skipped} unhandled/done actions")

    action_block = "\n".join(action_lines) if action_lines else "    pass  # no actions recorded"
    assertion_lines = _build_assertions(final_result, scenario, actions)
    docstring = f"Auto-generated test for scenario: {scenario}"

    app_url = _load_app_url()
    first_url = ""
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = next((k for k in action if k != "element"), None)
        if action_name in ("navigate", "go_to_url"):
            first_url = action.get(action_name, {}).get("url", "")
            break
    signature = "page: Page, app_url: str" if _is_local_url(first_url, app_url) else "page: Page"

    script = SCRIPT_TEMPLATE.format(
        test_name=test_name,
        trace_file=str(trace_path),
        scenario=scenario,
        output_file=str(output_path),
        func_name=func_name,
        docstring=docstring,
        signature=signature,
        task=textwrap.fill(task[:200], width=80, subsequent_indent="        "),
        action_lines=action_block,
        assertion_lines=assertion_lines,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    init_file = output_path.parent / "__init__.py"
    if not init_file.exists():
        init_file.touch()

    output_path.write_text(script, encoding="utf-8")
    print(f"[generator] Wrote trace-driven test ({len(action_lines)} actions) to: {output_path}")
    print(f"[generator] Run with: pytest {output_path} -v")


def main():
    parser = argparse.ArgumentParser(
        description="Convert a recorded browser-use trace JSON into a Playwright pytest script"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the input trace JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Path to write the generated pytest .py file",
    )
    parser.add_argument(
        "--name", "-n",
        default=None,
        help="Test function name (default: derived from scenario name)",
    )
    args = parser.parse_args()

    generate_script(
        trace_path=Path(args.input),
        output_path=Path(args.output),
        test_name=args.name,
    )


if __name__ == "__main__":
    main()

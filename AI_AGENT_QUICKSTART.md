# AI Agent Quickstart

Get the AI browser agent (`agent/recorder.py` + `agent_recorder.py`) running in a few minutes.

---

## 1. Prerequisites

- Python 3.10+
- A DeepSeek API key
- The target web app running if your task navigates to it, e.g. the loan calculator at `http://localhost:5001`

## 2. Configure the API key

Edit `.env` in the project root and set your key:

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

> If the model name in `.env` is `deepseek-v4-pro` and it fails, change it to `deepseek-chat`.

## 3. Create a virtual environment and install everything

From the project root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
playwright install --with-deps chromium
```

On Windows:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
playwright install chromium
```

> **Why two commands?** `pip` installs Python packages only. `playwright install` downloads the actual Chromium browser binary and, on Linux with `--with-deps`, the system libraries it needs. They cannot be merged into a single `pip install` command, but you can run them sequentially in one shell session.

## 5. Run the AI agent

```bash
python agent_recorder.py --task "Navigate to https://vb-bank-demo.vercel.app/transfer, log in with john.doe and user123, Transfer 100 dollars TO Jane Smith with account number: 2345678901. The transfer should be successful, John's account should be 100 dollars less." --output bank_transfer
```

The browser opens visibly by default so you can watch the agent. For unattended runs add `--headless`:

```bash
python agent_recorder.py \
  --task "Navigate to http://localhost:5001 and verify the loan form" \
  --output my_scenario \
  --headless
```

Expected output:

```text
[recorder] Saved 42 actions to: generated_scripts/standard_30_360_calculation.json
```

## 6. Generate a Playwright test from the recording (optional)

```bash
python script_generator.py \
  -i generated_scripts/standard_30_360_calculation.json \
  -o tests/generated/test_standard_30_360_calculation.py \
  -n test_standard_30_360_calculation
```

Run the generated test:

```bash
pytest tests/generated/test_standard_30_360_calculation.py -v
```

The loan calculator app must still be running on the URL used in your task.

## 7. Adjust timing if the target app is slow

These values are in `.env`:

```bash
AGENT_MIN_PAGE_LOAD_S=6.0
AGENT_NETWORK_IDLE_S=3.0
TIMEOUT_NavigateToUrlEvent=60
TIMEOUT_BrowserStateRequestEvent=60
```

Increase them if pages load slowly.

## Common issues

- **`ModuleNotFoundError: No module named 'agent'`** — run commands from the project root, not inside `agent/`.
- **`DEEPSEEK_API_KEY` missing** — export it or set it in `.env`.
- **Browser not found** — run `playwright install chromium` again.
- **Target app not responding** — start it first: `python loan_calculator_app/app.py`.
- **Model error** — switch `DEEPSEEK_MODEL` to `deepseek-chat`.

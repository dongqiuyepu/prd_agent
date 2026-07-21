# PRD Agent — AI-Powered UI Test Automation

An AI agent that uses a browser (Playwright) as a tool to record user interactions and generate deterministic Playwright test scripts. The agent can chat with you, perform browser tasks on demand, and produce replayable tests — no manual scripting required.

## Prerequisites

- Python 3.11+
- A DeepSeek API key (or compatible OpenAI-style LLM)

## Setup

```bash
# Clone the repo
git clone https://github.com/dongqiuyepu/prd_agent.git
cd prd_agent

# Create a virtual environment and install dependencies
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Playwright browsers
playwright install

# Configure your LLM API key
cp .env.example .env   # or create .env manually
```

Add to your `.env`:

```env
DEEPSEEK_API_KEY=your_api_key_here
```

## Usage

There are two workflows: **one-shot recording** and **interactive chat**.

### 1. One-Shot Recording

Record a test scenario in a single command. The agent opens a browser, performs the task, and saves a JSON trace.

```bash
python agent_recorder.py \
    --task "Go to https://example.com, log in as user/pass, click Dashboard" \
    --output my_scenario
```

Options:

- `--task`, `-t` — Natural language description of the test scenario (required)
- `--output`, `-o` — Base name for the output trace file (required, no extension)
- `--headless` — Run the browser in headless mode (default: visible)

Output: `generated_scripts/my_scenario.json`

### 2. Interactive Chat

Chat with the agent interactively. Each message becomes a browser task; general questions are answered directly without opening a browser.

```bash
python agent_chat.py [--headless] [--output NAME]
```

Chat commands:

- `/help` — Show available commands
- `/save [name]` — Save the recorded trace to `generated_scripts/<name>.json`
- `/generate <trace> [output]` — Generate a Playwright test from a saved trace
- `/quit` — Exit the chat (you will be prompted to save)

### 3. Generate a Playwright Test Script

Convert a recorded trace JSON into a deterministic Playwright pytest script.

```bash
python script_generator.py \
    -i generated_scripts/my_scenario.json \
    -o tests/generated/test_my_scenario.py
```

Options:

- `-i`, `--input` — Path to the trace JSON file (required)
- `-o`, `--output` — Path for the generated test file (required)
- `-n`, `--name` — Custom test function name (optional)

### 4. Run the Generated Test

```bash
pytest tests/generated/test_my_scenario.py -v
```

## End-to-End Example

```bash
# Step 1: Record
python agent_recorder.py \
    --task "Go to https://vb-bank-demo.vercel.app, log in as john.doe / user123, \
            transfer \$100 to Jane Smith (account 2345678901), \
            then verify on the dashboard that the balance is updated." \
    --output bank_transfer

# Step 2: Generate test
python script_generator.py \
    -i generated_scripts/bank_transfer.json \
    -o tests/generated/test_bank_transfer.py

# Step 3: Run test
pytest tests/generated/test_bank_transfer.py -v
```

## Project Structure

```text
prd_agent/
├── agent/
│   ├── recorder.py        # Agent creation, action recording, trace extraction
│   └── chat_session.py    # Interactive chat session with routing LLM
├── agent_recorder.py      # CLI entry point for one-shot recording
├── agent_chat.py          # CLI entry point for interactive chat
├── script_generator.py    # Trace JSON → Playwright pytest script
├── tests/
│   ├── conftest.py        # Pytest fixtures
│   ├── test_data.py       # Test data for parameterized tests
│   └── generated/         # Auto-generated Playwright test scripts
├── generated_scripts/     # Recorded trace JSON files (gitignored)
├── requirements.txt
└── pyproject.toml
```

## How It Works

1. **Record** — The AI agent (powered by browser-use + DeepSeek) performs a task in a real browser. Every action (click, fill, navigate, scroll) is captured with element metadata (test IDs, XPath, attributes).

2. **Generate** — `script_generator.py` reads the trace and produces a Playwright pytest script with robust locators (prioritizing `data-testid`, `aria-label`, `placeholder`, etc.) and verification assertions derived from the agent's final result.

3. **Replay** — The generated test runs with pure Playwright — no LLM or AI needed at runtime. It is deterministic and can be run in CI.

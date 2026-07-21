"""
agent_recorder.py — Phase 1: Record a test scenario using the AI agent.

Usage:
    python agent_recorder.py --task "TASK DESCRIPTION" --output SCENARIO_NAME [--headless]

Example:
    python agent_recorder.py \\
        --task "Navigate to http://localhost:5000, fill the loan form with
                principal=1000000, rate=3.6%, term=12 months, select 30/360 method,
                fill all required personal/employment/financial fields with realistic data,
                click submit, and verify the monthly interest shown is around 3000" \\
        --output standard_30_360_calculation

This will:
  1. Open a browser (visible by default so you can watch)
  2. The AI agent performs the task step by step
  3. Every action is recorded
  4. Saves the trace to: generated_scripts/standard_30_360_calculation.json

Then run:
    python script_generator.py -i generated_scripts/standard_30_360_calculation.json \\
                                -o tests/generated/test_standard_30_360.py
"""

import argparse
import asyncio
from agent.recorder import record_scenario


def main():
    parser = argparse.ArgumentParser(description="Record a UI test scenario with the AI agent")
    parser.add_argument(
        "--task", "-t",
        required=True,
        help="Natural language description of the test scenario to record",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Base name for the output trace JSON (no extension, e.g. 'standard_30_360')",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run browser in headless mode (default: visible so you can watch the agent)",
    )
    args = parser.parse_args()

    trace_path = asyncio.run(
        record_scenario(
            task=args.task,
            output_name=args.output,
            headless=args.headless,
        )
    )
    print(f"\nDone. Trace saved to: {trace_path}")
    print(f"Next step: python script_generator.py -i {trace_path} -o tests/generated/test_{args.output}.py")


if __name__ == "__main__":
    main()

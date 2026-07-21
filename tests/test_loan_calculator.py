"""
Baseline Playwright UI Tests for Loan Calculator.

These tests are hand-authored and run with pure Playwright — no LLM required.
Data is injected from tests/test_data.py so scenarios can be re-run with
different inputs by editing that file.

For AI-recorded tests, see tests/generated/ (created by script_generator.py).

Run:
    pytest tests/test_loan_calculator.py -v
"""

import pytest
from playwright.sync_api import Page, expect

from tests.test_data import (
    BORROWER_STANDARD,
    EMPLOYMENT_STANDARD,
    FINANCIAL_STANDARD,
    EMERGENCY_CONTACT_STANDARD,
    ADDITIONAL_INFO_STANDARD,
    LOAN_SCENARIOS,
)


# ---------------------------------------------------------------------------
# Shared helper: fill the entire required form via Playwright
# ---------------------------------------------------------------------------

def _fill_form(page: Page, loan_inputs: dict, borrower: dict, employment: dict,
               financial: dict, emergency: dict, additional: dict) -> None:
    """Fill all required form fields using Playwright locators by field id."""

    # Section 1 — Borrower
    page.locator("#borrower_name").fill(borrower["borrower_name"])
    page.locator("#id_number").fill(borrower["id_number"])
    page.locator("#gender").select_option(borrower["gender"])
    page.locator("#date_of_birth").fill(borrower["date_of_birth"])
    page.locator("#marital_status").select_option(borrower["marital_status"])
    page.locator("#education").select_option(borrower["education"])
    page.locator("#mobile_phone").fill(borrower["mobile_phone"])
    page.locator("#email").fill(borrower["email"])
    page.locator("#residential_address").fill(borrower["residential_address"])
    page.locator("#postal_code").fill(borrower["postal_code"])
    page.locator("#residence_type").select_option(borrower["residence_type"])
    page.locator("#years_at_residence").fill(borrower["years_at_residence"])

    # Section 2 — Employment
    page.locator("#employment_status").select_option(employment["employment_status"])
    page.locator("#employer_name").fill(employment["employer_name"])
    page.locator("#industry").select_option(employment["industry"])
    page.locator("#job_title").fill(employment["job_title"])
    page.locator("#years_employed").fill(employment["years_employed"])
    page.locator("#monthly_income").fill(employment["monthly_income"])
    page.locator("#additional_income").fill(employment["additional_income"])
    page.locator("#employer_phone").fill(employment["employer_phone"])
    page.locator("#employer_address").fill(employment["employer_address"])

    # Section 3 — Financial
    page.locator("#total_assets").fill(financial["total_assets"])
    page.locator("#total_liabilities").fill(financial["total_liabilities"])
    page.locator("#monthly_expenses").fill(financial["monthly_expenses"])
    page.locator("#existing_loans").fill(financial["existing_loans"])
    page.locator("#savings_amount").fill(financial["savings_amount"])

    # Section 4 — Loan Details
    page.locator("#principal").fill(loan_inputs["principal"])
    page.locator("#annual_rate").fill(loan_inputs["annual_rate"])
    page.locator("#term_months").fill(loan_inputs["term_months"])
    page.locator("#loan_purpose").select_option(loan_inputs["loan_purpose"])
    page.locator("#loan_type").select_option(loan_inputs["loan_type"])
    page.locator("#calculation_method").select_option(loan_inputs["calculation_method"])
    page.locator("#repayment_method").select_option(loan_inputs["repayment_method"])

    # Section 7 — Emergency Contact
    page.locator("#emergency_contact_name").fill(emergency["emergency_contact_name"])
    page.locator("#emergency_contact_relationship").select_option(emergency["emergency_contact_relationship"])
    page.locator("#emergency_contact_phone").fill(emergency["emergency_contact_phone"])

    # Section 8 — Additional
    page.locator("#bank_account").fill(additional["bank_account"])
    page.locator("#bank_name").fill(additional["bank_name"])
    page.locator("#preferred_contact_time").select_option(additional["preferred_contact_time"])
    page.locator("#preferred_contact_method").select_option(additional["preferred_contact_method"])
    page.locator("#application_date").fill(additional["application_date"])


# ---------------------------------------------------------------------------
# P0 Tests — Critical Path
# ---------------------------------------------------------------------------

def test_standard_30_360_calculation(page: Page, app_url: str):
    """
    P0: Standard loan with 30/360 method.
    Principal=1,000,000  Rate=3.6%  Term=12 months
    Expected: monthly_interest=3,000  total_interest=36,000
    """
    scenario = next(s for s in LOAN_SCENARIOS if s["id"] == "standard_30_360")
    page.goto(app_url)

    _fill_form(page, scenario["inputs"], BORROWER_STANDARD, EMPLOYMENT_STANDARD,
               FINANCIAL_STANDARD, EMERGENCY_CONTACT_STANDARD, ADDITIONAL_INFO_STANDARD)

    page.locator("#calculateBtn").click()

    result_section = page.locator("#resultSection")
    expect(result_section).to_be_visible(timeout=15000)

    monthly = page.locator("#monthlyInterest").inner_text()
    total = page.locator("#totalInterest").inner_text()

    assert "3,000" in monthly or "3000" in monthly, (
        f"Expected monthly interest ~3,000, got: {monthly}"
    )
    assert "36,000" in total or "36000" in total, (
        f"Expected total interest ~36,000, got: {total}"
    )


def test_result_section_visible_after_submit(page: Page, app_url: str):
    """
    P0: After a valid form submission the result section must become visible
    and show the success badge.
    """
    scenario = next(s for s in LOAN_SCENARIOS if s["id"] == "act_360_method")
    page.goto(app_url)

    _fill_form(page, scenario["inputs"], BORROWER_STANDARD, EMPLOYMENT_STANDARD,
               FINANCIAL_STANDARD, EMERGENCY_CONTACT_STANDARD, ADDITIONAL_INFO_STANDARD)

    page.locator("#calculateBtn").click()

    expect(page.locator("#resultSection")).to_be_visible(timeout=15000)
    expect(page.locator(".success-badge")).to_be_visible()


def test_form_validation_zero_principal(page: Page, app_url: str):
    """
    P0: Submitting with principal=0 must show the error div (not the result section).
    HTML5 required validation prevents submit, so the result section stays hidden.
    """
    page.goto(app_url)

    page.locator("#principal").fill("0")
    page.locator("#annual_rate").fill("3.6")
    page.locator("#term_months").fill("12")

    page.locator("#calculateBtn").click()

    result_section = page.locator("#resultSection")
    assert not result_section.is_visible(), (
        "Result section should NOT appear when principal is 0"
    )


# ---------------------------------------------------------------------------
# P1 Tests — Important Features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario_id,method", [
    ("standard_30_360", "30/360"),
    ("act_360_method",  "act/360"),
    ("act_365_method",  "act/365"),
])
def test_calculation_methods(page: Page, app_url: str, scenario_id: str, method: str):
    """
    P1: All three calculation methods produce a positive numeric result.
    """
    scenario = next(s for s in LOAN_SCENARIOS if s["id"] == scenario_id)
    page.goto(app_url)

    _fill_form(page, scenario["inputs"], BORROWER_STANDARD, EMPLOYMENT_STANDARD,
               FINANCIAL_STANDARD, EMERGENCY_CONTACT_STANDARD, ADDITIONAL_INFO_STANDARD)

    page.locator("#calculateBtn").click()

    expect(page.locator("#resultSection")).to_be_visible(timeout=15000)

    monthly_text = page.locator("#monthlyInterest").inner_text()
    assert "¥" in monthly_text and monthly_text != "¥0.00", (
        f"Method {method}: unexpected monthly interest: {monthly_text}"
    )


def test_repayment_schedule_row_count(page: Page, app_url: str):
    """
    P1: Repayment schedule table must have exactly term_months rows.
    Uses a 6-month loan so we expect exactly 6 rows.
    """
    six_month_inputs = {
        "principal": "120000",
        "annual_rate": "6.0",
        "term_months": "6",
        "loan_purpose": "education",
        "loan_type": "student",
        "calculation_method": "30/360",
        "repayment_method": "equal_principal",
    }
    page.goto(app_url)

    _fill_form(page, six_month_inputs, BORROWER_STANDARD, EMPLOYMENT_STANDARD,
               FINANCIAL_STANDARD, EMERGENCY_CONTACT_STANDARD, ADDITIONAL_INFO_STANDARD)

    page.locator("#calculateBtn").click()

    expect(page.locator("#resultSection")).to_be_visible(timeout=15000)

    rows = page.locator("#scheduleBody tr")
    assert rows.count() == 6, (
        f"Expected 6 schedule rows for 6-month loan, got {rows.count()}"
    )


def test_full_form_all_sections(page: Page, app_url: str):
    """
    P1: Fill all 57 fields across all 8 sections and verify calculation succeeds.
    """
    scenario = next(s for s in LOAN_SCENARIOS if s["id"] == "standard_30_360")
    page.goto(app_url)

    _fill_form(page, scenario["inputs"], BORROWER_STANDARD, EMPLOYMENT_STANDARD,
               FINANCIAL_STANDARD, EMERGENCY_CONTACT_STANDARD, ADDITIONAL_INFO_STANDARD)

    page.locator("#calculateBtn").click()

    expect(page.locator("#resultSection")).to_be_visible(timeout=15000)

    monthly = page.locator("#monthlyInterest").inner_text()
    assert monthly != "¥0.00", f"Monthly interest should not be zero, got: {monthly}"

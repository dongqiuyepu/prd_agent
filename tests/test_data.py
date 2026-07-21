"""
Test data for loan calculator UI automation tests.
Provides structured test inputs and expected outputs.
"""

APP_URL = "http://localhost:5001"

BORROWER_STANDARD = {
    "borrower_name": "张三",
    "id_number": "110101199001011234",
    "gender": "male",
    "date_of_birth": "1990-01-01",
    "marital_status": "married",
    "education": "bachelor",
    "mobile_phone": "13800138000",
    "email": "zhangsan@example.com",
    "residential_address": "北京市朝阳区建国路88号",
    "postal_code": "100000",
    "residence_type": "owned",
    "years_at_residence": "5",
}

EMPLOYMENT_STANDARD = {
    "employment_status": "employed",
    "employer_name": "北京科技有限公司",
    "industry": "it",
    "job_title": "软件工程师",
    "years_employed": "3",
    "monthly_income": "15000",
    "additional_income": "5000",
    "employer_phone": "01012345678",
    "employer_address": "北京市海淀区中关村大街1号",
}

FINANCIAL_STANDARD = {
    "total_assets": "1000000",
    "total_liabilities": "200000",
    "monthly_expenses": "8000",
    "existing_loans": "0",
    "credit_card_limit": "50000",
    "savings_amount": "300000",
    "investment_amount": "100000",
}

EMERGENCY_CONTACT_STANDARD = {
    "emergency_contact_name": "李四",
    "emergency_contact_relationship": "family",
    "emergency_contact_phone": "13900139000",
}

ADDITIONAL_INFO_STANDARD = {
    "bank_account": "6222021234567890123",
    "bank_name": "中国工商银行",
    "preferred_contact_time": "morning",
    "preferred_contact_method": "phone",
    "application_date": "2026-07-13",
}

LOAN_SCENARIOS = [
    {
        "id": "standard_30_360_calculation",
        "description": "Standard loan: 30/360 method — expect ¥3,000/month interest (recorded scenario)",
        "inputs": {
            "principal": "1000000",
            "annual_rate": "3.6",
            "term_months": "12",
            "loan_purpose": "home_purchase",
            "loan_type": "mortgage",
            "calculation_method": "30/360",
            "repayment_method": "interest_only",
        },
        "expected": {
            "monthly_interest": 3000.00,
            "total_interest": 36000.00,
            "total_amount": 1036000.00,
        },
    },
    {
        "id": "standard_30_360",
        "description": "Standard loan: 30/360 method — expect ¥3,000/month interest",
        "inputs": {
            "principal": "1000000",
            "annual_rate": "3.6",
            "term_months": "12",
            "loan_purpose": "home_purchase",
            "loan_type": "mortgage",
            "calculation_method": "30/360",
            "repayment_method": "equal_principal",
        },
        "expected": {
            "monthly_interest": 3000.00,
            "total_interest": 36000.00,
            "total_amount": 1036000.00,
        },
    },
    {
        "id": "act_360_method",
        "description": "act/360 method — verify different result from 30/360",
        "inputs": {
            "principal": "1000000",
            "annual_rate": "3.6",
            "term_months": "12",
            "loan_purpose": "personal",
            "loan_type": "personal",
            "calculation_method": "act/360",
            "repayment_method": "equal_principal",
        },
        "expected": {
            "monthly_interest": 3000.00,
        },
    },
    {
        "id": "act_365_method",
        "description": "act/365 method — foreign currency loan calculation",
        "inputs": {
            "principal": "1000000",
            "annual_rate": "3.6",
            "term_months": "12",
            "loan_purpose": "business",
            "loan_type": "business",
            "calculation_method": "act/365",
            "repayment_method": "equal_principal",
        },
        "expected": {
            "monthly_interest": 2958.90,
        },
    },
    {
        "id": "validation_zero_principal",
        "description": "Validation: zero principal should show error",
        "inputs": {
            "principal": "0",
            "annual_rate": "3.6",
            "term_months": "12",
            "loan_purpose": "personal",
            "loan_type": "personal",
            "calculation_method": "30/360",
            "repayment_method": "equal_principal",
        },
        "expected": {
            "error": True,
        },
    },
]

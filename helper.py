import pandas as pd
import os
import json
import ollama
from datetime import datetime

MONTHS_LIST = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

def get_current_month_name():
    return MONTHS_LIST[datetime.now().month - 1]

def load_data(filepath, columns):
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            return pd.read_csv(filepath)
        except pd.errors.EmptyDataError:
            pass
    return pd.DataFrame(columns=columns)

def get_prompt(current_month_name, cards_str):
    extraction_prompt = f"""
        You are a financial data extraction AI. Extract the user's input into a strictly formatted JSON object.
        Current month: {current_month_name}
        Available accounts/cards: {cards_str}

        Return ONLY valid JSON using the following template. Omit any keys that do not apply based on the "type".

        {{
        "type": "expense | income | investment | transfer",
        "amount": 0.00,
        "name": "Name of item, source, or asset",
        "month": "Extracted month, otherwise use {current_month_name}",
        "card": "Exact match from available accounts (Use for expense/income)",
        "from_card": "Exact match from available accounts (Use ONLY for transfer)",
        "to_card": "Exact match from available accounts (Use ONLY for transfer)",
        "investment_mode": "revaluation | contribution (Use ONLY for investment)",
        "rule_bucket": "Needs | Wants | Investments (Use ONLY for expense)"
        }}
        """
    return extraction_prompt

def process_financial_input(nl_input, current_month_name, cards_str):
    extraction_prompt = get_prompt(current_month_name, cards_str)
    response = ollama.chat(model='llama3', messages=[
        {'role': 'system', 'content': extraction_prompt},
        {'role': 'user', 'content': nl_input}
    ], format='json')
    
    extracted_data = json.loads(response['message']['content'])
    if 'amount' in extracted_data:
        try:
            extracted_data['amount'] = round(float(extracted_data['amount']), 2)
        except (ValueError, TypeError):
            pass
    return extracted_data

def format_ai_response(platform, record_type, amount, name, card_used=None, bucket=None, f_card=None, t_card=None, total_val=None, added_val=None):
    if platform == "telegram":
        if record_type == "expense":
            return f"**Expense Logged**\nItem: {name}\nAmount: {amount}zł\nAccount: {card_used}\nCategory: {bucket}"
        elif record_type == "income":
            return f"**Income Logged**\nSource: {name}\nAmount: {amount}zł\nAccount: {card_used}"
        elif record_type == "transfer":
            return f"**Transfer Executed**\nAmount: {amount}zł\nFrom: {f_card}\nTo: {t_card}"
        elif record_type == "investment":
            return f"**Investment Updated**\nAsset: {name}\nNew Total: {total_val}zł\nChange: {added_val:+.2f}zł"
    elif platform == "flask":
        if record_type == "expense":
            return f"Expense logged: {name} — {amount}zł on {card_used} ({bucket})"
        elif record_type == "income":
            return f"Income logged: {name} — {amount}zł to {card_used}"
        elif record_type == "transfer":
            return f"Transfer executed: {amount}zł from {f_card} to {t_card}"
        elif record_type == "investment":
            return f"Investment updated: {name} — Total {total_val}zł (change: {added_val:+.2f}zł)"
    return "Action logged successfully."

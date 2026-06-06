import pandas as pd
import os
import json
import ollama
from datetime import datetime

MONTHS_LIST = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
VALID_TYPES   = ("expense", "income", "investment", "transfer")
VALID_BUCKETS = ("Needs", "Wants", "Investments")
_BUCKET_NORM  = {b.lower(): b for b in VALID_BUCKETS}

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
    return f"""You are a financial data extraction AI. Your only job is to read a short natural-language entry and return a single JSON object.

Current month: {current_month_name}
Available accounts: {cards_str}

━━━ TYPE SELECTION RULES (read carefully) ━━━
• "income"   — money arriving FROM OUTSIDE (salary, client payment, gift, refund, bonus, "I got paid", "I received", "I earned").
               Use "card" for the destination account.
• "transfer" — money moved BETWEEN two of YOUR OWN listed accounts only.
               Use "from_card" (source) and "to_card" (destination).
               NEVER use this type if only one account is mentioned or money comes from outside.
• "expense"  — money you spent or paid out. Use "card" and always include "rule_bucket".
• "investment" — adding capital to an investment asset or updating its value.

━━━ FIELD RULES ━━━
• amount     : positive number (no currency symbols)
• name       : short label for the item, source, or asset
• month      : full month name (e.g. "June"); default to {current_month_name} if not stated
• card       : must exactly match one of: {cards_str}  (required for expense and income)
• from_card  : exactly one of the listed accounts  (ONLY for transfer)
• to_card    : exactly one of the listed accounts  (ONLY for transfer)
• rule_bucket: MUST be exactly one of "Needs", "Wants", or "Investments" (ONLY for expense)
               — Needs: rent, food, utilities, transport, insurance, medicine
               — Wants: restaurants, entertainment, clothes, subscriptions, hobbies
               — Investments: stocks, ETF, crypto, savings deposits

━━━ EXAMPLES ━━━
User: "Got my 6000 salary on PKO"
{{"type":"income","amount":6000,"name":"Salary","month":"{current_month_name}","card":"PKO"}}

User: "Spent 120 on groceries with Millennium card"
{{"type":"expense","amount":120,"name":"Groceries","month":"{current_month_name}","card":"Millennium","rule_bucket":"Needs"}}

User: "Transfer 500 from PKO to Revolut"
{{"type":"transfer","amount":500,"name":"Transfer","month":"{current_month_name}","from_card":"PKO","to_card":"Revolut"}}

User: "Bought 300zł of VOO ETF"
{{"type":"investment","amount":300,"name":"VOO ETF","month":"{current_month_name}","card":"PKO"}}

━━━ OUTPUT ━━━
Return ONLY the JSON object, nothing else. No markdown, no explanation."""

def validate_and_fix(extracted: dict, card_names: list, current_month: str) -> dict:
    """Normalize and fill safe defaults for any missing or invalid LLM output fields."""
    result = {}

    # type
    rtype = str(extracted.get("type") or "").strip().lower()
    result["type"] = rtype if rtype in VALID_TYPES else "expense"

    # amount
    try:
        amount = float(extracted.get("amount") or 0)
        result["amount"] = round(amount, 2) if amount > 0 else 0.0
    except (TypeError, ValueError):
        result["amount"] = 0.0

    # name
    name = str(extracted.get("name") or "").strip()
    result["name"] = name or "Unknown"

    # month
    month = str(extracted.get("month") or "").strip()
    result["month"] = month if month in MONTHS_LIST else current_month

    first_card = card_names[0] if card_names else ""
    last_card  = card_names[-1] if card_names else ""

    def _resolve_card(raw):
        c = str(raw or "").strip()
        return c if c in card_names else first_card

    if result["type"] == "expense":
        result["card"]        = _resolve_card(extracted.get("card"))
        raw_bucket            = str(extracted.get("rule_bucket") or "").strip()
        result["rule_bucket"] = _BUCKET_NORM.get(raw_bucket.lower(), "Wants")

    elif result["type"] == "income":
        result["card"] = _resolve_card(extracted.get("card"))

    elif result["type"] == "transfer":
        f = _resolve_card(extracted.get("from_card"))
        t = _resolve_card(extracted.get("to_card"))
        # prevent from == to when multiple accounts exist
        if f == t and len(card_names) > 1:
            t = next((c for c in card_names if c != f), last_card)
        result["from_card"] = f
        result["to_card"]   = t

    elif result["type"] == "investment":
        result["card"] = _resolve_card(extracted.get("card"))

    return result

def process_financial_input(nl_input, current_month_name, cards_str):
    extraction_prompt = get_prompt(current_month_name, cards_str)
    response = ollama.chat(model='qwen2.5:1.5b', messages=[
        {'role': 'system', 'content': extraction_prompt},
        {'role': 'user', 'content': nl_input}
    ], format='json')

    raw = json.loads(response['message']['content'])
    card_names = [c.strip() for c in cards_str.split(",") if c.strip()]
    return validate_and_fix(raw, card_names, current_month_name)

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

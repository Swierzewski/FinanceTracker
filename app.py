import helper
import pandas as pd
import plotly.express as px

import yaml
import os
import json
import uuid
import csv
import io
import numpy as np
import calendar
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR        = "data"
EXPENSES_FILE   = f"{DATA_DIR}/expenses.csv"
INVEST_FILE     = f"{DATA_DIR}/investments.csv"
PORTFOLIO_FILE  = f"{DATA_DIR}/portfolio.csv"
INCOME_FILE     = f"{DATA_DIR}/income.csv"
TRANSFERS_FILE  = f"{DATA_DIR}/transfers.csv"
CHECKLIST_FILE  = f"{DATA_DIR}/checklist.json"
PPK_FILE        = f"{DATA_DIR}/ppk.csv"   # Strictly isolated — never merged into portfolio totals
YAML_FILE       = "budget.yaml"
UNDO_BACKUP_CSV    = f"{DATA_DIR}/.undo_backup.csv"
UNDO_BACKUP_TARGET = f"{DATA_DIR}/.undo_target.txt"

os.makedirs(DATA_DIR, exist_ok=True)

MONTHS_LIST = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December",
]

if not os.path.exists(YAML_FILE):
    default_config = {
        "base_balances": {
            "Main Debit (mBank)": 5000.0,
            "Credit Card (Millennium)": -1500.0,
            "Cash": 300.0,
            "Revolut": 0.0,
        },
        "rules": {"needs": 50, "wants": 30, "investments": 20},
        "mandatory_expenses": [
            {"name": "Rent",     "amount": 1800.0},
            {"name": "Internet", "amount": 60.0},
        ],
    }
    with open(YAML_FILE, "w") as f:
        yaml.dump(default_config, f)

with open(YAML_FILE, "r") as f:
    config = yaml.safe_load(f)

card_names = list(config["base_balances"].keys())

# ── Data helpers ──────────────────────────────────────────────────────────────

def load_data(filepath, columns):
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            return pd.read_csv(filepath)
        except pd.errors.EmptyDataError:
            pass
    return pd.DataFrame(columns=columns)

def load_all():
    return (
        load_data(EXPENSES_FILE,  ["Date","Month","Item","Amount","Category","Rule_Bucket","Card"]),
        load_data(INVEST_FILE,    ["Date","Asset","Added_Value","Total_Value"]),
        load_data(INCOME_FILE,    ["Date","Month","Source","Amount","Card"]),
        load_data(TRANSFERS_FILE, ["Date","Month","From_Card","To_Card","Amount"]),
    )

ASSET_TYPES = ["Stocks", "ETF", "Crypto", "Bonds", "Cash", "Real Estate", "Other"]

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE) and os.path.getsize(PORTFOLIO_FILE) > 0:
        try:
            df = pd.read_csv(PORTFOLIO_FILE)
            if "asset_type" not in df.columns:
                df.insert(1, "asset_type", "Other")
            return df
        except pd.errors.EmptyDataError:
            pass
    return pd.DataFrame(columns=["id","asset_name","asset_type","amount_invested","current_value","last_updated"])

def _save_undo_backup(df, target_file):
    df.to_csv(UNDO_BACKUP_CSV, index=False)
    with open(UNDO_BACKUP_TARGET, "w") as f:
        f.write(target_file)

def load_ppk():
    if os.path.exists(PPK_FILE) and os.path.getsize(PPK_FILE) > 0:
        try:
            return pd.read_csv(PPK_FILE)
        except pd.errors.EmptyDataError:
            pass
    return pd.DataFrame(columns=[
        "id","date","employee_contribution","employer_contribution",
        "state_bonus","current_account_value",
    ])

# ── Checklist helpers ─────────────────────────────────────────────────────────

def _ordinal_suffix(n):
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

def load_checklist():
    today          = datetime.now()
    current_period = f"{today.year}-{today.month:02d}"
    if os.path.exists(CHECKLIST_FILE):
        with open(CHECKLIST_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {"last_reset": current_period, "items": []}
    # Auto-reset is_paid on first load of a new month
    if data.get("last_reset") != current_period:
        for item in data["items"]:
            item["is_paid_this_month"] = False
        data["last_reset"] = current_period
        _save_checklist(data)
    return data

def _save_checklist(data):
    with open(CHECKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_checklist_notifications(items):
    today      = datetime.now()
    max_day    = calendar.monthrange(today.year, today.month)[1]
    notifications = []
    for item in items:
        if item["is_paid_this_month"]:
            continue
        due_day  = min(item["due_day"], max_day)
        due_date = datetime(today.year, today.month, due_day)
        delta    = (due_date.date() - today.date()).days
        suffix   = _ordinal_suffix(due_day)
        if delta == 7:
            notifications.append({
                "type":    "warning",
                "message": f"'{item['name']}' is due in 7 days (on the {due_day}{suffix}).",
            })
        elif delta == 1:
            notifications.append({
                "type":    "danger",
                "message": f"'{item['name']}' is due TOMORROW (on the {due_day}{suffix}).",
            })
        elif delta < 0:
            notifications.append({
                "type":    "danger",
                "message": f"'{item['name']}' is OVERDUE (was due on the {due_day}{suffix}).",
            })
    return notifications

def _match_checklist(expense_name):
    """Mark checklist items paid when a logged expense name matches."""
    data = load_checklist()
    changed = False
    for item in data["items"]:
        if not item["is_paid_this_month"]:
            if item["name"].lower() in expense_name.lower():
                item["is_paid_this_month"] = True
                changed = True
    if changed:
        _save_checklist(data)

app.jinja_env.filters["ordinal"] = _ordinal_suffix

# ── Chart builders ────────────────────────────────────────────────────────────

_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Roboto, sans-serif", color="#4B5563"),
    margin=dict(t=40, b=20, l=20, r=20),
)

def fig_to_html(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})

def get_trend_data(expenses_df, income_df):
    labels, income_vals, expense_vals, invest_vals = [], [], [], []
    for m in MONTHS_LIST:
        m_inc = round(income_df[income_df["Month"] == m]["Amount"].sum()   if not income_df.empty   else 0, 2)
        m_exp = round(expenses_df[expenses_df["Month"] == m]["Amount"].sum() if not expenses_df.empty else 0, 2)
        m_inv = round(
            expenses_df[(expenses_df["Month"] == m) & (expenses_df["Rule_Bucket"] == "Investments")]["Amount"].sum()
            if not expenses_df.empty else 0, 2
        )
        if m_inc > 0 or m_exp > 0 or m_inv > 0:
            labels.append(m[:3])
            income_vals.append(m_inc)
            expense_vals.append(m_exp)
            invest_vals.append(m_inv)
    if not labels:
        return None
    return {"labels": labels, "income": income_vals, "expenses": expense_vals, "investments": invest_vals}

def get_analytics_data(expenses_df, income_df):
    agg = (
        expenses_df[expenses_df["Item"].str.contains(r"Total|Current", na=False, case=False)].copy()
        if not expenses_df.empty else pd.DataFrame()
    )
    monthly_exp = (
        agg.groupby("Month")["Amount"].sum().reindex(MONTHS_LIST).dropna()
        if not agg.empty else pd.Series(dtype=float)
    )
    rule_monthly = (
        expenses_df.groupby(["Month","Rule_Bucket"])["Amount"].sum()
        .unstack(fill_value=0).reindex(MONTHS_LIST).dropna(how="all")
        if not expenses_df.empty else pd.DataFrame()
    )
    monthly_inc = (
        income_df.groupby("Month")["Amount"].sum().reindex(MONTHS_LIST).dropna()
        if not income_df.empty else pd.Series(dtype=float)
    )

    months = [m for m in MONTHS_LIST if m in monthly_exp.index or m in monthly_inc.index]
    if not months:
        return None

    monthly_exp = monthly_exp.reindex(months, fill_value=0)
    monthly_inc = monthly_inc.reindex(months, fill_value=0)
    savings = monthly_inc - monthly_exp
    cumul   = savings.cumsum()
    mom     = monthly_exp.pct_change() * 100
    labels  = [m[:3] for m in months]

    mom_labels = [labels[i] for i, v in enumerate(mom.values) if not np.isnan(v)]
    mom_values = [round(float(v), 2) for v in mom.values if not np.isnan(v)]

    needs_pct = wants_pct = invest_pct = [0.0] * len(months)
    if not rule_monthly.empty:
        rm  = rule_monthly.reindex(months, fill_value=0)
        tot = rm.sum(axis=1).replace(0, np.nan)
        pct = rm.div(tot, axis=0).fillna(0) * 100
        if "Needs"       in pct.columns: needs_pct  = [round(float(v), 1) for v in pct["Needs"].values]
        if "Wants"       in pct.columns: wants_pct  = [round(float(v), 1) for v in pct["Wants"].values]
        if "Investments" in pct.columns: invest_pct = [round(float(v), 1) for v in pct["Investments"].values]

    return {
        "labels":     labels,
        "expenses":   [round(float(v), 2) for v in monthly_exp.values],
        "income":     [round(float(v), 2) for v in monthly_inc.values],
        "cumulative": [round(float(v), 2) for v in cumul.values],
        "mom_labels": mom_labels,
        "mom_values": mom_values,
        "needs":      needs_pct,
        "wants":      wants_pct,
        "invest":     invest_pct,
    }

# ── Main route ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    expenses_df, invest_df, income_df, transfers_df = load_all()
    current_month = MONTHS_LIST[datetime.now().month - 1]

    # Card balances
    card_balances = {}
    for c in card_names:
        base  = config["base_balances"].get(c, 0.0)
        inc   = income_df[income_df["Card"]  == c]["Amount"].sum() if not income_df.empty   else 0
        exp   = expenses_df[expenses_df["Card"] == c]["Amount"].sum() if not expenses_df.empty else 0
        t_out = transfers_df[transfers_df["From_Card"] == c]["Amount"].sum() if not transfers_df.empty else 0
        t_in  = transfers_df[transfers_df["To_Card"]   == c]["Amount"].sum() if not transfers_df.empty else 0
        card_balances[c] = base + inc - exp - t_out + t_in

    total_liquid_cash = sum(card_balances.values())

    portfolio_df      = load_portfolio()
    portfolio_invested = float(portfolio_df["amount_invested"].sum()) if not portfolio_df.empty else 0.0
    portfolio_current  = float(portfolio_df["current_value"].sum())   if not portfolio_df.empty else 0.0
    portfolio_pnl      = round(portfolio_current - portfolio_invested, 2)
    portfolio_rows     = portfolio_df.to_dict("records") if not portfolio_df.empty else []

    portfolio_by_type = {}
    for row in portfolio_rows:
        t = (row.get("asset_type") or "Other")
        if t not in portfolio_by_type:
            portfolio_by_type[t] = {"invested": 0.0, "current": 0.0}
        portfolio_by_type[t]["invested"] += float(row.get("amount_invested", 0))
        portfolio_by_type[t]["current"]  += float(row.get("current_value",  0))

    total_invested    = portfolio_current

    # ── PPK (isolated — included in net worth but never in portfolio totals) ───
    ppk_df = load_ppk()
    if not ppk_df.empty:
        ppk_df = ppk_df.sort_values("date").reset_index(drop=True)
        ppk_total_employee       = float(ppk_df["employee_contribution"].sum())
        ppk_total_employer_state = float(
            (ppk_df["employer_contribution"] + ppk_df["state_bonus"]).sum()
        )
        ppk_current_value        = float(ppk_df.iloc[-1]["current_account_value"])
        ppk_rows                 = ppk_df.to_dict("records")
        ppk_chart_data           = {
            "labels":        ppk_df["date"].tolist(),
            "employee":      ppk_df["employee_contribution"].round(2).tolist(),
            "employer_state": (
                ppk_df["employer_contribution"] + ppk_df["state_bonus"]
            ).round(2).tolist(),
        }
    else:
        ppk_total_employee = ppk_total_employer_state = ppk_current_value = 0.0
        ppk_rows = []
        ppk_chart_data = None
    ppk_total_contributions = ppk_total_employee + ppk_total_employer_state
    ppk_profit = ppk_current_value - ppk_total_contributions

    # PPK account value counts toward net worth but not toward the investment portfolio
    total_net_worth = total_liquid_cash + portfolio_current + ppk_current_value

    # Charts
    trend_data       = get_trend_data(expenses_df, income_df)
    analytics_data   = get_analytics_data(expenses_df, income_df)

    invest_chart = None
    if not invest_df.empty:
        fig = px.line(invest_df, x="Date", y="Total_Value", color="Asset",
                      markers=True, title="Portfolio Growth Curve")
        fig.update_layout(**_LAYOUT, height=380)
        invest_chart = fig_to_html(fig)

    invest_rows = invest_df.to_dict("records") if not invest_df.empty else []

    checklist_data          = load_checklist()
    checklist_items         = sorted(checklist_data["items"], key=lambda x: x["due_day"])
    checklist_notifications = get_checklist_notifications(checklist_items)

    return render_template(
        "index.html",
        card_balances           = card_balances,
        total_liquid_cash       = total_liquid_cash,
        total_invested          = total_invested,
        total_net_worth         = total_net_worth,
        months_list             = MONTHS_LIST,
        current_month           = current_month,
        trend_data              = trend_data,
        analytics_data          = analytics_data,
        invest_chart            = invest_chart,
        invest_rows             = invest_rows,
        portfolio_invested      = portfolio_invested,
        portfolio_current       = portfolio_current,
        portfolio_pnl           = portfolio_pnl,
        portfolio_rows          = portfolio_rows,
        checklist_items              = checklist_items,
        checklist_notifications      = checklist_notifications,
        ppk_total_employee           = ppk_total_employee,
        ppk_total_employer_state     = ppk_total_employer_state,
        ppk_total_contributions      = ppk_total_contributions,
        ppk_current_value            = ppk_current_value,
        ppk_profit                   = ppk_profit,
        ppk_rows                     = ppk_rows,
        ppk_chart_data               = ppk_chart_data,
        portfolio_by_type            = portfolio_by_type,
        asset_types                  = ASSET_TYPES,
    )

# ── API: monthly data ─────────────────────────────────────────────────────────

@app.route("/api/monthly")
def api_monthly():
    month = request.args.get("month", MONTHS_LIST[datetime.now().month - 1])
    expenses_df, _, income_df, _ = load_all()

    m_exp_df = expenses_df[expenses_df["Month"] == month] if not expenses_df.empty else pd.DataFrame()
    m_inc_df = income_df[income_df["Month"]     == month] if not income_df.empty   else pd.DataFrame()

    m_expenses = float(m_exp_df["Amount"].sum()) if not m_exp_df.empty else 0.0
    m_income   = float(m_inc_df["Amount"].sum()) if not m_inc_df.empty else 0.0

    rule_totals  = m_exp_df.groupby("Rule_Bucket")["Amount"].sum().to_dict() if not m_exp_df.empty else {}
    needs_spent  = rule_totals.get("Needs",       0)
    wants_spent  = rule_totals.get("Wants",       0)
    inv_spent    = rule_totals.get("Investments", 0)

    def safe_pct(spent, income):
        return f"{(spent / income) * 100:.1f}%" if income > 0 else "0.0%"

    exp_rows = (
        m_exp_df.reset_index().rename(columns={"index": "_row_idx"}).to_dict("records")
        if not m_exp_df.empty else []
    )
    inc_rows = m_inc_df.to_dict("records") if not m_inc_df.empty else []

    return jsonify(
        income      = m_income,
        expenses    = m_expenses,
        needs       = needs_spent,
        wants       = wants_spent,
        invest      = inv_spent,
        needs_pct   = safe_pct(needs_spent, m_income),
        wants_pct   = safe_pct(wants_spent, m_income),
        invest_pct  = safe_pct(inv_spent,   m_income),
        exp_rows    = exp_rows,
        inc_rows    = inc_rows,
    )

# ── API: update expense bucket ────────────────────────────────────────────────

RULE_BUCKETS = ("Needs", "Wants", "Investments")

@app.route("/api/expense/<int:row_idx>/bucket", methods=["POST"])
def api_update_expense_bucket(row_idx):
    data   = request.get_json(force=True)
    bucket = (data.get("bucket") or "").strip()
    if bucket not in RULE_BUCKETS:
        return jsonify(success=False, error="Invalid bucket value.")
    expenses_df = load_data(
        EXPENSES_FILE,
        ["Date","Month","Item","Amount","Category","Rule_Bucket","Card"],
    )
    if row_idx < 0 or row_idx >= len(expenses_df):
        return jsonify(success=False, error="Row index out of range.")
    expenses_df.at[row_idx, "Rule_Bucket"] = bucket
    expenses_df.to_csv(EXPENSES_FILE, index=False)
    return jsonify(success=True)

# ── API: AI entry ─────────────────────────────────────────────────────────────

@app.route("/api/ai_entry", methods=["POST"])
def api_ai_entry():
    data     = request.get_json(force=True)
    nl_input = (data.get("text") or "").strip()
    if not nl_input:
        return jsonify(success=False, error="No input provided.")

    expenses_df, _, income_df, transfers_df = load_all()
    current_month = MONTHS_LIST[datetime.now().month - 1]
    cards_str     = ", ".join(card_names)
    date_str      = datetime.now().strftime("%Y-%m-%d")

    try:
        extracted = helper.process_financial_input(nl_input, current_month, cards_str)
    except Exception as e:
        return jsonify(success=False, error=f"AI parsing error: {e}")

    rtype  = extracted.get("type")
    amount = float(extracted.get("amount", 0.0))
    name   = extracted.get("name", "Unknown")
    month  = extracted.get("month", current_month)

    try:
        if rtype == "expense":
            card_used = extracted.get("card", card_names[0])
            bucket    = extracted.get("rule_bucket", "Wants")
            backup    = expenses_df.copy()
            new_row   = pd.DataFrame([{"Date": date_str, "Month": month, "Item": name,
                                        "Amount": amount, "Category": "General",
                                        "Rule_Bucket": bucket, "Card": card_used}])
            pd.concat([expenses_df, new_row], ignore_index=True).to_csv(EXPENSES_FILE, index=False)
            _save_undo_backup(backup, EXPENSES_FILE)
            _match_checklist(name)
            msg = helper.format_ai_response("flask","expense",amount,name,card_used=card_used,bucket=bucket)

        elif rtype == "income":
            card_used = extracted.get("card", card_names[0])
            backup    = income_df.copy()
            new_row   = pd.DataFrame([{"Date": date_str, "Month": month,
                                        "Source": name, "Amount": amount, "Card": card_used}])
            pd.concat([income_df, new_row], ignore_index=True).to_csv(INCOME_FILE, index=False)
            _save_undo_backup(backup, INCOME_FILE)
            msg = helper.format_ai_response("flask","income",amount,name,card_used=card_used)

        elif rtype == "transfer":
            f_card = extracted.get("from_card", card_names[0])
            t_card = extracted.get("to_card",   card_names[-1])
            backup = transfers_df.copy()
            new_row = pd.DataFrame([{"Date": date_str, "Month": month,
                                     "From_Card": f_card, "To_Card": t_card, "Amount": amount}])
            pd.concat([transfers_df, new_row], ignore_index=True).to_csv(TRANSFERS_FILE, index=False)
            _save_undo_backup(backup, TRANSFERS_FILE)
            msg = helper.format_ai_response("flask","transfer",amount,name,f_card=f_card,t_card=t_card)

        elif rtype == "investment":
            card_used = extracted.get("card", card_names[0])
            backup    = expenses_df.copy()
            new_row   = pd.DataFrame([{"Date": date_str, "Month": month, "Item": name,
                                        "Amount": amount, "Category": "Investment",
                                        "Rule_Bucket": "Investments", "Card": card_used}])
            pd.concat([expenses_df, new_row], ignore_index=True).to_csv(EXPENSES_FILE, index=False)
            _save_undo_backup(backup, EXPENSES_FILE)
            _match_checklist(name)
            msg = helper.format_ai_response("flask","expense",amount,name,
                                            card_used=card_used, bucket="Investments")
        else:
            return jsonify(success=False, error=f"Unrecognised record type: {rtype!r}")

    except Exception as e:
        return jsonify(success=False, error=f"Data write error: {e}")

    return jsonify(success=True, message=msg)

# ── API: undo ─────────────────────────────────────────────────────────────────

_UNDO_ALLOWED = {EXPENSES_FILE, INCOME_FILE, TRANSFERS_FILE}

@app.route("/api/undo", methods=["POST"])
def api_undo():
    if not os.path.exists(UNDO_BACKUP_TARGET) or not os.path.exists(UNDO_BACKUP_CSV):
        return jsonify(success=False, error="Nothing to undo.")
    try:
        with open(UNDO_BACKUP_TARGET) as f:
            target_file = f.read().strip()
        if target_file not in _UNDO_ALLOWED:
            return jsonify(success=False, error="Invalid undo target.")
        df = pd.read_csv(UNDO_BACKUP_CSV)
        df.to_csv(target_file, index=False)
        os.remove(UNDO_BACKUP_CSV)
        os.remove(UNDO_BACKUP_TARGET)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

# ── API: investments (portfolio) ─────────────────────────────────────────────

@app.route("/api/investments/add", methods=["POST"])
def api_investments_add():
    data = request.get_json(force=True)
    asset_name = (data.get("asset_name") or "").strip()
    if not asset_name:
        return jsonify(success=False, error="Asset name is required.")
    try:
        amount_invested = float(data.get("amount_invested", 0))
        raw_current     = data.get("current_value")
        current_value   = float(raw_current) if raw_current not in (None, "") else amount_invested
    except (TypeError, ValueError):
        return jsonify(success=False, error="Invalid numeric value.")
    if amount_invested <= 0:
        return jsonify(success=False, error="Amount invested must be positive.")

    asset_type = (data.get("asset_type") or "Other").strip()
    if asset_type not in ASSET_TYPES:
        asset_type = "Other"

    portfolio_df = load_portfolio()
    new_row = pd.DataFrame([{
        "id":              str(uuid.uuid4()),
        "asset_name":      asset_name,
        "asset_type":      asset_type,
        "amount_invested": round(amount_invested, 2),
        "current_value":   round(current_value,   2),
        "last_updated":    datetime.now().strftime("%Y-%m-%d %H:%M"),
    }])
    pd.concat([portfolio_df, new_row], ignore_index=True).to_csv(PORTFOLIO_FILE, index=False)
    return jsonify(success=True)

@app.route("/api/investments/update", methods=["POST"])
def api_investments_update():
    data = request.get_json(force=True)
    asset_id = (data.get("id") or "").strip()
    try:
        current_value = float(data.get("current_value", 0))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Invalid numeric value.")
    if current_value < 0:
        return jsonify(success=False, error="Current value cannot be negative.")

    portfolio_df = load_portfolio()
    mask = portfolio_df["id"] == asset_id
    if not mask.any():
        return jsonify(success=False, error="Asset not found.")

    portfolio_df.loc[mask, "current_value"] = round(current_value, 2)
    portfolio_df.loc[mask, "last_updated"]   = datetime.now().strftime("%Y-%m-%d %H:%M")
    portfolio_df.to_csv(PORTFOLIO_FILE, index=False)
    return jsonify(success=True)

@app.route("/api/investments/delete/<asset_id>", methods=["POST"])
def api_investments_delete(asset_id):
    portfolio_df = load_portfolio()
    portfolio_df = portfolio_df[portfolio_df["id"] != asset_id]
    portfolio_df.to_csv(PORTFOLIO_FILE, index=False)
    return jsonify(success=True)

# ── API: PPK (isolated retirement fund — never affects portfolio totals) ──────

@app.route("/api/ppk/add", methods=["POST"])
def api_ppk_add():
    data = request.get_json(force=True)
    try:
        employee    = float(data.get("employee_contribution", 0) or 0)
        employer    = float(data.get("employer_contribution", 0) or 0)
        state_bonus = float(data.get("state_bonus",           0) or 0)
        raw_val     = data.get("current_account_value")
        current_val = float(raw_val) if raw_val not in (None, "") else 0.0
    except (TypeError, ValueError):
        return jsonify(success=False, error="Invalid numeric value.")

    if employee < 0 or employer < 0 or state_bonus < 0 or current_val < 0:
        return jsonify(success=False, error="Values cannot be negative.")
    if employee == 0 and employer == 0:
        return jsonify(success=False, error="At least one contribution amount is required.")

    entry_date = (data.get("date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    ppk_df = load_ppk()
    new_row = pd.DataFrame([{
        "id":                     str(uuid.uuid4()),
        "date":                   entry_date,
        "employee_contribution":  round(employee,    2),
        "employer_contribution":  round(employer,    2),
        "state_bonus":            round(state_bonus, 2),
        "current_account_value":  round(current_val, 2),
    }])
    pd.concat([ppk_df, new_row], ignore_index=True).to_csv(PPK_FILE, index=False)
    return jsonify(success=True)


@app.route("/api/ppk/delete/<entry_id>", methods=["POST"])
def api_ppk_delete(entry_id):
    ppk_df = load_ppk()
    ppk_df = ppk_df[ppk_df["id"] != entry_id]
    ppk_df.to_csv(PPK_FILE, index=False)
    return jsonify(success=True)


# ── API: checklist ────────────────────────────────────────────────────────────

@app.route("/api/checklist/add", methods=["POST"])
def api_checklist_add():
    data    = request.get_json(force=True)
    name    = (data.get("name") or "").strip()
    due_day = data.get("due_day")
    if not name:
        return jsonify(success=False, error="Name is required.")
    try:
        due_day = int(due_day)
        if not (1 <= due_day <= 31):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(success=False, error="Due day must be between 1 and 31.")
    checklist_data = load_checklist()
    checklist_data["items"].append({
        "id":                str(uuid.uuid4()),
        "name":              name,
        "due_day":           due_day,
        "is_paid_this_month": False,
    })
    _save_checklist(checklist_data)
    return jsonify(success=True)

@app.route("/api/checklist/delete/<item_id>", methods=["POST"])
def api_checklist_delete(item_id):
    checklist_data = load_checklist()
    checklist_data["items"] = [i for i in checklist_data["items"] if i["id"] != item_id]
    _save_checklist(checklist_data)
    return jsonify(success=True)

@app.route("/api/checklist/toggle/<item_id>", methods=["POST"])
def api_checklist_toggle(item_id):
    checklist_data = load_checklist()
    for item in checklist_data["items"]:
        if item["id"] == item_id:
            item["is_paid_this_month"] = not item["is_paid_this_month"]
            break
    _save_checklist(checklist_data)
    return jsonify(success=True)

# ── API: bulk import ──────────────────────────────────────────────────────────

@app.route("/api/bulk_import", methods=["POST"])
def api_bulk_import():
    data    = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    fmt     = (data.get("format")  or "auto").strip().lower()

    if not content:
        return jsonify(success=False, error="No content provided.")

    current_month = MONTHS_LIST[datetime.now().month - 1]
    cards_str     = ", ".join(card_names)
    today_str     = datetime.now().strftime("%Y-%m-%d")

    # ── Detect format and parse entries: list of (nl_text, date_str) ──────────
    entries = []

    if fmt == "auto":
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        first = lines[0] if lines else ""
        if "," in first and any(k in first.lower() for k in ("description","item","amount","name")):
            fmt = "csv"
        else:
            fmt = "text"

    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            desc   = (row.get("description") or row.get("item") or row.get("name") or "").strip()
            amount = (row.get("amount") or "").strip()
            card   = (row.get("card")   or "").strip()
            date   = (row.get("date")   or "").strip()
            parts  = [p for p in [desc, amount, card] if p]
            nl     = " ".join(parts)
            if nl:
                entries.append((nl, date or today_str))
    else:
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- ") or line.startswith("* "):
                line = line[2:].strip()
            if line:
                entries.append((line, today_str))

    if not entries:
        return jsonify(success=False, error="No valid entries found in content.")

    expenses_df, _, income_df, transfers_df = load_all()

    results, errors = [], []
    expenses_modified = income_modified = transfers_modified = False

    for nl_text, entry_date in entries:
        try:
            try:
                dt          = datetime.strptime(entry_date, "%Y-%m-%d")
                entry_month = MONTHS_LIST[dt.month - 1]
            except ValueError:
                entry_month = current_month
                entry_date  = today_str

            extracted = helper.process_financial_input(nl_text, entry_month, cards_str)

            rtype  = extracted.get("type")
            amount = float(extracted.get("amount", 0.0))
            name   = extracted.get("name", "Unknown")
            month  = extracted.get("month", entry_month)

            if rtype == "expense":
                card_used = extracted.get("card", card_names[0])
                bucket    = extracted.get("rule_bucket", "Wants")
                new_row   = pd.DataFrame([{"Date": entry_date, "Month": month, "Item": name,
                                           "Amount": amount, "Category": "General",
                                           "Rule_Bucket": bucket, "Card": card_used}])
                expenses_df = pd.concat([expenses_df, new_row], ignore_index=True)
                expenses_modified = True
                _match_checklist(name)
                results.append(f"Expense: {name} — {amount}zł ({bucket}) on {card_used}")

            elif rtype == "income":
                card_used = extracted.get("card", card_names[0])
                new_row   = pd.DataFrame([{"Date": entry_date, "Month": month,
                                           "Source": name, "Amount": amount, "Card": card_used}])
                income_df = pd.concat([income_df, new_row], ignore_index=True)
                income_modified = True
                results.append(f"Income: {name} — {amount}zł to {card_used}")

            elif rtype == "transfer":
                f_card  = extracted.get("from_card", card_names[0])
                t_card  = extracted.get("to_card",   card_names[-1])
                new_row = pd.DataFrame([{"Date": entry_date, "Month": month,
                                         "From_Card": f_card, "To_Card": t_card, "Amount": amount}])
                transfers_df = pd.concat([transfers_df, new_row], ignore_index=True)
                transfers_modified = True
                results.append(f"Transfer: {amount}zł from {f_card} to {t_card}")

            elif rtype == "investment":
                card_used = extracted.get("card", card_names[0])
                new_row   = pd.DataFrame([{"Date": entry_date, "Month": month, "Item": name,
                                           "Amount": amount, "Category": "Investment",
                                           "Rule_Bucket": "Investments", "Card": card_used}])
                expenses_df = pd.concat([expenses_df, new_row], ignore_index=True)
                expenses_modified = True
                results.append(f"Investment: {name} — {amount}zł on {card_used}")

            else:
                errors.append(f"Unknown type for: {nl_text[:50]!r}")

        except Exception as e:
            errors.append(f"Failed '{nl_text[:40]}': {e}")

    if results:
        try:
            if expenses_modified:
                expenses_df.to_csv(EXPENSES_FILE,   index=False)
            if income_modified:
                income_df.to_csv(INCOME_FILE,       index=False)
            if transfers_modified:
                transfers_df.to_csv(TRANSFERS_FILE, index=False)
        except Exception as e:
            return jsonify(success=False, error=f"Failed to save data: {e}")

    return jsonify(
        success  = True,
        imported = len(results),
        failed   = len(errors),
        results  = results,
        errors   = errors,
    )


# ── Monthly PDF report ────────────────────────────────────────────────────────

@app.route("/report/<month>")
def monthly_report(month):
    if month not in MONTHS_LIST:
        return "Invalid month", 404
    expenses_df, _, income_df, _ = load_all()

    m_exp_df = expenses_df[expenses_df["Month"] == month].copy() if not expenses_df.empty else pd.DataFrame()
    m_inc_df = income_df[income_df["Month"]     == month].copy() if not income_df.empty   else pd.DataFrame()

    # Ensure Amount is numeric so Jinja "%.2f" formatting never crashes on string values
    for df in (m_exp_df, m_inc_df):
        if not df.empty and "Amount" in df.columns:
            df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)

    m_income   = float(m_inc_df["Amount"].sum()) if not m_inc_df.empty else 0.0
    m_expenses = float(m_exp_df["Amount"].sum()) if not m_exp_df.empty else 0.0
    m_savings  = round(m_income - m_expenses, 2)

    rule_totals = m_exp_df.groupby("Rule_Bucket")["Amount"].sum().to_dict() if not m_exp_df.empty else {}

    portfolio_df   = load_portfolio()
    portfolio_rows = portfolio_df.to_dict("records") if not portfolio_df.empty else []

    return render_template(
        "report.html",
        month          = month,
        m_income       = round(m_income,   2),
        m_expenses     = round(m_expenses, 2),
        m_savings      = m_savings,
        needs          = round(rule_totals.get("Needs",       0), 2),
        wants          = round(rule_totals.get("Wants",       0), 2),
        invest         = round(rule_totals.get("Investments", 0), 2),
        exp_rows       = m_exp_df.to_dict("records") if not m_exp_df.empty else [],
        inc_rows       = m_inc_df.to_dict("records") if not m_inc_df.empty else [],
        portfolio_rows = portfolio_rows,
        generated_at   = datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

# ── Data editor page ──────────────────────────────────────────────────────────

_DATA_TABLES = {
    "expenses":  (EXPENSES_FILE,  ["Date","Month","Item","Amount","Category","Rule_Bucket","Card"]),
    "income":    (INCOME_FILE,    ["Date","Month","Source","Amount","Card"]),
    "transfers": (TRANSFERS_FILE, ["Date","Month","From_Card","To_Card","Amount"]),
}

@app.route("/data")
def data_page():
    return render_template("data.html", card_names=card_names, months_list=MONTHS_LIST)

@app.route("/api/data/<table>")
def api_data_get(table):
    if table not in _DATA_TABLES:
        return jsonify(success=False, error="Unknown table"), 404
    filepath, columns = _DATA_TABLES[table]
    df = load_data(filepath, columns)
    return jsonify(success=True, rows=df.to_dict("records"), columns=columns)

@app.route("/api/data/<table>/save", methods=["POST"])
def api_data_save(table):
    if table not in _DATA_TABLES:
        return jsonify(success=False, error="Unknown table"), 404
    filepath, columns = _DATA_TABLES[table]
    data = request.get_json(force=True)
    rows = data.get("rows")
    if rows is None:
        return jsonify(success=False, error="Missing 'rows' field.")
    try:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        df = df[columns]
        if "Amount" in df.columns and not df.empty:
            df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0).round(2)
        df.to_csv(filepath, index=False)
    except Exception as e:
        return jsonify(success=False, error=str(e))
    return jsonify(success=True, saved=len(df))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)

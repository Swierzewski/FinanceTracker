# FinanceTracker

A personal finance dashboard with AI-powered data entry via a web app and Telegram bot. Built with Flask, Plotly, and a local LLM (Ollama/Llama3).

---

## Overview

FinanceTracker lets you log expenses, income, transfers, and investments using plain-language text. An on-device LLM parses your input and categorises it automatically. All data lives in local CSV files — no cloud, no subscriptions.

You can interact through two interfaces:
- **Web dashboard** — full-featured UI with charts, analytics, and manual management
- **Telegram bot** — log entries from your phone with a quick text message

---

## Features

### Dashboard tabs

| Tab | Contents |
|-----|----------|
| Accounts & General | Live card balances, net worth, portfolio P&L, investment table, PPK (retirement fund) tracker, recurring expense checklist, AI entry box |
| Monthly Breakdown | Per-month income vs. expenses, 50/30/20 rule breakdown (Needs / Wants / Investments) |
| Trend Lines | Multi-month income/expense/investment bar chart, savings curve, month-over-month change, rule-bucket stacked chart |
| Asset Valuation | Investment portfolio table with P&L per asset, portfolio growth line chart |

### AI entry (web + Telegram)

Type a natural-language description and the LLM extracts the transaction details:

```
"Paid 120zł for groceries from PKO"
"Received 8500zł salary to PKO"
"Transferred 500zł from PKO to Revolut"
"Bought 200zł of ETF"
```

Supported record types: `expense`, `income`, `transfer`, `investment`.

Each entry can be **undone** with one click (web) or `/undo` (Telegram).

### Budget rules (50/30/20)

Every expense is tagged as **Needs**, **Wants**, or **Investments**. The dashboard shows how much of your income each bucket consumed, and compares it against the targets configured in `budget.yaml`.

### Recurring expense checklist

Define recurring bills (rent, internet, subscriptions) with a due day. The dashboard shows notifications:
- 7 days before the due date
- 1 day before
- When overdue

When you log an expense whose name matches a checklist item, it is automatically marked paid. The checklist resets itself on the first load of a new month.

### PPK (retirement fund) tracker

Log employee contributions, employer contributions, and state bonuses separately. PPK account value is included in your net worth but is isolated from the investment portfolio totals.

---

## Project Structure

```
FinanceTracker/
├── app.py              # Flask web application + REST API
├── helper.py           # Ollama LLM integration, shared data loaders
├── telegram_bot.py     # Telegram bot
├── budget.yaml         # Account balances, budget rules, mandatory expenses
├── requirements.txt
├── data/               # CSV data files (git-ignored)
│   ├── expenses.csv
│   ├── income.csv
│   ├── transfers.csv
│   ├── portfolio.csv
│   ├── ppk.csv
│   └── checklist.json
├── templates/
│   ├── base.html
│   └── index.html
└── static/
    ├── css/styles.css
    └── js/app.js
```

---

## Setup

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) running locally with the `llama3` model pulled:
  ```bash
  ollama pull llama3
  ```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure `budget.yaml`

Edit `budget.yaml` to set your account names, starting balances, and budget rule percentages:

```yaml
base_balances:
  Main Debit (PKO): 13787.07
  Revolut: 8132.73
  Cash: 1144.53

rules:
  needs: 50
  wants: 30
  investments: 20

mandatory_expenses:
  - name: Rent
    amount: 2133.0
  - name: Internet
    amount: 81.84
```

`base_balances` represents each account's balance at the point you started tracking. All subsequent income, expenses, and transfers are applied on top of these figures.

### Environment variables

Create a `.env` file (only required for the Telegram bot):

```
TELEGRAM_BOT_TOKEN=your_token_here
ALLOWED_USER_IDS=123456789
SECRET_KEY=your_flask_secret
```

---

## Running

### Web dashboard

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

### Telegram bot

```bash
python telegram_bot.py
```

Run both processes simultaneously to use both interfaces against the same data files.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main dashboard |
| GET | `/api/monthly?month=January` | Monthly income/expense breakdown |
| POST | `/api/ai_entry` | Log entry via natural language |
| POST | `/api/undo` | Undo the last AI entry |
| POST | `/api/expense/<row>/bucket` | Re-categorise an expense bucket |
| POST | `/api/investments/add` | Add a portfolio asset |
| POST | `/api/investments/update` | Update asset current value |
| POST | `/api/investments/delete/<id>` | Remove a portfolio asset |
| POST | `/api/ppk/add` | Add a PPK contribution record |
| POST | `/api/ppk/delete/<id>` | Remove a PPK record |
| POST | `/api/checklist/add` | Add a recurring expense |
| POST | `/api/checklist/delete/<id>` | Remove a recurring expense |
| POST | `/api/checklist/toggle/<id>` | Mark a recurring expense paid/unpaid |

---

## Tech Stack

| Layer | Library |
|-------|---------|
| Web framework | Flask |
| Data processing | pandas, NumPy |
| Charts | Plotly |
| LLM integration | Ollama (llama3) |
| Config | PyYAML |
| Telegram | pyTelegramBotAPI |

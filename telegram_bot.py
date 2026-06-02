import telebot
import pandas as pd
import yaml
import os
import ollama
import json
from datetime import datetime
import helper
from dotenv import load_dotenv



### Configuration and Init
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Error: TELEGRAM_BOT_TOKEN is not set in the environment variables.")

bot = telebot.TeleBot(BOT_TOKEN)
ALLOWED_USERS = [int(user_id) for user_id in os.getenv("ALLOWED_USER_IDS").split(",")] if os.getenv("ALLOWED_USER_IDS") else []

DATA_DIR = "data"
INCOME_FILE = f"{DATA_DIR}/income.csv"
EXPENSES_FILE = f"{DATA_DIR}/expenses.csv"
INVEST_FILE = f"{DATA_DIR}/investments.csv"
TRANSFERS_FILE = f"{DATA_DIR}/transfers.csv"
YAML_FILE = "budget.yaml"

# Needed for undo functionality: store last state before modification
user_backups = {}


### Helper Functions
with open(YAML_FILE, 'r') as f:
    config = yaml.safe_load(f)
    card_names = list(config['base_balances'].keys())

def is_authorized(message):
    return message.from_user.id in ALLOWED_USERS


### Main Commands
@bot.message_handler(func=lambda message: not is_authorized(message))
def handle_unauthorized(message):
    bot.reply_to(message, "Access Denied. You are not authorized.")

@bot.message_handler(commands=['start', 'help'], func=is_authorized)
def send_welcome(message):
    bot.reply_to(message, "Hi to your Finance Agent!\n\nJust text me your expenses, income, or transfers.\nIf I make a mistake, just type /undo to delete the last entry.")

@bot.message_handler(commands=['undo', 'redo'], func=is_authorized)
def undo_last_action(message):
    chat_id = message.chat.id
    if chat_id in user_backups and user_backups[chat_id]["file"] is not None:
        target_file = user_backups[chat_id]["file"]
        backup_df = user_backups[chat_id]["backup_df"]
        
        # Overwrite current CSV with the backup
        backup_df.to_csv(target_file, index=False)
        
        # Clear backup
        user_backups[chat_id]["file"] = None
        user_backups[chat_id]["backup_df"] = None
        
        bot.reply_to(message, "Action successfully undone! The entry has been deleted.")
    else:
        bot.reply_to(message, "Nothing to undo, or backup expired.")

@bot.message_handler(func=lambda message: is_authorized(message))
def process_financial_text(message):
    chat_id = message.chat.id
    nl_input = message.text
    
    bot.send_chat_action(chat_id, 'typing')
    
    current_month_name = helper.get_current_month_name()
    cards_str = ", ".join(card_names)
    
    try:
        extracted_data = helper.process_financial_input(nl_input, current_month_name, cards_str)
        date_str = datetime.now().strftime("%Y-%m-%d")
        record_type = extracted_data.get('type')
        amount = float(extracted_data.get('amount', 0.0))
        name = extracted_data.get('name', 'Unknown')
        month = extracted_data.get('month', current_month_name)
        
        # Determine files and load current state for Backup
        if record_type == "expense":
            df = helper.load_data(EXPENSES_FILE, ['Date', 'Month', 'Item', 'Amount', 'Category', 'Rule_Bucket', 'Card'])
            user_backups[chat_id] = {"file": EXPENSES_FILE, "backup_df": df.copy()}
            
            card_used = extracted_data.get('card', card_names[0])
            bucket = extracted_data.get('rule_bucket', 'Wants')
            new_row = pd.DataFrame([{'Date': date_str, 'Month': month, 'Item': name, 'Amount': amount, 'Category': 'General', 'Rule_Bucket': bucket, 'Card': card_used}])
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(EXPENSES_FILE, index=False)
            bot.reply_to(message, f"**Expense Logged**\nItem: {name}\nAmount: {amount}zł\nAccount: {card_used}\nCategory: {bucket}", parse_mode="Markdown")
            
        elif record_type == "income":
            df = helper.load_data(INCOME_FILE, ['Date', 'Month', 'Source', 'Amount', 'Card'])
            user_backups[chat_id] = {"file": INCOME_FILE, "backup_df": df.copy()}
            
            card_used = extracted_data.get('card', card_names[0])
            new_row = pd.DataFrame([{'Date': date_str, 'Month': month, 'Source': name, 'Amount': amount, 'Card': card_used}])
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(INCOME_FILE, index=False)
            bot.reply_to(message, f"**Income Logged**\nSource: {name}\nAmount: {amount}zł\nAccount: {card_used}", parse_mode="Markdown")
            
        elif record_type == "transfer":
            df = helper.load_data(TRANSFERS_FILE, ['Date', 'Month', 'From_Card', 'To_Card', 'Amount'])
            user_backups[chat_id] = {"file": TRANSFERS_FILE, "backup_df": df.copy()}
            
            f_card = extracted_data.get('from_card', card_names[0])
            t_card = extracted_data.get('to_card', card_names[-1])
            new_row = pd.DataFrame([{'Date': date_str, 'Month': month, 'From_Card': f_card, 'To_Card': t_card, 'Amount': amount}])
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(TRANSFERS_FILE, index=False)
            bot.reply_to(message, f"**Transfer Executed**\nAmount: {amount}zł\nFrom: {f_card}\nTo: {t_card}", parse_mode="Markdown")
            
        elif record_type == "investment":
            df = helper.load_data(INVEST_FILE, ['Date', 'Asset', 'Added_Value', 'Total_Value'])
            user_backups[chat_id] = {"file": INVEST_FILE, "backup_df": df.copy()}
            
            prev_total = 0.0
            if not df.empty:
                asset_history = df[df['Asset'].str.contains(name, case=False, na=False)]
                if not asset_history.empty:
                    prev_total = asset_history['Total_Value'].iloc[-1]
            mode = extracted_data.get('investment_mode', 'contribution')
            added_val = round((amount - prev_total) if mode == "revaluation" else amount, 2)
            total_val = round(amount if mode == "revaluation" else (prev_total + amount), 2)
            new_row = pd.DataFrame([{'Date': date_str, 'Asset': name, 'Added_Value': added_val, 'Total_Value': total_val}])
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(INVEST_FILE, index=False)
            msg = helper.format_ai_response("telegram", "investment", amount, name, total_val=total_val, added_val=added_val)
            bot.reply_to(message, msg, parse_mode="Markdown")
        else:
            bot.reply_to(message, "Sorry, I couldn't understand if this was an expense, income, or transfer. Please rephrase.")
    except Exception as e:
        bot.reply_to(message, f"Error parsing request: {e}")

if __name__ == "__main__":
    print("Telegram Bot is running ...")
    bot.infinity_polling()
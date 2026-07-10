"""
Synapse Telegram Bot - Control your agent fleet from your phone.

Reuses the Claude brain + tools from smart_chat.py, with Telegram as the interface.

Setup:
    pip install python-telegram-bot
    export ANTHROPIC_API_KEY="sk-ant-..."
    export TELEGRAM_BOT_TOKEN="7123...:AAH..."     (from @BotFather)
    python telegram_bot.py

Security:
    On first message, the bot prints YOUR Telegram user id in the terminal.
    Set TELEGRAM_ALLOWED_USER to that id so ONLY you can control your fleet:
    export TELEGRAM_ALLOWED_USER="123456789"
"""

import os
import anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# Reuse the brain from smart_chat: tools, executor, system prompt, model
from smart_chat import TOOLS, execute_tool, SYSTEM_PROMPT, MODEL

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Per-chat conversation history: {chat_id: [messages]}
conversations = {}
MAX_HISTORY = 40  # trim old messages so context doesn't grow forever

ALLOWED_USER = os.environ.get("TELEGRAM_ALLOWED_USER")  # your Telegram user id (string)


def claude_turn(chat_id: int, user_text: str) -> str:
    """Run one Claude turn with tool use for this chat."""
    messages = conversations.setdefault(chat_id, [])
    messages.append({"role": "user", "content": user_text})

    # Trim history (keep pairs intact from the start)
    if len(messages) > MAX_HISTORY:
        del messages[: len(messages) - MAX_HISTORY]
        # Ensure history starts with a user message
        while messages and messages[0]["role"] != "user":
            messages.pop(0)

    import json
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if not tool_calls:
            text = "".join(b.text for b in response.content if b.type == "text")
            messages.append({"role": "assistant", "content": response.content})
            return text or "(no response)"

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in tool_calls:
            print(f"[tool: {call.name}] {json.dumps(call.input)}")
            result = execute_tool(call.name, call.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result,
            })
        messages.append({"role": "user", "content": results})


def authorized(update: Update) -> bool:
    """Only allow the configured user (if set)."""
    user_id = str(update.effective_user.id)
    if ALLOWED_USER is None:
        print(f"⚠️  Message from Telegram user id: {user_id}")
        print(f"    To lock the bot to yourself, set: export TELEGRAM_ALLOWED_USER=\"{user_id}\"")
        return True  # open mode until configured
    return user_id == ALLOWED_USER


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Sorry, this bot is private.")
        return
    await update.message.reply_text(
        "🤖 Synapse Assistant online!\n\n"
        "Try:\n"
        "• \"i walked 5 km this morning\"\n"
        "• \"i studied 2 hours of math\"\n"
        "• \"how's my fleet doing?\"\n"
        "• \"make me an agent that tracks my water intake\"\n\n"
        "/reset — clear conversation memory"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    conversations.pop(update.effective_chat.id, None)
    await update.message.reply_text("🧹 Conversation memory cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Sorry, this bot is private.")
        return

    user_text = update.message.text
    chat_id = update.effective_chat.id
    print(f"\nYou (Telegram): {user_text}")

    # Show typing indicator while Claude thinks
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = claude_turn(chat_id, user_text)
    except anthropic.APIError as e:
        reply = f"⚠️ API error: {e}"
    except Exception as e:
        reply = f"⚠️ Error: {e}"

    print(f"Bot: {reply[:200]}")
    await update.message.reply_text(reply)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set your bot token first:  export TELEGRAM_BOT_TOKEN='7123...:AAH...'")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set your API key first:  export ANTHROPIC_API_KEY='sk-ant-...'")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("=" * 55)
    print("  📱 SYNAPSE TELEGRAM BOT — running!")
    print("  Open Telegram on your phone and message your bot.")
    print("  (Ctrl+C to stop)")
    print("=" * 55)
    app.run_polling()


if __name__ == "__main__":
    main()
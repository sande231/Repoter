"""
Synapse Telegram Bot - Control your agent fleet from your phone, by voice or text.

Reuses the Claude brain + tools from smart_chat.py, with Telegram as the interface.

Setup:
    pip install python-telegram-bot openai-whisper edge-tts pydub
    export ANTHROPIC_API_KEY="sk-ant-..."
    export TELEGRAM_BOT_TOKEN="7123...:AAH..."     (from @BotFather)
    python telegram_bot.py

    Voice transcription runs locally via openai-whisper, and voice replies are
    synthesized locally via edge-tts - no OpenAI API key needed for either.
    Converting speech to Telegram-ready voice notes requires ffmpeg on PATH.

Security:
    On first message, the bot prints YOUR Telegram user id in the terminal.
    Set TELEGRAM_ALLOWED_USER to that id so ONLY you can control your fleet:
    export TELEGRAM_ALLOWED_USER="123456789"
"""

import io
import os
import tempfile

import anthropic
import edge_tts
import whisper
from pydub import AudioSegment
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import email_queue
# Reuse the brain from smart_chat: tools, executor, system prompt, model
from smart_chat import TOOLS, execute_tool, SYSTEM_PROMPT, MODEL, PENDING_EMAILS

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
whisper_model = whisper.load_model("base")

# Voice replies should sound spoken, not read like a report. Kept separate from
# smart_chat.SYSTEM_PROMPT so the CLI chat (smart_chat.py) is unaffected.
VOICE_ADDENDUM = """

You are being used through a voice-enabled Telegram bot (Jarvis-style). Every reply may be read
aloud as speech, so:
- Keep replies short and conversational, like you're talking, not writing a report.
- Never use markdown, bullet points, numbered lists, or headers - say it in plain sentences.
- Avoid long strings of numbers or symbols that are awkward to hear spoken aloud."""

TELEGRAM_SYSTEM_PROMPT = SYSTEM_PROMPT + VOICE_ADDENDUM

# Per-chat conversation history: {chat_id: [messages]}
conversations = {}
MAX_HISTORY = 40  # trim old messages so context doesn't grow forever

ALLOWED_USER = os.environ.get("TELEGRAM_ALLOWED_USER")  # your Telegram user id (string)

TTS_VOICE = "en-US-GuyNeural"


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
            system=TELEGRAM_SYSTEM_PROMPT,
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
            result = execute_tool(call.name, call.input, chat_id=chat_id)
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


async def transcribe_voice(update: Update) -> str:
    """Download an incoming Telegram voice note and transcribe it with local Whisper."""
    tg_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await tg_file.download_to_drive(tmp_path)
        result = whisper_model.transcribe(tmp_path)
        return result["text"]
    finally:
        os.remove(tmp_path)


async def synthesize_speech(text: str) -> bytes:
    """Convert reply text to speech with local edge-tts, as Telegram-voice-note-ready Opus audio."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_tmp:
        mp3_path = mp3_tmp.name
    ogg_path = mp3_path[: -len(".mp3")] + ".ogg"
    try:
        communicate = edge_tts.Communicate(text, voice=TTS_VOICE)
        await communicate.save(mp3_path)
        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(ogg_path, format="ogg", codec="libopus")
        with open(ogg_path, "rb") as f:
            return f.read()
    finally:
        os.remove(mp3_path)
        if os.path.exists(ogg_path):
            os.remove(ogg_path)


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """Run one Claude turn for user_text and send back both a text and a voice reply."""
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

    try:
        audio_bytes = await synthesize_speech(reply)
        voice_note = io.BytesIO(audio_bytes)
        voice_note.name = "reply.ogg"
        await context.bot.send_voice(chat_id=chat_id, voice=voice_note)
    except Exception as e:
        print(f"⚠️ TTS/voice-send error: {e}")

    pending = PENDING_EMAILS.get(chat_id)
    if pending:
        preview = (
            "📧 Draft ready — review before sending:\n\n"
            f"To: {pending['recipient']}\n"
            f"Subject: {pending['subject']}\n\n"
            f"{pending['body']}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Send", callback_data=f"email_send:{chat_id}:{pending['token']}"),
            InlineKeyboardButton("❌ Discard", callback_data=f"email_discard:{chat_id}:{pending['token']}"),
        ]])
        await context.bot.send_message(chat_id=chat_id, text=preview, reply_markup=keyboard)


async def handle_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not authorized(update):
        await query.answer("Not authorized.")
        return

    action, chat_id_str, token = query.data.split(":", 2)
    chat_id = int(chat_id_str)
    pending = PENDING_EMAILS.get(chat_id)

    if not pending or pending["token"] != token:
        await query.answer()
        await query.edit_message_text("This draft is no longer active (expired or already handled).")
        return

    PENDING_EMAILS.pop(chat_id, None)

    if action == "email_send":
        email_queue.enqueue(pending["subject"], pending["body"], [pending["recipient"]])
        await query.answer("Sent")
        await query.edit_message_text(f"✅ Queued for delivery to {pending['recipient']}.")
    else:
        await query.answer("Discarded")
        await query.edit_message_text("❌ Draft discarded — nothing was sent.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Sorry, this bot is private.")
        return

    await respond(update, context, update.message.text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Sorry, this bot is private.")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        user_text = await transcribe_voice(update)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Couldn't transcribe that voice note: {e}")
        return

    await respond(update, context, user_text)


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
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_email_callback))

    print("=" * 55)
    print("  📱 SYNAPSE TELEGRAM BOT — running!")
    print("  Open Telegram on your phone and message your bot.")
    print("  (Ctrl+C to stop)")
    print("=" * 55)
    app.run_polling()


if __name__ == "__main__":
    main()
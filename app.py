
import os
import logging
import requests
from io import BytesIO
from pypdf import PdfReader
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
HF_MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-7B-Instruct"
)
PDF_FOLDER = "pdfs"

documents = []


def load_pdfs():
    global documents
    documents = []

    if not os.path.isdir(PDF_FOLDER):
        os.makedirs(PDF_FOLDER, exist_ok=True)

    for filename in os.listdir(PDF_FOLDER):
        if filename.lower().endswith(".pdf"):
            path = os.path.join(PDF_FOLDER, filename)
            try:
                reader = PdfReader(path)
                text = "\n".join(
                    page.extract_text() or ""
                    for page in reader.pages
                )
                if text.strip():
                    documents.append({
                        "name": filename,
                        "text": text
                    })
                    logging.info("PDF yuklandi: %s", filename)
            except Exception:
                logging.exception("PDF o'qilmadi: %s", filename)


def find_context(question, limit=5):
    words = {
        word.lower().strip(".,!?():;")
        for word in question.split()
        if len(word) > 2
    }
    matches = []

    for document in documents:
        paragraphs = document["text"].splitlines()
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if len(paragraph) < 30:
                continue

            paragraph_words = {
                word.lower().strip(".,!?():;")
                for word in paragraph.split()
            }
            score = len(words & paragraph_words)

            if score:
                matches.append(
                    (score, document["name"], paragraph)
                )

    matches.sort(key=lambda item: item[0], reverse=True)
    selected = []
    seen = set()

    for score, name, paragraph in matches:
        key = (name, paragraph)
        if key not in seen:
            selected.append((name, paragraph))
            seen.add(key)
        if len(selected) >= limit:
            break

    return selected


def ask_ai(question, context):
    if not HF_TOKEN:
        return "AI tokeni hali sozlanmagan. Administrator HF_TOKEN ni sozlashi kerak."

    context_text = "\n\n".join(
        f"PDF: {name}\n{paragraph}"
        for name, paragraph in context
    )

    if not context_text:
        return "PDF hujjatlaridan savolga mos ma'lumot topilmadi."

    url = (
        "https://router.huggingface.co/v1/"
        "chat/completions"
    )

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": HF_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen o'zbek tilida javob beradigan yordamchisan. "
                    "Asosan berilgan PDF parchalariga tayan. "
                    "Javob hujjatlarda bo'lmasa, buni ochiq ayt. "
                    "Ma'lumotni o'ylab topma."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PDF ma'lumotlari:\n{context_text}\n\n"
                    f"Savol: {question}"
                ),
            },
        ],
        "max_tokens": 500,
        "temperature": 0.2,
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90,
    )

    if response.status_code != 200:
        logging.error(
            "AI xatosi %s: %s",
            response.status_code,
            response.text[:500],
        )
        return (
            "AI xizmatidan javob olib bo'lmadi. "
            "Model nomi, ruxsat yoki bepul limitni tekshirish kerak."
        )

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Assalomu alaykum!\n"
        "Men PDF hujjatlar asosida savollarga javob beruvchi botman.\n"
        "Savolingni yoz."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Savolingni oddiy matn ko'rinishida yubor.\n"
        "Javoblar yuklangan PDF hujjatlar asosida tayyorlanadi."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = update.message.text

    if not question:
        return

    await update.message.reply_text("Savolingni tekshiryapman...")

    try:
        relevant_context = find_context(question)

        if not relevant_context:
            await update.message.reply_text(
                "PDF fayllardan mos ma'lumot topilmadi. "
                "Hujjatlar joylanganini tekshir."
            )
            return

        answer = ask_ai(question, relevant_context)

        if len(answer) > 4000:
            answer = answer[:3950] + "\n\n(Javob qisqartirildi.)"

        await update.message.reply_text(answer)

    except Exception:
        logging.exception("Xabarni qayta ishlashda xato")
        await update.message.reply_text(
            "Xatolik yuz berdi. Keyinroq qayta urinib ko'r."
        )


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN sozlanmagan.")

    load_pdfs()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logging.info("Bot ishga tushmoqda. PDF soni: %s", len(documents))
    app.run_polling()


if __name__ == "__main__":
    main()


import os
import logging
import asyncio
from pathlib import Path

import requests
from flask import Flask
from pypdf import PdfReader
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN")

MODEL = "Qwen/Qwen2.5-7B-Instruct"
AI_URL = "https://router.huggingface.co/v1/chat/completions"

web = Flask(__name__)
documents = []
pdf_count = 0
pages_read = 0
pdf_errors = 0


@web.get("/")
def home():
    return "PDF AI bot ishlayapti!", 200


@web.get("/health")
def health():
    return {
        "status": "ok",
        "pdf_files": pdf_count,
        "pages_read": pages_read,
    }, 200


def load_pdfs():
    global documents, pdf_count, pages_read, pdf_errors

    new_documents = []
    found = 0
    read_pages = 0
    errors = 0

    for file in sorted(BASE_DIR.glob("*.pdf")):
        found += 1
        parts = []

        try:
            reader = PdfReader(str(file))

            for number, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ""

                    if not text.strip():
                        text = page.extract_text(
                            extraction_mode="layout"
                        ) or ""

                    if text.strip():
                        parts.append(
                            f"[{file.name}, {number}-sahifa]\n{text}"
                        )
                        read_pages += 1

                except Exception:
                    logger.exception(
                        "Sahifa o'qilmadi: %s, %s",
                        file.name,
                        number,
                    )

            if parts:
                new_documents.append({
                    "name": file.name,
                    "text": "\n\n".join(parts),
                })

            logger.info(
                "%s: %s ta matnli sahifa",
                file.name,
                len(parts),
            )

        except Exception:
            errors += 1
            logger.exception("PDF ochilmadi: %s", file.name)

    documents = new_documents
    pdf_count = found
    pages_read = read_pages
    pdf_errors = errors

    logger.info(
        "YAKUNIY HOLAT: %s PDF, %s matnli sahifa, %s xato",
        found,
        read_pages,
        errors,
    )

    return found, read_pages, errors


def find_context(question, limit=12000):
    words = {
        word.lower().strip(".,!?():;\"'«»")
        for word in question.split()
        if len(word) > 2
    }

    matches = []

    for doc in documents:
        for paragraph in doc["text"].split("\n\n"):
            paragraph_words = {
                word.lower().strip(".,!?():;\"'«»")
                for word in paragraph.split()
                if len(word) > 2
            }

            score = len(words & paragraph_words)

            if score:
                matches.append(
                    (score, doc["name"], paragraph.strip())
                )

    matches.sort(key=lambda item: item[0], reverse=True)

    result = []
    total = 0

    for score, name, paragraph in matches:
        piece = f"Hujjat: {name}\n{paragraph}"

        if total + len(piece) > limit:
            continue

        result.append(piece)
        total += len(piece)

        if total >= limit:
            break

    if result:
        return "\n\n".join(result)

    # Kalit so'z topilmasa, hujjatlardan qisqa kontekst olamiz.
    for doc in documents:
        piece = f"Hujjat: {doc['name']}\n{doc['text'][:2000]}"

        if total + len(piece) > limit:
            break

        result.append(piece)
        total += len(piece)

    return "\n\n".join(result)


def ask_ai(question, context):
    if not HF_TOKEN:
        return (
            "AI tokeni sozlanmagan. Render → Environment bo'limiga "
            "HF_TOKEN o'zgaruvchisini qo'sh."
        )

    if not context.strip():
        return (
            "PDF fayllardan matn topilmadi. /status bilan tekshir. "
            "Agar PDF skanerlangan rasm bo'lsa, OCR kerak bo'lishi mumkin."
        )

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen o'zbek tilidagi PDF hujjatlar yordamchisisan. "
                    "Savolga berilgan hujjatlar asosida javob ber. "
                    "Javob hujjatda bo'lmasa, buni ochiq ayt. "
                    "Ma'lumotni o'ylab topma. Sodda va aniq yoz."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PDF ma'lumotlari:\n{context}\n\n"
                    f"Foydalanuvchi savoli: {question}"
                ),
            },
        ],
        "max_tokens": 700,
        "temperature": 0.2,
    }

    try:
        response = requests.post(
            AI_URL,
            headers=headers,
            json=payload,
            timeout=90,
        )

        if response.status_code != 200:
            logger.error(
                "AI API xatosi: HTTP %s, %s",
                response.status_code,
                response.text[:500],
            )
            return (
                f"AI xizmatida xato: HTTP {response.status_code}. "
                "Render Logs bo'limini tekshir."
            )

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except requests.Timeout:
        return "AI javobi kechikyapti. Birozdan keyin qayta urin."

    except Exception:
        logger.exception("AI javobida xato")
        return "AI javobini olishda xato yuz berdi."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men PDF hujjatlar asosida javob beruvchi botman.\n\n"
        "Savolingni yoz.\n"
        "/status — PDF holati\n"
        "/reload — PDFlarni qayta o'qish"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Topilgan PDF: {pdf_count}\n"
        f"Matni o'qilgan sahifalar: {pages_read}\n"
        f"Matni bor hujjatlar: {len(documents)}\n"
        f"PDF xatolari: {pdf_errors}\n"
        f"AI tokeni: {'Sozlangan' if HF_TOKEN else 'Sozlanmagan'}"
    )


async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text("PDFlarni qayta o'qiyapman...")

    found, pages, errors = await asyncio.to_thread(load_pdfs)

    await update.message.reply_text(
        f"Qayta o'qish tugadi.\n"
        f"PDF: {found}\n"
        f"Matnli sahifalar: {pages}\n"
        f"Xatolar: {errors}"
    )


async def answer_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = (update.message.text or "").strip()

    if not question:
        return

    await update.message.reply_text("Hujjatlardan izlayapman...")

    context_text = find_context(question)
    answer = await asyncio.to_thread(
        ask_ai,
        question,
        context_text,
    )

    # Telegram xabarining maksimal uzunligidan oshmasin.
    for start_index in range(0, len(answer), 4000):
        await update.message.reply_text(
            answer[start_index:start_index + 4000]
        )


def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN topilmadi. Render Environment ni tekshir."
        )

    load_pdfs()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reload", reload_pdfs))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer_question,
        )
    )

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import threading

    threading.Thread(
        target=lambda: web.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
        ),
        daemon=True,
    ).start()

    main()

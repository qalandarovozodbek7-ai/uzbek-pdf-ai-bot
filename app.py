
import os
import glob
import logging
import threading
import asyncio
import requests
import fitz

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

documents = []
pdf_count = 0
page_count = 0
pdf_errors = 0

web = Flask(__name__)


def load_pdfs():
    global documents, pdf_count, page_count, pdf_errors

    documents = []
    pdf_count = 0
    page_count = 0
    pdf_errors = 0

    paths = glob.glob(
        os.path.join(BASE_DIR, "**", "*.pdf"),
        recursive=True,
    )
    paths = list(dict.fromkeys(paths))

    logger.info("Topilgan PDF fayllar: %s", len(paths))

    for path in paths:
        try:
            with fitz.open(path) as doc:
                pdf_count += 1
                extracted = 0

                for index, page in enumerate(doc):
                    text = page.get_text("text").strip()

                    if text:
                        documents.append({
                            "file": os.path.basename(path),
                            "page": index + 1,
                            "text": text,
                        })
                        extracted += 1
                        page_count += 1

                logger.info(
                    "%s: %s sahifadan matn olindi",
                    os.path.basename(path),
                    extracted,
                )

        except Exception:
            pdf_errors += 1
            logger.exception("PDF ochishda xato: %s", path)

    logger.info(
        "Yakun: PDF=%s, matnli sahifa=%s, xato=%s",
        pdf_count,
        page_count,
        pdf_errors,
    )


def find_context(question, max_chars=10000):
    words = {
        word.lower().strip(".,!?():;\"'«»")
        for word in question.split()
        if len(word) > 2
    }

    ranked = []

    for item in documents:
        lower = item["text"].lower()
        score = sum(1 for word in words if word in lower)

        if score:
            ranked.append((score, item))

    ranked.sort(key=lambda x: x[0], reverse=True)

    result = []
    total = 0

    for score, item in ranked[:8]:
        chunk = (
            f"Manba: {item['file']}, "
            f"sahifa {item['page']}\n"
            f"{item['text']}\n"
        )

        if total + len(chunk) > max_chars:
            break

        result.append(chunk)
        total += len(chunk)

    return "\n---\n".join(result)


def ask_ai(question):
    token = os.getenv("HF_TOKEN")

    if not token:
        return (
            "AI token sozlanmagan. Render → Environment "
            "bo‘limida HF_TOKEN mavjudligini tekshir."
        )

    context = find_context(question)

    if not context:
        return (
            "PDF hujjatlardan savolingga mos matn topilmadi. "
            "/status orqali PDF va matnli sahifalar sonini tekshir. "
            "PDF skanerlangan rasm bo‘lsa, OCR talab qilinishi mumkin."
        )

    url = "https://router.huggingface.co/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen yo‘l qurilishi, yo‘l ekspluatatsiyasi, "
                    "laboratoriya sinovlari va sifat nazorati "
                    "bo‘yicha o‘zbek tilida javob beradigan yordamchisan. "
                    "Faqat berilgan hujjat matniga asoslan. "
                    "Aniq javob manbada bo‘lmasa, buni ochiq ayt. "
                    "Me’yor, raqam yoki standartni o‘ylab topma. "
                    "Javob oxirida manba fayli va sahifasini ko‘rsat."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Hujjatlardan topilgan matn:\n{context}\n\n"
                    f"Savol: {question}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 700,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=90,
        )

        if response.status_code != 200:
            logger.error(
                "AI xatosi: %s %s",
                response.status_code,
                response.text[:1000],
            )
            return (
                f"AI xizmati xatosi: HTTP {response.status_code}. "
                "Render Logs va HF_TOKEN sozlamasini tekshirish kerak."
            )

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception:
        logger.exception("AI so‘rovida xato")
        return (
            "AI xizmatiga ulanishda xato yuz berdi. "
            "Keyinroq urinib ko‘r."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men yo‘l qurilishi va boshqa PDF hujjatlar "
        "asosida savollarga javob beraman.\n\n"
        "Savolingni yoz.\n"
        "/status — PDF holati\n"
        "/reload — PDFlarni qayta o‘qish"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Topilgan PDF: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"PDF xatolari: {pdf_errors}"
    )


async def reload_pdfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("PDF fayllar qayta o‘qilmoqda...")
    await asyncio.to_thread(load_pdfs)

    await update.message.reply_text(
        f"Tayyor.\n"
        f"PDF: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"Xatolar: {pdf_errors}"
    )


async def answer_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()

    await update.message.reply_text(
        "Hujjatlardan javob qidiryapman..."
    )

    answer = await asyncio.to_thread(ask_ai, question)

    await update.message.reply_text(answer[:4000])


@web.route("/")
def home():
    return "PDF bot ishlayapti", 200


@web.route("/health")
def health():
    return "OK", 200


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    web.run(host="0.0.0.0", port=port)


def main():
    load_pdfs()

    token = os.getenv("TELEGRAM_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN sozlanmagan. "
            "Render Environment bo‘limini tekshir."
        )

    threading.Thread(target=run_web, daemon=True).start()

    bot = Application.builder().token(token).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("reload", reload_pdfs))
    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer_question,
        )
    )

    logger.info("Telegram bot ishga tushmoqda")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

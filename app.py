
import os
import glob
import logging
import threading
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
PDF_DIR = os.path.join(BASE_DIR, "pdfs")

documents = []
pdf_count = 0
page_count = 0
pdf_errors = 0

app = Flask(__name__)


def load_pdfs():
    global documents, pdf_count, page_count, pdf_errors

    documents = []
    pdf_count = 0
    page_count = 0
    pdf_errors = 0

    # PDF fayllarni loyiha ichidagi barcha papkalardan qidirish
    paths = glob.glob(
        os.path.join(BASE_DIR, "**", "*.pdf"),
        recursive=True,
    )
    paths = list(dict.fromkeys(paths))

    logger.info("Topilgan PDF fayllar: %s", len(paths))

    for path in paths:
        pdf_count += 1

        try:
            doc = fitz.open(path)
            extracted = 0

            for index, page in enumerate(doc):
                text = page.get_text("text").strip()

                # Birinchi sahifa diagnostikasi
                if index == 0:
                    logger.info(
                        "PDF=%s | Sahifalar=%s | "
                        "Birinchi sahifa matni=%s belgi | "
                        "Rasmlar=%s",
                        os.path.basename(path),
                        len(doc),
                        len(text),
                        len(page.get_images(full=True)),
                    )
                    if text:
                        logger.info("Matn namunasi: %r", text[:200])

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
            doc.close()

        except Exception:
            pdf_errors += 1
            logger.exception("PDF ochishda xato: %s", path)

    logger.info(
        "Yakun: PDF=%s, matnli sahifa=%s, xato=%s",
        pdf_count, page_count, pdf_errors,
    )


def find_context(question, max_chars=10000):
    words = {
        word.lower().strip(".,!?():;\"'")
        for word in question.split()
        if len(word) > 2
    }

    ranked = []
    for item in documents:
        text = item["text"]
        lower = text.lower()
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
        return "HF_TOKEN sozlanmagan. Render → Environment bo‘limini tekshir."

    context = find_context(question)

    if not context:
        return (
            "PDF fayllardan bu savolga mos matn topilmadi. "
            "Avval /status buyrug‘i bilan matnli sahifalar sonini tekshir."
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
                    "Sen o‘zbek tilida javob beradigan yordamchisan. "
                    "Faqat berilgan PDF matniga asoslan. "
                    "Javobni o‘zbek tilida aniq yoz. "
                    "Javob manbada bo‘lmasa, buni ochiq ayt. "
                    "Manba fayli va sahifasini ko‘rsat."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PDF matnlari:\n{context}\n\n"
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
                f"AI xatosi: {response.status_code}. "
                "Render Logs bo‘limini tekshirish kerak."
            )

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception:
        logger.exception("AI so‘rovida xato")
        return "AI xizmatiga ulanishda xato yuz berdi. Keyinroq urinib ko‘r."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men PDF hujjatlar asosida javob beraman.\n"
        "Savolingni yoz.\n\n"
        "/status — PDF holati\n"
        "/reload — PDF fayllarni qayta o‘qish"
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
        f"Tayyor.\nPDF: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"Xatolar: {pdf_errors}"
    )


async def answer_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = update.message.text.strip()
    await update.message.reply_text("Hujjatlardan javob qidiryapman...")
    answer = await asyncio.to_thread(ask_ai, question)
    await update.message.reply_text(answer[:4000])


web = Flask(__name__)


@web.route("/")
def home():
    return "PDF bot ishlayapti"


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
        raise RuntimeError("TELEGRAM_TOKEN sozlanmagan")

    threading.Thread(target=run_web, daemon=True).start()

    bot = Application.builder().token(token).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("reload", reload_pdfs))
    bot.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, answer_question)
    )

    logger.info("Telegram bot ishga tushmoqda")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    main()

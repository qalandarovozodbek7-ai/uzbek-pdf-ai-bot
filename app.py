
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
HF_URL = "https://router.huggingface.co/v1/chat/completions"

# AI modeli
HF_MODEL = "openai/gpt-oss-20b"

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

    logger.info("Topilgan PDF: %s", len(paths))

    for path in paths:
        try:
            with fitz.open(path) as pdf:
                pdf_count += 1
                extracted = 0

                for index, page in enumerate(pdf):
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
                    "%s: %s matnli sahifa",
                    os.path.basename(path),
                    extracted,
                )

        except Exception:
            pdf_errors += 1
            logger.exception("PDF ochilmadi: %s", path)

    logger.info(
        "Natija: PDF=%s, sahifa=%s, xato=%s",
        pdf_count,
        page_count,
        pdf_errors,
    )


def tokenize(text):
    return {
        word.strip(".,!?():;\"'«»[]{}").lower()
        for word in text.split()
        if len(word.strip(".,!?():;\"'«»[]{}")) > 2
    }


def find_context(question, max_chars=12000):
    question_words = tokenize(question)
    ranked = []

    for item in documents:
        text_words = tokenize(item["text"])
        score = len(question_words & text_words)

        if score:
            ranked.append((score, item))

    ranked.sort(key=lambda item: item[0], reverse=True)

    result = []
    total = 0

    for score, item in ranked[:8]:
        chunk = (
            f"Manba: {item['file']}, sahifa {item['page']}\n"
            f"{item['text']}\n"
        )

        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 300:
                result.append(chunk[:remaining])
            break

        result.append(chunk)
        total += len(chunk)

    return "\n---\n".join(result)


def ask_ai(question):
    token = os.getenv("HF_TOKEN")

    if not token:
        return (
            "HF_TOKEN sozlanmagan. Render → Environment "
            "bo‘limini tekshir."
        )

    context = find_context(question)

    if not context:
        if not documents:
            return (
                "PDF fayllardan o‘qiladigan matn topilmadi. "
                "Skanerlangan PDF uchun OCR kerak."
            )

        return (
            "Savolingga mos matn PDF ichidan topilmadi. "
            "Savolni boshqa so‘zlar bilan yozib ko‘r."
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": HF_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen yo‘l qurilishi, yo‘l ekspluatatsiyasi, "
                    "laboratoriya sinovlari va sifat nazorati "
                    "bo‘yicha o‘zbek tilidagi yordamchisan. "
                    "Berilgan hujjat matniga asoslan. "
                    "Me'yorlar, raqamlar va standartlarni "
                    "o‘ylab topma. Javobni hujjatdan tasdiqlab "
                    "bo‘lmasa, buni ochiq ayt. Javob oxirida "
                    "manba fayli va sahifasini ko‘rsat."
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
            HF_URL,
            headers=headers,
            json=payload,
            timeout=90,
        )

        if response.status_code != 200:
            logger.error(
                "Hugging Face HTTP %s: %s",
                response.status_code,
                response.text[:2000],
            )

            try:
                data = response.json()
                detail = (
                    data.get("error")
                    or data.get("message")
                    or str(data)
                )
            except ValueError:
                detail = response.text

            return (
                f"AI xatosi: HTTP {response.status_code}\n"
                f"Model: {HF_MODEL}\n"
                f"Sababi: {str(detail)[:1200]}"
            )

        data = response.json()
        choices = data.get("choices", [])

        if not choices:
            return "AI bo‘sh javob qaytardi."

        answer = choices[0].get("message", {}).get("content", "")

        if not answer:
            return "AI javob matnini qaytarmadi."

        return answer.strip()

    except requests.Timeout:
        logger.exception("AI vaqtida javob bermadi")
        return "AI javobi kechikdi. Birozdan keyin qayta urin."

    except requests.RequestException:
        logger.exception("AI ulanish xatosi")
        return "AI xizmatiga ulanib bo‘lmadi. Keyinroq urin."

    except Exception:
        logger.exception("Kutilmagan AI xatosi")
        return "Kutilmagan xato. Render Logs ni tekshir."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "Assalomu alaykum!\n"
        "Men PDF hujjatlar asosida savollarga javob beraman.\n\n"
        "Savolingni yoz.\n"
        "/status — PDF holati\n"
        "/reload — PDFlarni qayta o‘qish"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        f"PDF fayllar: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"PDF xatolari: {pdf_errors}\n"
        f"AI modeli: {HF_MODEL}"
    )


async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text("PDFlar qayta o‘qilmoqda...")
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
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()

    if not question:
        return

    if len(question) > 2000:
        await update.message.reply_text(
            "Savoling juda uzun. Qisqartirib yubor."
        )
        return

    await update.message.reply_text(
        "Hujjatlardan javob qidiryapman..."
    )

    answer = await asyncio.to_thread(ask_ai, question)

    for start_index in range(0, len(answer), 3500):
        await update.message.reply_text(
            answer[start_index:start_index + 3500]
        )


@web.route("/")
def home():
    return "PDF bot ishlayapti", 200


@web.route("/health")
def health():
    return "OK", 200


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    web.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


def main():
    load_pdfs()

    token = os.getenv("TELEGRAM_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN topilmadi. "
            "Render Environment ni tekshir."
        )

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reload", reload_pdfs))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer_question,
        )
    )

    logger.info("Bot ishga tushmoqda. Model: %s", HF_MODEL)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

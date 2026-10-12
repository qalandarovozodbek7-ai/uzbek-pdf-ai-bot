
import os
import glob
import logging
import threading
import asyncio

import fitz
import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =============== SOZLAMALAR ===============

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

MODEL = "Qwen/Qwen2.5-7B-Instruct"
HF_URL = "https://router.huggingface.co/v1/chat/completions"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

web = Flask(__name__)

documents = []
pdf_count = 0
pages_read = 0
pdf_errors = []


# =============== PDF O'QISH ===============

def load_pdfs():
    global documents, pdf_count, pages_read, pdf_errors

    found_documents = []
    errors = []
    total_pdfs = 0
    total_pages = 0

    pdf_files = glob.glob(
        os.path.join(BASE_DIR, "**", "*.pdf"),
        recursive=True,
    )
    pdf_files = sorted(set(pdf_files))

    logger.info("PDF qidirilayotgan papka: %s", BASE_DIR)
    logger.info("Topilgan PDF fayllar: %s", len(pdf_files))

    for pdf_path in pdf_files:
        total_pdfs += 1
        logger.info("PDF ochilmoqda: %s", pdf_path)

        try:
            with fitz.open(pdf_path) as pdf:
                logger.info(
                    "%s faylida %s ta sahifa bor",
                    os.path.basename(pdf_path),
                    len(pdf),
                )

                for page_number, page in enumerate(pdf):
                    try:
                        text = page.get_text("text").strip()

                        # Birinchi sahifani diagnostika qilish
                        if page_number == 0:
                            logger.info(
                                "PDF: %s | Sahifa o'lchami: %s | "
                                "Matn uzunligi: %s | Rasmlar: %s",
                                os.path.basename(pdf_path),
                                page.rect,
                                len(text),
                                len(page.get_images(full=True)),
                            )

                            # Ajratilgan matndan namunani ko'rsatish
                            if text:
                                logger.info(
                                    "Matn namunasi: %s",
                                    repr(text[:250]),
                                )

                        if text:
                            found_documents.append({
                                "file": os.path.basename(pdf_path),
                                "page": page_number + 1,
                                "text": text,
                            })
                            total_pages += 1

                    except Exception as exc:
                        errors.append(
                            f"{os.path.basename(pdf_path)}, "
                            f"{page_number + 1}-sahifa: {exc}"
                        )
                        logger.exception("Sahifani o'qishda xato")

        except Exception as exc:
            errors.append(
                f"{os.path.basename(pdf_path)}: {exc}"
            )
            logger.exception("PDF ochilmadi")

    documents = found_documents
    pdf_count = total_pdfs
    pages_read = total_pages
    pdf_errors = errors

    logger.info(
        "YAKUNIY NATIJA: %s ta PDF topildi, "
        "%s ta matnli sahifa o'qildi, %s ta xato",
        pdf_count,
        pages_read,
        len(pdf_errors),
    )

    return pdf_count, pages_read, pdf_errors


# =============== KONTEKST TOPISH ===============

def find_context(question, max_chars=12000):
    if not documents:
        return ""

    words = {
        word.lower().strip(".,!?():;\"'")
        for word in question.split()
        if len(word) > 2
    }

    ranked = []

    for doc in documents:
        lower_text = doc["text"].lower()
        score = sum(1 for word in words if word in lower_text)

        if score > 0:
            ranked.append((score, doc))

    ranked.sort(key=lambda item: item[0], reverse=True)

    if ranked:
        selected = [doc for _, doc in ranked[:8]]
    else:
        selected = documents[:5]

    parts = []
    used_chars = 0

    for doc in selected:
        part = (
            f"Fayl: {doc['file']}\n"
            f"Sahifa: {doc['page']}\n"
            f"Matn:\n{doc['text']}\n"
        )

        remaining = max_chars - used_chars

        if remaining <= 0:
            break

        parts.append(part[:remaining])
        used_chars += min(len(part), remaining)

    return "\n---\n".join(parts)


# =============== AI JAVOBI ===============

def ask_ai(question):
    if not HF_TOKEN:
        return "Render Environment bo'limida HF_TOKEN yo'q."

    context = find_context(question)

    if not context:
        return (
            "PDFlardan matn olinmagan. /status va Render "
            "Logs natijalarini tekshirish kerak."
        )

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen o'zbek tilidagi hujjatlar yordamchisisan. "
                    "Berilgan hujjat matniga tayangan holda javob ber. "
                    "Hujjatda yo'q ma'lumotni to'qib chiqarmagin. "
                    "Imkon bo'lsa fayl nomi va sahifani ko'rsat."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Hujjatlardan olingan matn:\n{context}\n\n"
                    f"Savol: {question}"
                ),
            },
        ],
        "max_tokens": 700,
        "temperature": 0.2,
    }

    try:
        response = requests.post(
            HF_URL,
            headers={
                "Authorization": f"Bearer {HF_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )

        if response.status_code != 200:
            logger.error(
                "AI xatosi %s: %s",
                response.status_code,
                response.text[:1000],
            )
            return (
                f"AI xizmatida xato: HTTP {response.status_code}. "
                "Render Logs bo'limini tekshir."
            )

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except requests.Timeout:
        return "AI javobi kechikdi. Birozdan keyin qayta urin."

    except Exception:
        logger.exception("AI so'rovida xato")
        return "AI javobini olishda xato yuz berdi."


# =============== TELEGRAM BUYRUQLARI ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men PDF hujjatlar bo'yicha savollarga javob beraman.\n\n"
        "/status — PDF holati\n"
        "/reload — PDFlarni qayta o'qish\n\n"
        "Savolingni oddiy matn ko'rinishida yubor."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error_text = ""

    if pdf_errors:
        error_text = "\nXatolar:\n" + "\n".join(pdf_errors[:3])

    await update.message.reply_text(
        f"Topilgan PDF: {pdf_count}\n"
        f"Matnli sahifalar: {pages_read}\n"
        f"O'qilgan sahifalar ro'yxati: {len(documents)}\n"
        f"PDF xatolari: {len(pdf_errors)}"
        f"{error_text}"
    )


async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text("PDFlar qayta o'qilmoqda...")

    count, pages, errors = await asyncio.to_thread(load_pdfs)

    await update.message.reply_text(
        f"Qayta o'qish tugadi.\n"
        f"PDFlar: {count}\n"
        f"Matnli sahifalar: {pages}\n"
        f"Xatolar: {len(errors)}"
    )


async def handle_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = update.message.text.strip()

    if not question:
        return

    await update.message.reply_text("Hujjatlardan javob qidiryapman...")

    answer = await asyncio.to_thread(ask_ai, question)

    for index in range(0, len(answer), 4000):
        await update.message.reply_text(answer[index:index + 4000])


# =============== WEB TEKSHIRUV ===============

@web.route("/")
def home():
    return "PDF AI bot ishlayapti!"


@web.route("/health")
def health():
    return {
        "status": "ok",
        "pdf_count": pdf_count,
        "pages_read": pages_read,
        "errors": len(pdf_errors),
    }


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    web.run(host="0.0.0.0", port=port)


# =============== ISHGA TUSHIRISH ===============

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN Render Environment'da sozlanmagan."
        )

    load_pdfs()

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    bot = Application.builder().token(TELEGRAM_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("reload", reload_pdfs))
    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_question,
        )
    )

    logger.info("Bot ishga tushmoqda...")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

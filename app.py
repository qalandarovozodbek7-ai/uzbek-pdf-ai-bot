
import os
import glob
import logging
import threading

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

# ---------------- SOZLAMALAR ----------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

MODEL = "Qwen/Qwen2.5-7B-Instruct"
HF_URL = "https://router.huggingface.co/v1/chat/completions"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

documents = []
pdf_count = 0
pages_read = 0
pdf_errors = []


# ---------------- PDF MATNINI O'QISH ----------------

def load_pdfs():
    global documents, pdf_count, pages_read, pdf_errors

    new_documents = []
    errors = []
    total_pdfs = 0
    total_pages = 0

    pdf_files = glob.glob(
        os.path.join(BASE_DIR, "**", "*.pdf"),
        recursive=True,
    )

    pdf_files = sorted(set(pdf_files))

    for pdf_path in pdf_files:
        total_pdfs += 1

        try:
            with fitz.open(pdf_path) as pdf:
                for page_number, page in enumerate(pdf):
                    try:
                        text = page.get_text("text").strip()

                        if text:
                            new_documents.append({
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

        except Exception as exc:
            errors.append(f"{os.path.basename(pdf_path)}: {exc}")

    documents = new_documents
    pdf_count = total_pdfs
    pages_read = total_pages
    pdf_errors = errors

    logger.info(
        "%s ta PDF topildi, %s ta matnli sahifa o'qildi, "
        "%s ta xato",
        pdf_count,
        pages_read,
        len(pdf_errors),
    )

    return pdf_count, pages_read, pdf_errors


# ---------------- KERAKLI MATNNI TOPISH ----------------

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
        text = doc["text"]
        lower_text = text.lower()

        score = sum(
            1 for word in words if word in lower_text
        )

        if score > 0:
            ranked.append((score, doc))

    ranked.sort(key=lambda item: item[0], reverse=True)

    # Mos sahifalar topilsa, eng moslarini ishlatamiz
    if ranked:
        selected = [doc for _, doc in ranked[:8]]
    else:
        # Moslik topilmasa, hujjatlarning boshidan kontekst olamiz
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
        used_chars += len(part)

    return "\n---\n".join(parts)


# ---------------- AI JAVOBI ----------------

def ask_ai(question):
    if not HF_TOKEN:
        return (
            "HF_TOKEN sozlanmagan. Render → Environment bo'limida "
            "HF_TOKEN nomli o'zgaruvchi qo'sh."
        )

    context = find_context(question)

    if not context:
        return (
            "PDF fayllardan matn topilmadi. "
            "Avval /status orqali holatni tekshir."
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
                    "Sen o'zbek tilida javob beradigan yordamchisan. "
                    "Asosan berilgan PDF matniga tayan. "
                    "Javobni hujjatda topilmagan bo'lsa, "
                    "buni ochiq ayt. Dalil yoki ma'lumotni "
                    "o'zingdan to'qib chiqarmagin. "
                    "Kerak bo'lsa fayl nomi va sahifani ko'rsat."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PDF hujjatlardan olingan ma'lumot:\n"
                    f"{context}\n\n"
                    f"Foydalanuvchi savoli: {question}"
                ),
            },
        ],
        "max_tokens": 700,
        "temperature": 0.2,
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
                "Hugging Face xatosi: %s %s",
                response.status_code,
                response.text[:1000],
            )
            return (
                "AI xizmatida xato yuz berdi. "
                f"HTTP kodi: {response.status_code}. "
                "Render loglarini tekshirish kerak."
            )

        data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()

        return answer or "AI bo'sh javob qaytardi."

    except requests.Timeout:
        return "AI javob berishga ulgurmayapti. Birozdan keyin qayta urin."

    except Exception as exc:
        logger.exception("AI so'rovida xato")
        return f"Javob olishda xato yuz berdi: {exc}"


# ---------------- TELEGRAM BUYRUQLARI ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Assalomu alaykum!\n\n"
        "Men PDF hujjatlar asosida savollarga javob beruvchi botman.\n\n"
        "PDFlar bo'yicha savolingni yoz.\n\n"
        "/status — bot va PDF holati\n"
        "/reload — PDFlarni qayta o'qish"
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

    count, pages, errors = await __import__("asyncio").to_thread(
        load_pdfs
    )

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

    await update.message.reply_text(
        "Savolingni tekshiryapman, biroz kut..."
    )

    answer = await __import__("asyncio").to_thread(
        ask_ai,
        question,
    )

    # Telegram bitta xabarda 4096 belgigacha qabul qiladi
    for start_index in range(0, len(answer), 4000):
        await update.message.reply_text(
            answer[start_index:start_index + 4000]
        )


# ---------------- WEB HEALTH CHECK ----------------

@app.route("/")
def home():
    return "PDF AI bot ishlayapti!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "pdf_count": pdf_count,
        "pages_read": pages_read,
        "errors": len(pdf_errors),
    }


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# ---------------- BOTNI ISHGA TUSHIRISH ----------------

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN Render Environment bo'limida yo'q!"
        )

    load_pdfs()

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    application = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reload", reload_pdfs))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question)
    )

    logger.info("Telegram bot ishga tushmoqda...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

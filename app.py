
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

# =========================
# SOZLAMALAR
# =========================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HF_URL = "https://router.huggingface.co/v1/chat/completions"
HF_MODEL = "Qwen/Qwen2.5-7B-Instruct"

documents = []
pdf_count = 0
page_count = 0
pdf_errors = 0

web = Flask(__name__)


# =========================
# PDF FAYLLARNI O'QISH
# =========================

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
            with fitz.open(path) as pdf:
                pdf_count += 1
                extracted = 0

                for index, page in enumerate(pdf):
                    text = page.get_text("text").strip()

                    if not text:
                        continue

                    documents.append({
                        "file": os.path.basename(path),
                        "page": index + 1,
                        "text": text,
                    })

                    extracted += 1
                    page_count += 1

                logger.info(
                    "%s: %s ta matnli sahifa",
                    os.path.basename(path),
                    extracted,
                )

        except Exception:
            pdf_errors += 1
            logger.exception("PDF xatosi: %s", path)

    logger.info(
        "Natija: PDF=%s, matnli sahifa=%s, xato=%s",
        pdf_count,
        page_count,
        pdf_errors,
    )


# =========================
# SAVOLGA MOS MATN TOPISH
# =========================

def tokenize(text):
    return {
        word.strip(".,!?():;\"'«»[]{}").lower()
        for word in text.split()
        if len(word.strip(".,!?():;\"'«»[]{}")) > 2
    }


def find_context(question, max_chars=14000):
    question_words = tokenize(question)

    if not question_words or not documents:
        return ""

    ranked = []

    for item in documents:
        text_words = tokenize(item["text"])
        score = len(question_words & text_words)

        if score > 0:
            ranked.append((score, item))

    ranked.sort(key=lambda item: item[0], reverse=True)

    result = []
    total_chars = 0

    for score, item in ranked[:10]:
        chunk = (
            f"Manba: {item['file']}, "
            f"sahifa {item['page']}\n"
            f"{item['text']}\n"
        )

        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars

            if remaining > 300:
                result.append(chunk[:remaining])

            break

        result.append(chunk)
        total_chars += len(chunk)

    return "\n---\n".join(result)


# =========================
# AI ORQALI JAVOB OLISH
# =========================

def ask_ai(question):
    token = os.getenv("HF_TOKEN")

    if not token:
        return (
            "HF_TOKEN topilmadi.\n"
            "Render → Environment bo‘limida "
            "HF_TOKEN o‘rnatilganini tekshir."
        )

    context = find_context(question)

    if not context:
        if not documents:
            return (
                "PDF ichidan o‘qiladigan matn topilmadi.\n"
                f"Topilgan PDF: {pdf_count}\n"
                f"Matnli sahifalar: {page_count}\n\n"
                "PDF skanerlangan rasm bo‘lishi mumkin. "
                "Bunday fayl uchun OCR kerak."
            )

        return (
            "Hujjatlardan savolingga mos matn topilmadi.\n"
            "Savolni boshqacha yozib ko‘r yoki "
            "/status orqali PDF holatini tekshir."
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
                    "yo‘l laboratoriyasi, qurilish materiallari "
                    "va sifat nazorati bo‘yicha yordamchisan. "
                    "O‘zbek tilida sodda va tushunarli javob ber. "
                    "Asosiy javobni berilgan hujjatlarga tayangan "
                    "holda yoz. Hujjatda mavjud bo‘lmagan raqam, "
                    "me'yor yoki standartni o‘ylab topma. "
                    "Javobni hujjatdan tasdiqlab bo‘lmasa, "
                    "buni ochiq ayt. Mos ma'lumot topilsa, "
                    "manba fayli va sahifa raqamini ko‘rsat."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Quyidagi matnlar PDF hujjatlardan olindi.\n\n"
                    f"{context}\n\n"
                    f"Foydalanuvchi savoli: {question}"
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
            # Haqiqiy xato sababini Render Logs ga yozish
            logger.error(
                "Hugging Face HTTP %s: %s",
                response.status_code,
                response.text[:2000],
            )

            # Foydalanuvchiga xatoning qisqa izohini berish
            try:
                error_data = response.json()
                error_detail = (
                    error_data.get("error")
                    or error_data.get("message")
                    or str(error_data)
                )
            except ValueError:
                error_detail = response.text

            return (
                f"AI xizmati xatosi: HTTP {response.status_code}\n\n"
                f"Sababi: {str(error_detail)[:1000]}\n\n"
                "Bu xabar xatoni aniqlash uchun chiqarildi."
            )

        data = response.json()

        choices = data.get("choices", [])

        if not choices:
            logger.error("AI javobida choices topilmadi: %s", data)
            return "AI bo‘sh javob qaytardi. Keyinroq urinib ko‘r."

        answer = (
            choices[0]
            .get("message", {})
            .get("content", "")
        )

        if not answer:
            return "AI javob matnini qaytarmadi."

        return answer.strip()

    except requests.Timeout:
        logger.exception("AI so‘rovi vaqti tugadi")
        return (
            "AI xizmati 90 soniyada javob bermadi. "
            "Birozdan keyin qayta urinib ko‘r."
        )

    except requests.RequestException:
        logger.exception("AI xizmatiga ulanish xatosi")
        return (
            "AI xizmatiga ulanib bo‘lmadi. "
            "Internet, Hugging Face xizmati va tokenni tekshir."
        )

    except Exception:
        logger.exception("Kutilmagan AI xatosi")
        return "Kutilmagan xato yuz berdi. Render Logs ni tekshir."


# =========================
# TELEGRAM BUYRUQLARI
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text(
        "Assalomu alaykum!\n\n"
        "Men PDF hujjatlar asosida savollarga javob beraman.\n\n"
        "Masalan:\n"
        "• Asfalt zichligi qanday aniqlanadi?\n"
        "• Yo‘l asosini tekshirish tartibi qanday?\n"
        "• Laboratoriyada qanday sinovlar o‘tkaziladi?\n\n"
        "/status — PDF holati\n"
        "/reload — PDF fayllarni qayta o‘qish"
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text(
        f"PDF fayllar: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"PDF xatolari: {pdf_errors}\n\n"
        "Eslatma: skanerlangan sahifalar "
        "matnli sahifalar soniga kirmasligi mumkin."
    )


async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text(
        "PDF fayllar qayta o‘qilmoqda..."
    )

    await asyncio.to_thread(load_pdfs)

    await update.message.reply_text(
        "Qayta o‘qish tugadi.\n\n"
        f"PDF fayllar: {pdf_count}\n"
        f"Matnli sahifalar: {page_count}\n"
        f"PDF xatolari: {pdf_errors}"
    )


# =========================
# SAVOLLARGA JAVOB BERISH
# =========================

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
            "Savol juda uzun. Iltimos, uni "
            "2000 ta belgidan qisqaroq qilib yubor."
        )
        return

    await update.message.reply_text(
        "Savolingni hujjatlardan tekshiryapman..."
    )

    answer = await asyncio.to_thread(ask_ai, question)

    # Telegram bitta xabarda juda uzun matn yuborishni cheklaydi
    for start_index in range(0, len(answer), 3500):
        await update.message.reply_text(
            answer[start_index:start_index + 3500]
        )


# =========================
# RENDER WEB SERVER
# =========================

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


# =========================
# BOTNI ISHGA TUSHIRISH
# =========================

def main():
    load_pdfs()

    token = os.getenv("TELEGRAM_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN topilmadi. "
            "Render Environment bo‘limini tekshir."
        )

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    application.add_handler(
        CommandHandler("reload", reload_pdfs)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer_question,
        )
    )

    logger.info("Telegram bot ishga tushmoqda")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

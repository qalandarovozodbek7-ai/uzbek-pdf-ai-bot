
import os
import re
import threading
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

# Render va Telegram sozlamalari
BASE_DIR = Path(__file__).resolve().parent

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-7B-Instruct",
)

HF_URL = "https://router.huggingface.co/v1/chat/completions"

documents = []
pdf_status = "PDF hali yuklanmagan"
web = Flask(__name__)


@web.route("/")
def home():
    return (
        f"Uzbek PDF AI bot ishlayapti. "
        f"PDF sahifalari: {len(documents)}",
        200,
    )


def web_server():
    web.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        use_reloader=False,
    )


def find_pdf_files():
    """Loyiha ichidagi barcha PDF fayllarni topadi."""
    found = []

    # Asosiy papka va pdfs papkasini tekshirish
    search_dirs = [BASE_DIR, BASE_DIR / "pdfs"]

    ignored_dirs = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "site-packages",
    }

    seen = set()

    for folder in search_dirs:
        if not folder.is_dir():
            continue

        for current, dirs, files in os.walk(folder):
            dirs[:] = [
                d for d in dirs
                if d not in ignored_dirs
            ]

            for filename in files:
                if not filename.lower().endswith(".pdf"):
                    continue

                path = (Path(current) / filename).resolve()

                if path not in seen and path.is_file():
                    seen.add(path)
                    found.append(path)

    return sorted(found, key=lambda p: str(p).lower())


def load_pdfs():
    """PDF fayllarni o'qib, sahifalar bo'yicha saqlaydi."""
    global pdf_status

    documents.clear()
    pdf_files = find_pdf_files()

    print("Loyiha papkasi:", BASE_DIR)
    print("Topilgan PDF soni:", len(pdf_files))

    if not pdf_files:
        pdf_status = "PDF fayllar topilmadi"
        print("XATO: PDF fayllar loyiha ichida yo'q.")
        print("PDF'larni GitHub'ga ham yuklash kerak.")
        return 0

    for filename in pdf_files:
        try:
            reader = PdfReader(str(filename))

            if reader.is_encrypted:
                try:
                    if not reader.decrypt(""):
                        print("PDF parol bilan himoyalangan:", filename)
                        continue
                except Exception:
                    print("PDF ochilmadi:", filename)
                    continue

            added = 0

            for page_number, page in enumerate(
                reader.pages, start=1
            ):
                content = page.extract_text() or ""
                content = re.sub(r"\s+", " ", content).strip()

                if content:
                    documents.append({
                        "file": filename.name,
                        "page": page_number,
                        "text": content,
                    })
                    added += 1

            print(
                f"PDF: {filename.name}; "
                f"sahifalar: {len(reader.pages)}; "
                f"matnli sahifalar: {added}"
            )

        except Exception as error:
            print(f"PDF xatosi ({filename.name}): {error}")

    pdf_status = (
        f"{len(pdf_files)} ta PDF topildi, "
        f"{len(documents)} ta matnli sahifa o'qildi"
    )

    print("Natija:", pdf_status)
    return len(documents)


def find_context(question):
    """Savolga mos sahifalarni kalit so'zlar orqali topadi."""
    words = set(re.findall(r"\w+", question.lower()))
    words = {word for word in words if len(word) > 2}

    if not words:
        return ""

    scored = []

    for doc in documents:
        text_words = set(
            re.findall(r"\w+", doc["text"].lower())
        )
        score = len(words & text_words)

        if score:
            scored.append((score, doc))

    scored.sort(key=lambda item: item[0], reverse=True)

    results = []

    for score, doc in scored[:5]:
        results.append(
            f"Manba: {doc['file']}, {doc['page']}-sahifa\n"
            f"{doc['text'][:2500]}"
        )

    return "\n\n".join(results)


def ask_ai(question, context):
    """Hugging Face modeli orqali javob oladi."""
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN sozlanmagan")

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
                    "Faqat berilgan PDF matnlariga asoslan. "
                    "PDFda yo'q ma'lumotni o'ylab topma. "
                    "Javobni sodda, aniq va tushunarli yoz. "
                    "Javob oxirida manba fayli va sahifasini ko'rsat. "
                    "Ma'lumot topilmasa, buni ochiq ayt."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"PDF ma'lumotlari:\n{context}\n\n"
                    f"Savol: {question}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 600,
    }

    response = requests.post(
        HF_URL,
        headers=headers,
        json=payload,
        timeout=90,
    )

    if not response.ok:
        print(
            "Hugging Face xatosi:",
            response.status_code,
            response.text[:1000],
        )

    response.raise_for_status()
    data = response.json()

    return data["choices"][0]["message"]["content"].strip()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Salom! Men PDF hujjatlar asosida savollarga javob beraman.\n\n"
        f"Holat: {pdf_status}\n\n"
        "Savolingni yoz.\n"
        "/status — PDF holatini ko'rish\n"
        "/reload — PDF fayllarni qayta o'qish"
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        f"PDF holati: {pdf_status}\n"
        f"O'qilgan matnli sahifalar: {len(documents)}"
    )


async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    count = load_pdfs()

    await update.message.reply_text(
        f"PDF qayta tekshirildi.\n"
        f"Holat: {pdf_status}\n"
        f"O'qilgan sahifalar: {count}"
    )


async def answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()

    if not documents:
        await update.message.reply_text(
            "Hozir PDF matnlari topilmadi.\n"
            f"Holat: {pdf_status}\n"
            "GitHub'dagi PDF fayllarni tekshirish kerak."
        )
        return

    relevant = find_context(question)

    if not relevant:
        await update.message.reply_text(
            "Savolingga mos kalit so'zlar PDF matnlaridan "
            "topilmadi. Savolni boshqacha yozib ko'r."
        )
        return

    await update.message.reply_text(
        "PDF ma'lumotlarini tekshiryapman..."
    )

    try:
        result = ask_ai(question, relevant)
        await update.message.reply_text(result[:4000])

    except Exception as error:
        print("AI xatosi:", repr(error))
        await update.message.reply_text(
            "AI xizmatida xatolik yuz berdi.\n"
            "Server loglarini tekshirish kerak."
        )


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN Render Environment'da yo'q."
        )

    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN Render Environment'da yo'q."
        )

    load_pdfs()

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reload", reload_pdfs))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer,
        )
    )

    print("Telegram bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    threading.Thread(
        target=web_server,
        daemon=True,
    ).start()

    main()


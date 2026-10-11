
import os
import re
import glob
import threading
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
MODEL = os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")

documents = []
web = Flask(__name__)


@web.route("/")
def home():
    return "Uzbek PDF AI bot ishlayapti!", 200


def web_server():
    web.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        use_reloader=False,
    )


def load_pdfs():
    documents.clear()

    for filename in sorted(glob.glob("*.pdf")):
        try:
            reader = PdfReader(filename)

            for page_number, page in enumerate(reader.pages, start=1):
                content = page.extract_text() or ""
                content = re.sub(r"\s+", " ", content).strip()

                if content:
                    documents.append({
                        "file": os.path.basename(filename),
                        "page": page_number,
                        "text": content,
                    })

            print("PDF o'qildi:", filename)

        except Exception as error:
            print("PDF xatosi:", filename, error)

    print("Jami sahifalar:", len(documents))


def find_context(question):
    words = set(re.findall(r"\w+", question.lower()))
    words = {word for word in words if len(word) > 2}

    scored = []

    for doc in documents:
        text_words = set(re.findall(r"\w+", doc["text"].lower()))
        score = len(words & text_words)

        if score > 0:
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
    url = "https://router.huggingface.co/v1/chat/completions"

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
                    "Javobni aniq va tushunarli yoz. "
                    "Agar javob PDF matnida bo'lmasa, buni ochiq ayt. "
                    "Manba fayli va sahifasini ko'rsat."
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
        url,
        headers=headers,
        json=payload,
        timeout=90,
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Salom! Men 10 ta PDF hujjat asosida "
        "savollarga javob beraman. Savolingni yoz."
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
            "PDF hujjatlar topilmadi. Server sozlamalarini tekshirish kerak."
        )
        return

    relevant = find_context(question)

    if not relevant:
        await update.message.reply_text(
            "PDF hujjatlardan savolingga mos ma'lumot topilmadi."
        )
        return

    await update.message.reply_text(
        "Hujjatlarni tekshiryapman..."
    )

    try:
        result = ask_ai(question, relevant)
        await update.message.reply_text(result[:4000])

    except Exception as error:
        print("AI xatosi:", error)
        await update.message.reply_text(
            "AI xizmatida xatolik yuz berdi. "
            "Birozdan keyin qayta urinib ko'r."
        )


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN sozlanmagan.")

    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN sozlanmagan.")

    load_pdfs()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, answer)
    )

    print("Telegram bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    threading.Thread(
        target=web_server,
        daemon=True,
    ).start()

    main()

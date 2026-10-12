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
BASE_DIR = Path(__file__).resolve().parent
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-7B-Instruct",
)
HF_URL = "https://router.huggingface.co/v1/chat/completions"
app = Flask(__name__)
documents = []
pdf_status = "PDF fayllar hali yuklanmadi"
def find_pdf_files():
    found = []
    ignored = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
    }
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in ignored
        ]
        for filename in files:
            if filename.lower().endswith(".pdf"):
                found.append(Path(root) / filename)
    return sorted(set(found))
def load_pdfs():
    global documents, pdf_status
    documents = []
    pdf_files = find_pdf_files()
    pages_read = 0
    errors = 0
    print(f"PDF fayllar soni: {len(pdf_files)}")
    for filename in pdf_files:
        print(f"PDF: {filename}")
        try:
            reader = PdfReader(str(filename))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as error:
                    print(
                        f"PDF ochilmadi: {filename.name}: "
                        f"{error}"
                    )
                    errors += 1
                    continue
            for page_number, page in enumerate(
                reader.pages,
                start=1,
            ):
                try:
                    content = (
                        page.extract_text(
                            extraction_mode="layout"
                        )
                        or ""
                    )
                except Exception as error:
                    print(
                        f"SAHIFA XATOSI: {filename.name}, "
                        f"{page_number}-sahifa: {error}"
                    )
                    errors += 1
                    continue
                content = re.sub(
                    r"\s+",
                    " ",
                    content,
                ).strip()
                print(
                    f"Tekshirildi: {filename.name}, "
                    f"{page_number}-sahifa, "
                    f"matn uzunligi: {len(content)}"
                )
                if content:
                    documents.append({
                        "file": filename.name,
                        "page": page_number,
                        "text": content,
                    })
                    pages_read += 1
        except Exception as error:
            print(
                f"PDF XATOSI: {filename.name}: {error}"
            )
            errors += 1
    pdf_status = (
        f"{len(pdf_files)} ta PDF topildi, "
        f"{pages_read} ta matnli sahifa o'qildi, "
        f"{errors} ta xato"
    )
    print("YAKUNIY HOLAT:", pdf_status)
def find_context(question, limit=5):
    if not documents:
        return ""
    question_words = set(
        re.findall(r"\w+", question.lower())
    )
    scored = []
    for document in documents:
        text = document["text"]
        text_words = set(
            re.findall(r"\w+", text.lower())
        )
        score = len(question_words & text_words)
        if score:
            scored.append((score, document))
    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )
    selected = []
    for score, document in scored[:limit]:
        selected.append(
            f"Manba: {document['file']}, "
            f"{document['page']}-sahifa\n"
            f"{document['text']}"
        )
    return "\n\n".join(selected)
def ask_ai(question, context):
    if not HF_TOKEN:
        return (
            "AI xizmati sozlanmagan. "
            "Render Environment ichida HF_TOKEN "
            "borligini tekshir."
        )
    if not context:
        return (
            "PDF fayllardan savolingga mos matn "
            "topilmadi. /reload buyrug'i bilan "
            "PDFlarni qayta yuklashga urinib ko'r."
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
                    "Sen o'zbek tilida javob beradigan "
                    "yordamchisan. Asosan berilgan PDF "
                    "matnlariga tayan. Javob hujjatlarda "
                    "bo'lmasa, buni ochiq ayt. "
                    "Manba fayli va sahifasini ko'rsat."
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
        "max_tokens": 900,
    }
    try:
        response = requests.post(
            HF_URL,
            headers=headers,
            json=payload,
            timeout=90,
        )
        if response.status_code != 200:
            print(
                "AI API xatosi:",
                response.status_code,
                response.text[:1000],
            )
            return (
                "AI xizmatida xatolik yuz berdi. "
                f"Kod: {response.status_code}"
            )
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as error:
        print("AI so'rov xatosi:", error)
        return "AI javob berishda xatolik yuz berdi."
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Salom! Men PDF hujjatlar asosida "
        "savollarga javob beruvchi botman.\n\n"
        "Savolingni yoz.\n"
        "/status — PDF holatini ko'rish\n"
        "/reload — PDFlarni qayta o'qish"
    )
async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(pdf_status)
async def reload_pdfs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "PDF fayllar qayta o'qilmoqda..."
    )
    await asyncio.to_thread(load_pdfs)
    await update.message.reply_text(pdf_status)
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = update.message.text
    if not question:
        return
    if not documents:
        await update.message.reply_text(
            "Hozir PDF matnlari topilmadi.\n"
            f"Holat: {pdf_status}\n\n"
            "PDF fayllar va Render loglarini tekshirish kerak."
        )
        return
    await update.message.reply_text(
        "Savolingni hujjatlardan tekshiryapman..."
    )
    relevant_context = find_context(question)
    answer = await asyncio.to_thread(
        ask_ai,
        question,
        relevant_context,
    )
    await update.message.reply_text(
        answer[:4000]
    )
@app.route("/")
def home():
    return "Bot ishlayapti. " + pdf_status
@app.route("/health")
def health():
    return "OK"
def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
    )
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN Render Environment'da yo'q"
        )
    load_pdfs()
    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()
    bot = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("reload", reload_pdfs))
    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )
    print("Telegram bot ishga tushmoqda...")
    bot.run_polling()
if __name__ == "__main__":
    import asyncio
    main()

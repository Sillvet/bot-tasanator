import telebot
import os
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("CALCULADORA_TOKEN")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['chatid'])
def get_chat_id(message):
    bot.reply_to(message, f"📌 El chat_id de este grupo es: {message.chat.id}")
    print(f"🔍 Chat ID detectado: {message.chat.id}")

print("✅ Bot esperando comando /chatid para mostrar el chat_id del grupo...")
bot.infinity_polling()

import telebot
import os
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("CALCULADORA_TOKEN")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(func=lambda m: True)
def get_chat_id(message):
    bot.reply_to(message, f"El chat_id de este grupo es: {message.chat.id}")
    print(f"Chat ID detectado: {message.chat.id}")

print("✅ Bot esperando mensajes. Escribe algo en el grupo para obtener el chat_id...")
bot.infinity_polling()

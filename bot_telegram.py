import os
import telebot
from supabase import create_client, Client
from dotenv import load_dotenv

# Cargar variables desde .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
USUARIOS_AUTORIZADOS = list(map(int, os.getenv("USUARIOS_AUTORIZADOS", "").split(",")))

# Conexión Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Iniciar el bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Emojis por país para mostrar ordenadamente
emojis_paises = {
    "venezuela": "🇻🇪",
    "colombia": "🇨🇴",
    "argentina": "🇦🇷",
    "perú": "🇵🇪",
    "brasil": "🇧🇷",
    "euro": "🇪🇺",
    "usa": "🇺🇸",
    "méxico": "🇲🇽",
    "panamá": "🇵🇦",
    "ecuador": "🇪🇨",
}

# --- Generar menú de botones ---
def generar_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    botones = [telebot.types.KeyboardButton(emoji + " " + pais.title()) for pais, emoji in emojis_paises.items()]
    for i in range(0, len(botones), 2):
        markup.row(*botones[i:i+2])
    return markup

# --- Obtener tasas por país ---
def obtener_tasas(nombre_pais):
    try:
        response = supabase.table("tasas").select("nombre_tasa, valor").execute()
        tasas = [t for t in response.data if nombre_pais.lower() in t['nombre_tasa'].lower() and "Tasa" in t['nombre_tasa']]
        if not tasas:
            return "❌ No se encontraron tasas para ese país."

        tasas_ordenadas = sorted(tasas, key=lambda x: ("full" not in x["nombre_tasa"].lower(), x["nombre_tasa"]))
        mensaje = f"📍 Tasas para {nombre_pais.title()}\n"
        for t in tasas_ordenadas:
            valor = round(t['valor'], 4)
            mensaje += f"{t['nombre_tasa']}: {valor}\n"
        return mensaje.strip()
    except Exception as e:
        return f"❌ Error consultando Supabase: {e}"

# --- Manejo de mensajes autorizados ---
def autorizado(message):
    if message.from_user.id not in USUARIOS_AUTORIZADOS:
        bot.reply_to(message, "⛔ Acceso restringido. No estás autorizado.")
        return False
    return True

# --- Comando /start o mensaje "tasas" ---
@bot.message_handler(commands=["start"])
@bot.message_handler(func=lambda m: m.text.lower() == "tasas")
def mostrar_menu(message):
    if not autorizado(message): return
    bienvenida = "👋 Hola, selecciona un país o escribe su nombre para ver las tasas:\n"
    bot.send_message(message.chat.id, bienvenida, reply_markup=generar_menu())

# --- Selección por emoji ---
@bot.message_handler(func=lambda message: any(p in message.text.lower() for p in emojis_paises))
def mostrar_por_pais(message):
    if not autorizado(message): return
    for pais in emojis_paises:
        if pais in message.text.lower():
            mensaje = obtener_tasas(pais)
            bot.reply_to(message, mensaje)
            return

# --- Cualquier texto ---
@bot.message_handler(func=lambda m: True)
def por_defecto(message):
    if not autorizado(message): return
    texto = message.text.lower()
    if texto in emojis_paises:
        mostrar_por_pais(message)
    else:
        bot.reply_to(message, "❌ Comando no reconocido. Toca un país o escribe 'tasas'.")

# --- Iniciar escucha del bot ---
print("🤖 Bot escuchando...")
bot.infinity_polling()

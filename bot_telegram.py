import threading
import time
from datetime import datetime, timedelta
import os
import telebot
from supabase import create_client, Client
from dotenv import load_dotenv
from guardar_tasas import actualizar_todas_las_tasas
from dateutil import parser  # 🔧 Corrección para manejar ISO con zona horaria

# === CONFIGURACIÓN ===
MODO_TEST = true
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
USUARIOS_AUTORIZADOS = list(map(int, os.getenv("USUARIOS_AUTORIZADOS", "").split(",")))
USUARIO_LIMITADO = 794327412
USUARIO_RESTRINGIDO = 7278912173

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

emojis_paises = {
    "venezuela": "🇻🇪",
    "colombia": "🇨🇴",
    "argentina": "🇦🇷",
    "perú": "🇵🇪",
    "brasil": "🇧🇷",
    "europa": "🇪🇺",
    "usa": "🇺🇸",
    "méxico": "🇲🇽",
    "panamá": "🇵🇦",
    "ecuador": "🇪🇨",
    "chile": "🇨🇱",
}

# === FUNCIONES DE BOT ===
def generar_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    botones = [telebot.types.KeyboardButton(f"{emoji} {pais.title()}") for pais, emoji in emojis_paises.items()]
    for i in range(0, len(botones), 2):
        markup.row(*botones[i:i+2])
    return markup

def obtener_pares_disponibles(nombre_pais):
    hoy = (datetime.utcnow() - timedelta(hours=4)).date().isoformat()
    response = supabase.table("tasas").select("nombre_tasa, fecha_actual").order("fecha_actual", desc=True).execute()
    data = response.data

    pares = set()
    for t in data:
        if (
            "tasa full" in t["nombre_tasa"].lower()
            and "promedio" not in t["nombre_tasa"].lower()
            and t["fecha_actual"].startswith(hoy)
            and nombre_pais.lower() in t["nombre_tasa"].lower()
        ):
            par = t["nombre_tasa"].replace("Tasa full ", "")
            pares.add(par)

    return sorted(list(pares))

def obtener_tasas_par(nombre_par, user_id):
    try:
        ahora = datetime.utcnow() - timedelta(hours=4)
        hora_actual_num = ahora.hour
        hoy = ahora.date().isoformat()
        
        # Si es antes de las 9 a.m., mostramos mensaje de fuera de horario
        if hora_actual_num < 9:
            return "🕒 Actualmente estamos fuera de horario laboral (9:00 a.m. - 9:00 p.m.). Por favor, consulta más tarde."

        response = supabase.table("tasas").select("*").order("fecha_actual", desc=True).execute()
        data = response.data

        def buscar_valor(nombre_tasa):
            for row in data:
                if row["nombre_tasa"].lower() == nombre_tasa.lower() and row["fecha_actual"].startswith(hoy):
                    valor = float(row["valor"])
                    hora = parser.isoparse(row["fecha_actual"])  # ✅ CORREGIDO
                    return valor, hora.strftime("%H:%M")
            return None, None

        # Buscar valores
        tasa_full_actual, hora_actual = buscar_valor(f"Tasa full {nombre_par}")
        tasa_full_prom, _ = buscar_valor(f"Tasa full promedio {nombre_par}")
        tasa_pub_actual, _ = buscar_valor(f"Tasa público {nombre_par}")
        tasa_pub_prom, _ = buscar_valor(f"Tasa público promedio {nombre_par}")
        tasa_may_actual, _ = buscar_valor(f"Tasa mayorista {nombre_par}")
        tasa_may_prom, _ = buscar_valor(f"Tasa mayorista promedio {nombre_par}")

        if user_id in [USUARIO_LIMITADO, USUARIO_RESTRINGIDO]:
            if tasa_pub_actual is None or tasa_may_actual is None:
                return "❌ No hay datos disponibles para ese par."
            return (
                f"📊 Tasas para {nombre_par}\n\n"
                f"Tasa Mayorista Actual: {tasa_may_actual}\n"
                f"Tasa Mayorista Promedio: {tasa_may_prom if tasa_may_prom is not None else 'No disponible'}\n"
                f"Tasa Público Actual: {tasa_pub_actual}\n"
                f"Tasa Público Promedio: {tasa_pub_prom if tasa_pub_prom is not None else 'No disponible'}\n\n"
                f"🕒 Última actualización de datos: {hora_actual}"
            )

        if tasa_full_actual is None or tasa_pub_actual is None or tasa_may_actual is None:
            return "❌ No hay datos suficientes disponibles para ese par."

        return (
            f"📊 Tasas para {nombre_par}\n\n"
            f"Tasa Full Actual: {tasa_full_actual}\n"
            f"Tasa Full Promedio: {tasa_full_prom if tasa_full_prom is not None else 'No disponible'}\n"
            f"Tasa Mayorista Actual: {tasa_may_actual}\n"
            f"Tasa Mayorista Promedio: {tasa_may_prom if tasa_may_prom is not None else 'No disponible'}\n"
            f"Tasa Público Actual: {tasa_pub_actual}\n"
            f"Tasa Público Promedio: {tasa_pub_prom if tasa_pub_prom is not None else 'No disponible'}\n\n"
            f"🕒 Última actualización de datos: {hora_actual}"
        )

    except Exception as e:
        return f"❌ Error obteniendo tasas: {e}"

def autorizado(message):
    if message.from_user.id not in USUARIOS_AUTORIZADOS:
        bot.reply_to(message, "⛔️ Acceso restringido. No estás autorizado.")
        return False
    return True

@bot.message_handler(commands=["start"])
@bot.message_handler(func=lambda m: m.text.lower() == "tasas")
def mostrar_menu(message):
    if not autorizado(message): return
    bienvenida = "🔕 Hola, selecciona un país para ver los pares disponibles:"
    bot.send_message(message.chat.id, bienvenida, reply_markup=generar_menu())

@bot.message_handler(func=lambda message: True)
def manejar_mensaje(message):
    if not autorizado(message): return
    texto = message.text.lower()

    if " - " in texto:
        mensaje = obtener_tasas_par(texto.title(), message.from_user.id)
        bot.reply_to(message, mensaje)
        return

    for pais in emojis_paises:
        if pais in texto:
            pares = obtener_pares_disponibles(pais)
            if pares:
                markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
                for par in pares:
                    markup.add(telebot.types.KeyboardButton(par))
                bot.send_message(message.chat.id, f"🔍 Elige un par disponible con {pais.title()}:", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, f"❌ No se encontraron pares con {pais.title()}.")
            return

    bot.reply_to(message, "❌ Comando no reconocido. Escribe 'Tasas' o selecciona un país.")

# === ACTUALIZACIÓN PERIÓDICA ===
def actualizar_periodicamente():
    while True:
        try:
            ahora = datetime.utcnow() - timedelta(hours=4)
            hora_actual = ahora.hour
            minuto_actual = ahora.minute

            if MODO_TEST:
                if 9 <= hora_actual <= 21:
                    print(f"🧪 [TEST] Actualizando tasas a las {ahora.strftime('%H:%M')}...")
                    actualizar_todas_las_tasas()
                    print("✅ Tasas actualizadas (TEST).")
                else:
                    print(f"⏸️ [TEST] Fuera del horario ({ahora.strftime('%H:%M')})")
                time.sleep(300)
            else:
                if 9 <= hora_actual <= 21 and minuto_actual == 0:
                    print(f"🔄 Actualizando tasas a las {ahora.strftime('%H:%M')}...")
                    actualizar_todas_las_tasas()
                    print("✅ Tasas actualizadas.")
                else:
                    print(f"⏸️ Esperando hora exacta (actual: {ahora.strftime('%H:%M')})")
                time.sleep(60)

        except Exception as e:
            print(f"⚠️ Error al actualizar tasas: {e}")
            time.sleep(60)

# === INICIO DE BOT ===
if MODO_TEST:
    print("🧪 El bot está corriendo en MODO TEST (actualiza cada 5 min).")
else:
    print("✅ El bot está corriendo en MODO PRODUCCIÓN (actualiza cada 1 h entre 09:00 y 21:00).")

try:
    modo = "🧪 MODO TEST (actualiza cada 5 min)" if MODO_TEST else "✅ MODO PRODUCCIÓN (actualiza cada 1 h)"
    bot.send_message(USUARIOS_AUTORIZADOS[0], f"🤖 Bot iniciado en {modo}")
except Exception as e:
    print(f"⚠️ No se pudo enviar mensaje de inicio: {e}")

threading.Thread(target=actualizar_periodicamente, daemon=True).start()
print("🤖 Bot escuchando...")
bot.infinity_polling()

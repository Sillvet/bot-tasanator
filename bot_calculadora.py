import telebot
from telebot import types
from supabase import create_client, Client
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

# === CONFIGURACIÓN ===
load_dotenv()

TELEGRAM_TOKEN = os.getenv("CALCULADORA_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

CHAT_ID_OPERADORES = os.getenv("CHAT_ID_OPERADORES", "-4834814893")
CHAT_ID_GANANCIAS = os.getenv("CHAT_ID_GANANCIAS", "-4867786872")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Diccionario para guardar datos temporales de cada usuario
user_data = {}

# Lista de países disponibles
paises = [
    "Chile", "Venezuela", "Colombia", "Argentina",
    "Perú", "Brasil", "Europa", "USA",
    "México", "Panamá", "Ecuador"
]

# === Funciones de tasas ===
def obtener_tasa(origen, destino, tipo_tasa):
    nombre_tasa = f"Tasa {tipo_tasa} {origen} - {destino}"
    response = supabase.table("tasas").select("valor") \
        .eq("nombre_tasa", nombre_tasa) \
        .order("fecha_actual", desc=True).limit(1).execute()
    if response.data:
        return float(response.data[0]["valor"])
    return None

def obtener_tasa_full(origen, destino):
    return obtener_tasa(origen, destino, "full")

def obtener_valor_usdt(origen):
    try:
        nombre_tasa = f"USDT en {origen} (venta)"
        response = supabase.table("tasas").select("valor") \
            .eq("nombre_tasa", nombre_tasa) \
            .order("fecha_actual", desc=True).limit(1).execute()
        if response.data:
            return float(response.data[0]["valor"])
    except Exception as e:
        print(f"❌ Error obteniendo USDT para {origen}: {e}")
    return None

# === Función para registrar transacción ===
def registrar_transaccion(data):
    try:
        response = supabase.table("transacciones").insert({
            "usuario": data["usuario"],
            "origen": data["origen"],
            "destino": data["destino"],
            "tipo_tasa": data["tipo_tasa"],
            "monto_envio": data["monto_envio"],
            "monto_recibir": data["monto_recibir"],
            "nombre_receptor": data["nombre_receptor"],
            "documento_receptor": data["documento_receptor"],
            "cuenta_receptor": data["cuenta_receptor"],
            "nombre_banco": data["nombre_banco"],
            "codigo_transaccion": data["codigo_transaccion"],
            "fecha": (datetime.utcnow() - timedelta(hours=4)).isoformat()
        }).execute()
        print(f"✅ Transacción guardada: {response.data}")
    except Exception as e:
        print(f"❌ Error guardando transacción: {e}")

# === NUEVA FUNCIÓN: Registrar ganancia en saldos_diarios ===
def registrar_ganancia(moneda, ganancia):
    hoy = (datetime.utcnow() - timedelta(hours=4)).date().isoformat()
    try:
        response = supabase.table("saldos_diarios").select("*") \
            .eq("fecha", hoy).eq("moneda", moneda).execute()

        if response.data:
            registro = response.data[0]
            nuevo_saldo = (registro.get("saldo_final") or 0) + ganancia
            nueva_ganancia = (registro.get("ganancia_dia") or 0) + ganancia

            supabase.table("saldos_diarios").update({
                "saldo_final": nuevo_saldo,
                "ganancia_dia": nueva_ganancia
            }).eq("id", registro["id"]).execute()
        else:
            supabase.table("saldos_diarios").insert({
                "fecha": hoy,
                "moneda": moneda,
                "saldo_inicial": 0,
                "saldo_final": ganancia,
                "ganancia_dia": ganancia,
                "ubicacion": "Pendiente"
            }).execute()

        print(f"✅ Ganancia {ganancia} {moneda} registrada en saldos_diarios.")
    except Exception as e:
        print(f"❌ Error registrando ganancia: {e}")

# === Handler /start ===
@bot.message_handler(commands=['start'])
def start(message):
    user_data[message.chat.id] = {}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(paises), 2):
        markup.row(*paises[i:i+2])
    bot.send_message(message.chat.id, "👋 ¡Hola! Selecciona el país de **origen** del envío:", reply_markup=markup)
    bot.register_next_step_handler(message, seleccionar_origen)

def seleccionar_origen(message):
    origen = message.text
    if origen not in paises:
        bot.reply_to(message, "⚠️ Por favor, selecciona un país válido.")
        return start(message)
    user_data[message.chat.id]["origen"] = origen

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(paises), 2):
        markup.row(*paises[i:i+2])
    bot.send_message(message.chat.id, "📍 Ahora selecciona el país de **destino**:", reply_markup=markup)
    bot.register_next_step_handler(message, seleccionar_destino)

def seleccionar_destino(message):
    destino = message.text
    if destino not in paises:
        bot.reply_to(message, "⚠️ Por favor, selecciona un país válido.")
        return seleccionar_origen(message)
    user_data[message.chat.id]["destino"] = destino

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row("Público", "Mayorista")
    bot.send_message(message.chat.id, "💱 ¿Qué tipo de tasa deseas usar?", reply_markup=markup)
    bot.register_next_step_handler(message, seleccionar_tipo_tasa)

def seleccionar_tipo_tasa(message):
    tipo_tasa = message.text.lower()
    if tipo_tasa not in ["público", "mayorista"]:
        bot.reply_to(message, "⚠️ Selecciona una opción válida (Público o Mayorista).")
        return seleccionar_destino(message)
    user_data[message.chat.id]["tipo_tasa"] = tipo_tasa

    bot.send_message(message.chat.id, "💰 Ingresa el monto a enviar (en la moneda de origen):")
    bot.register_next_step_handler(message, ingresar_monto)

def ingresar_monto(message):
    try:
        monto_envio = float(message.text)
        user_data[message.chat.id]["monto_envio"] = monto_envio
    except:
        bot.reply_to(message, "⚠️ Ingresa un número válido.")
        return seleccionar_tipo_tasa(message)

    origen = user_data[message.chat.id]["origen"]
    destino = user_data[message.chat.id]["destino"]
    tipo_tasa = user_data[message.chat.id]["tipo_tasa"]

    tasa = obtener_tasa(origen, destino, tipo_tasa)
    if not tasa:
        bot.reply_to(message, "❌ No se pudo obtener la tasa actual para este par.")
        return

    user_data[message.chat.id]["tasa"] = tasa

    if origen == "Colombia" and destino == "Venezuela":
        monto_recibir = round(monto_envio / tasa, 2)
    else:
        monto_recibir = round(monto_envio * tasa, 2)

    user_data[message.chat.id]["monto_recibir"] = monto_recibir
    user_data[message.chat.id]["usuario"] = message.from_user.username or message.from_user.first_name

    bot.send_message(message.chat.id, "👤 Ingresa el **nombre completo del receptor**:")
    bot.register_next_step_handler(message, ingresar_nombre_receptor)

def ingresar_nombre_receptor(message):
    user_data[message.chat.id]["nombre_receptor"] = message.text
    bot.send_message(message.chat.id, "🆔 Ingresa el **documento de identidad del receptor**:")
    bot.register_next_step_handler(message, ingresar_documento_receptor)

def ingresar_documento_receptor(message):
    user_data[message.chat.id]["documento_receptor"] = message.text
    bot.send_message(message.chat.id, "🏦 Ingresa el **número de cuenta del receptor**:")
    bot.register_next_step_handler(message, ingresar_cuenta_receptor)

def ingresar_cuenta_receptor(message):
    user_data[message.chat.id]["cuenta_receptor"] = message.text
    bot.send_message(message.chat.id, "🏦 Ingresa el **nombre del banco del receptor**:")
    bot.register_next_step_handler(message, ingresar_nombre_banco)

def ingresar_nombre_banco(message):
    user_data[message.chat.id]["nombre_banco"] = message.text
    bot.send_message(message.chat.id, "🔢 Ingresa el **código de transacción** (tracking):")
    bot.register_next_step_handler(message, ingresar_codigo_transaccion)

def ingresar_codigo_transaccion(message):
    user_data[message.chat.id]["codigo_transaccion"] = message.text
    data = user_data[message.chat.id]

    resumen = (
        f"📊 **Resumen de envío:**\n\n"
        f"Origen: {data['origen']}\n"
        f"Destino: {data['destino']}\n"
        f"Tasa ({data['tipo_tasa']}): {data['tasa']}\n"
        f"Monto a enviar: {data['monto_envio']}\n"
        f"💵 Monto a recibir: {data['monto_recibir']}\n\n"
        f"👤 **Receptor:**\n"
        f"Nombre: {data['nombre_receptor']}\n"
        f"Documento: {data['documento_receptor']}\n"
        f"Cuenta: {data['cuenta_receptor']}\n"
        f"Banco: {data['nombre_banco']}\n"
        f"🔢 Código: {data['codigo_transaccion']}"
    )

    bot.send_message(message.chat.id, resumen, parse_mode="Markdown")
    registrar_transaccion(data)
    bot.send_message(CHAT_ID_OPERADORES, f"🚀 **Nueva Transacción:**\n\n{resumen}", parse_mode="Markdown")

    tasa_full = obtener_tasa_full(data['origen'], data['destino'])
    if tasa_full and data['tasa'] < tasa_full:
        ganancia_moneda_origen = round(
            (data['monto_envio'] * (tasa_full - data['tasa'])) / tasa_full, 2
        )
        valor_usdt = obtener_valor_usdt(data['origen'])
        ganancia_usdt = round(ganancia_moneda_origen / valor_usdt, 2) if valor_usdt else 0

        mensaje_ganancia = (
            f"💰 **Ganancia generada:**\n\n"
            f"Código: {data['codigo_transaccion']}\n"
            f"Ganancia: {ganancia_moneda_origen} {data['origen']}\n"
            f"Ganancia en USDT: {ganancia_usdt} USDT"
        )
        bot.send_message(CHAT_ID_GANANCIAS, mensaje_ganancia, parse_mode="Markdown")

        registrar_ganancia(data['origen'], ganancia_moneda_origen)

# === Comando /resumen para ver ganancias diarias ===
@bot.message_handler(commands=['resumen'])
def resumen_diario(message):
    hoy = (datetime.utcnow() - timedelta(hours=4)).date().isoformat()
    try:
        response = supabase.table("saldos_diarios").select("*").eq("fecha", hoy).execute()
        if not response.data:
            bot.reply_to(message, "📊 No hay registros de ganancias hoy.")
            return

        resumen = "📊 **Resumen de ganancias hoy:**\n\n"
        for row in response.data:
            resumen += (f"Moneda: {row['moneda']}\n"
                        f"Saldo final: {row['saldo_final']}\n"
                        f"Ganancia día: {row['ganancia_dia']}\n"
                        f"Ubicación: {row['ubicacion']}\n\n")

        bot.send_message(message.chat.id, resumen, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error consultando el resumen: {e}")

# === Handler fallback ===
@bot.message_handler(func=lambda message: True)
def fallback(message):
    bot.reply_to(message, "❓ No entendí tu mensaje. Usa /start para comenzar una operación.")

print("🤖 Bot Calculadora de Envíos corriendo...")
bot.infinity_polling()

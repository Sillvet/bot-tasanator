import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from supabase_client import supabase

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GRUPO_REGISTRO_ID = -4841192951  # ID del grupo donde se registran los saldos

bot = telebot.TeleBot(TELEGRAM_TOKEN)


# === Obtener la última tasa para el país ===
def obtener_tasa_usdt_por_pais(pais: str):
    nombre_tasa = f"USDT en {pais}"
    response = supabase.table("tasas") \
        .select("valor") \
        .eq("nombre_tasa", nombre_tasa) \
        .order("fecha_actual", desc=True) \
        .limit(1) \
        .execute()
    if response.data:
        return float(response.data[0]["valor"])
    print(f"❌ No se encontró tasa para {nombre_tasa}")
    return None


# === Guardar el saldo en Supabase ===
def registrar_saldo_diario(pais: str, monto_local: float, moneda: str, usuario_id: int, nombre_usuario: str):
    tasa = obtener_tasa_usdt_por_pais(pais)
    if not tasa:
        return "❌ No se puede registrar el saldo: falta la tasa de ese país."

    monto_usdt = monto_local / tasa
    fecha_venezuela = (datetime.utcnow() - timedelta(hours=4)).date()

    try:
        response = supabase.table("registros_saldos_capital").insert({
            "fecha": fecha_venezuela.isoformat(),
            "pais": pais,
            "usuario_id": usuario_id,
            "nombre_usuario": nombre_usuario,
            "monto_local": monto_local,
            "moneda": moneda.upper(),
            "monto_usdt": round(monto_usdt, 4)
        }).execute()

        if response.data:
            return f"✅ Saldo registrado: {monto_local} {moneda.upper()} en {pais} ≈ {monto_usdt:.4f} USDT"
        else:
            return "❌ No se registró el saldo. Supabase no devolvió datos."
    except Exception as e:
        return f"❌ Error al registrar saldo: {e}"


# === Escuchar mensajes del grupo y registrar saldos ===
@bot.message_handler(commands=['saldo'])
def handle_saldo_command(message):
    if message.chat.id != GRUPO_REGISTRO_ID:
        return  # Ignorar si el mensaje no es del grupo autorizado

    try:
        partes = message.text.split()
        if len(partes) != 4:
            bot.reply_to(message, "❗ Formato incorrecto. Usa: `/saldo <país> <monto> <moneda>`\nEjemplo: `/saldo Chile 300000 CLP`", parse_mode='Markdown')
            return

        _, pais, monto, moneda = partes
        monto = float(monto)
        usuario_id = message.from_user.id
        nombre_usuario = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()

        resultado = registrar_saldo_diario(pais.title(), monto, moneda.upper(), usuario_id, nombre_usuario)
        bot.reply_to(message, resultado)
    except Exception as e:
        bot.reply_to(message, f"❌ Error procesando el saldo: {e}")


print("✅ Bot de registro de saldos iniciado...")
bot.infinity_polling()

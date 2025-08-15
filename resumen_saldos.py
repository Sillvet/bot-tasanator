import os
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from supabase_client import supabase

# =========================
# Configuración
# =========================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GRUPO_REGISTRO_ID = int(os.getenv("GRUPO_REGISTRO_ID", "-4841192951"))   # grupo TRABAJADORES
GRUPO_GERENCIA_ID = int(os.getenv("GRUPO_GERENCIA_ID", "-4867786872"))   # grupo GERENCIA
TABLA_SALDOS = os.getenv("TABLA_SALDOS", "registro_saldos_capital")      # nombre de tabla en Supabase

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Hora de Venezuela (UTC-4)
def now_ve():
    return datetime.utcnow() - timedelta(hours=4)

# =========================
# Tipos de saldo
# =========================
TIPOS_VALIDOS = {"efectivo", "zelle", "banco", "transferencia", "otros"}

def normalizar_tipo(valor: str | None) -> str:
    if not valor:
        return "transferencia"
    v = valor.strip().lower()
    # sinónimos comunes
    if v in {"cash"}:
        return "efectivo"
    if v not in TIPOS_VALIDOS:
        return "otros"
    return v

# =========================
# Utilidades de tasas/DB
# =========================
def obtener_tasa_usdt_por_pais(pais: str):
    """
    Busca la última tasa 'USDT en {pais}' en Supabase y devuelve su valor (float).
    """
    nombre_tasa = f"USDT en {pais}"
    try:
        resp = supabase.table("tasas") \
            .select("valor, fecha_actual, nombre_tasa") \
            .eq("nombre_tasa", nombre_tasa) \
            .order("fecha_actual", desc=True) \
            .limit(1) \
            .execute()
        print(f"[DEBUG] tasa query {nombre_tasa} -> {resp.data}")
        if resp.data:
            return float(resp.data[0]["valor"])
        print(f"❌ No se encontró tasa para {nombre_tasa}")
        return None
    except Exception as e:
        print(f"❌ Error consultando tasa {nombre_tasa}: {e}")
        return None


def registrar_saldo_diario(
    pais: str,
    monto_local: float,
    moneda: str,
    usuario_id: int,
    nombre_usuario: str,
    tipo: str = "transferencia",
):
    """
    Convierte monto_local a USDT con la tasa más reciente del país y lo guarda en la tabla de saldos.
    """
    tipo = normalizar_tipo(tipo)
    tasa = obtener_tasa_usdt_por_pais(pais)
    if not tasa:
        return f"❌ No se puede registrar el saldo: falta la tasa de '{pais}'."

    monto_usdt = monto_local / tasa
    payload = {
        "fecha": now_ve().date().isoformat(),
        "pais": pais,
        "usuario_id": int(usuario_id),
        "nombre_usuario": nombre_usuario,
        "monto_local": float(monto_local),
        "moneda": moneda.upper(),
        "monto_usdt": round(monto_usdt, 4),
        "tipo": tipo,
    }
    try:
        print(f"[DEBUG] insert payload -> {payload}")
        resp = supabase.table(TABLA_SALDOS).insert(payload).execute()
        print(f"[DEBUG] insert response -> data={getattr(resp, 'data', None)} error={getattr(resp, 'error', None)}")
        if getattr(resp, "data", None):
            return (f"✅ Saldo registrado: {monto_local} {moneda.upper()} en {pais} "
                    f"(tipo: {tipo}) ≈ {monto_usdt:.4f} USDT")
        return f"❌ No se registró el saldo. Respuesta de Supabase: {resp.__dict__}"
    except Exception as e:
        return f"❌ Error al registrar saldo: {str(e)}"


def obtener_resumen_saldos(fecha=None):
    """
    Genera el texto de resumen de saldos por país (con subtotales por tipo) para la fecha dada.
    """
    if fecha is None:
        fecha = now_ve().date()
    try:
        resp = supabase.table(TABLA_SALDOS) \
            .select("*") \
            .eq("fecha", fecha.isoformat()) \
            .execute()
        print(f"[DEBUG] resumen query -> {len(resp.data) if resp and resp.data else 0} filas")
    except Exception as e:
        return f"❌ Error consultando registros: {e}"

    if not resp.data:
        return f"❕ No se encontraron saldos registrados hoy ({fecha})."

    # {pais: {moneda, total_local, total_usdt, por_tipo:{tipo:{local,usdt}}}}
    resumen, total_usdt = {}, 0.0
    for row in resp.data:
        pais = row["pais"]
        moneda = row["moneda"]
        tipo = normalizar_tipo(row.get("tipo"))
        monto_local = float(row["monto_local"])
        monto_usdt = float(row["monto_usdt"])

        resumen.setdefault(pais, {"moneda": moneda, "total_local": 0.0, "total_usdt": 0.0, "por_tipo": {}})
        resumen[pais]["total_local"] += monto_local
        resumen[pais]["total_usdt"] += monto_usdt
        total_usdt += monto_usdt

        por_tipo = resumen[pais]["por_tipo"].setdefault(tipo, {"local": 0.0, "usdt": 0.0})
        por_tipo["local"] += monto_local
        por_tipo["usdt"] += monto_usdt

    # Mensaje
    mensaje = f"📊 *Resumen de saldos del día* ({fecha}):\n\n"
    for pais, datos in sorted(resumen.items()):
        mensaje += (
            f"📍 *{pais}*\n"
            f"   - {datos['total_local']:.2f} {datos['moneda']}\n"
            f"   - ≈ {datos['total_usdt']:.4f} USDT\n"
        )
        if datos["por_tipo"]:
            for t, vals in sorted(datos["por_tipo"].items()):
                mensaje += f"     · {t}: {vals['local']:.2f} {datos['moneda']} ≈ {vals['usdt']:.4f} USDT\n"
        mensaje += "\n"
    mensaje += f"💰 *Total general:* {total_usdt:.4f} USDT"
    return mensaje

# =========================
# Handlers del bot
# =========================
@bot.message_handler(commands=['saldo'])
def handle_saldo(message):
    # Solo acepta desde el grupo de registro
    if message.chat.id != GRUPO_REGISTRO_ID:
        return

    partes = message.text.split()
    # /saldo Pais Monto Moneda [tipo]
    if len(partes) not in (4, 5):
        bot.reply_to(
            message,
            "❗ Formato incorrecto.\n"
            "   /saldo <país> <monto> <moneda> [tipo]\n"
            "   Ejemplos:\n"
            "   /saldo Chile 750000 CLP\n"
            "   /saldo Chile 750000 CLP efectivo"
        )
        return

    _, pais, monto_str, moneda, *resto = partes
    tipo = normalizar_tipo(resto[0]) if resto else "transferencia"

    try:
        monto = float(monto_str)
    except ValueError:
        bot.reply_to(message, "❗ El monto debe ser numérico. Ej: /saldo Chile 750000 CLP")
        return

    usuario_id = message.from_user.id
    nombre_usuario = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "Desconocido"

    resultado = registrar_saldo_diario(pais.title(), monto, moneda.upper(), usuario_id, nombre_usuario, tipo=tipo)
    bot.reply_to(message, resultado)


@bot.message_handler(commands=['testsaldo'])
def test_saldo(message):
    # /testsaldo Chile 750000 CLP
    partes = message.text.split()
    if len(partes) < 4:
        bot.reply_to(message, "Usa: /testsaldo <país> <monto> <moneda>")
        return
    _, pais, monto_str, _ = partes
    try:
        monto = float(monto_str)
    except:
        bot.reply_to(message, "Monto inválido.")
        return
    tasa = obtener_tasa_usdt_por_pais(pais.title())
    usdt = None if not tasa else round(monto / tasa, 4)
    bot.reply_to(message, f"Tasa '{pais.title()}': {tasa}\nUSDT estimado: {usdt}")


@bot.message_handler(commands=['resumen'])
def handle_resumen(message):
    # Permite pedir el resumen manualmente (pruebas). Envíalo al grupo de gerencia.
    if message.chat.id not in (GRUPO_REGISTRO_ID, GRUPO_GERENCIA_ID):
        return
    msg = obtener_resumen_saldos()
    bot.send_message(GRUPO_GERENCIA_ID, msg, parse_mode="Markdown")

# =========================
# Scheduler simple 21:00 VE
# =========================
def scheduler_resumen():
    """
    Hilo que revisa cada 60s si es 21:00 VE.
    Envía el resumen una sola vez por día.
    """
    ultimo_envio = None  # guarda fecha (YYYY-MM-DD) del último envío
    while True:
        try:
            ahora = now_ve()
            es_2100 = (ahora.hour == 21 and ahora.minute == 0)
            hoy_str = ahora.date().isoformat()

            if es_2100 and ultimo_envio != hoy_str:
                msg = obtener_resumen_saldos(ahora.date())
                bot.send_message(GRUPO_GERENCIA_ID, msg, parse_mode="Markdown")
                ultimo_envio = hoy_str
                print(f"✅ Resumen enviado {hoy_str} a las 21:00 VE")
        except Exception as e:
            print(f"⚠️ Error en scheduler_resumen: {e}")

        time.sleep(60)

# Lanzar scheduler en segundo plano
threading.Thread(target=scheduler_resumen, daemon=True).start()

print("🤖 Bot de saldos (registro + resumen 21:00 VE) iniciado…")
bot.infinity_polling()

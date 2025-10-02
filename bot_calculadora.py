import os
import traceback
from pathlib import Path
from datetime import datetime, timedelta

import telebot
from telebot import types, apihelper

from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

# ========== CARGA .ENV ROBUSTA ==========
dotenv_path = find_dotenv(usecwd=True) or Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=dotenv_path, override=True)

def _mask(v):
    if not v:
        return "MISSING"
    return f"len={len(v)}"

TELEGRAM_TOKEN = os.getenv("CALCULADORA_TOKEN") or os.getenv("TASANATOR_TOKEN")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

CHAT_ID_OPERADORES = os.getenv("CHAT_ID_OPERADORES", "-4834814893")
CHAT_ID_GANANCIAS  = os.getenv("CHAT_ID_GANANCIAS",  "-4867786872")

# Admins autorizados para /precargar (IDs de Telegram separados por coma en .env)
ADMINS = set(
    int(x.strip()) for x in (os.getenv("USUARIOS_AUTORIZADOS","").split(",") if os.getenv("USUARIOS_AUTORIZADOS") else [])
    if x.strip().isdigit()
)

print(f".env path: {dotenv_path}")
print("ENV → token:", _mask(TELEGRAM_TOKEN),
      "| supabase_url:", "OK" if SUPABASE_URL else "MISSING",
      "| supabase_key:", "OK" if SUPABASE_KEY else "MISSING")

missing = []
if not TELEGRAM_TOKEN: missing.append("CALCULADORA_TOKEN o TASANATOR_TOKEN")
if not SUPABASE_URL:  missing.append("SUPABASE_URL")
if not SUPABASE_KEY:  missing.append("SUPABASE_KEY")
if missing:
    raise RuntimeError("Faltan variables: " + ", ".join(missing) + f"\n.env: {dotenv_path}")

# ========== INIT BOT / SUPABASE ==========
bot = telebot.TeleBot(TELEGRAM_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Estado temporal por usuario/chat
user_data = {}

# Catálogo de países
paises = [
    "Chile", "Venezuela", "Colombia", "Argentina",
    "Perú", "Brasil", "Europa", "USA",
    "México", "Panamá", "Ecuador"
]

# --- País -> Moneda + helpers ---
PAIS_MONEDA = {
    "Chile": "CLP",
    "Venezuela": "VES",
    "Colombia": "COP",
    "Argentina": "ARS",
    "Perú": "PEN",
    "Brasil": "BRL",
    "Europa": "EUR",
    "USA": "USD",
    "México": "MXN",
    "Panamá": "USD",
    "Ecuador": "USD",
}

# Cuenta contable por país (para columna NOT NULL 'cuenta' en movimientos_saldo)
CUENTA_POR_PAIS = {
    "Chile": "Operativa-CLP",
    "Venezuela": "Operativa-VES",
    "Colombia": "Operativa-COP",
    "Argentina": "Operativa-ARS",
    "Perú": "Operativa-PEN",
    "Brasil": "Operativa-BRL",
    "Europa": "Operativa-EUR",
    "USA": "Operativa-USD",
    "México": "Operativa-MXN",
    "Panamá": "Operativa-USD",
    "Ecuador": "Operativa-USD",
}

def hoy_utc4_date_str():
    return (datetime.utcnow() - timedelta(hours=4)).date().isoformat()

def convertir_a_usdt(pais: str, monto_local: float) -> float:
    """Convierte monto local del país a USDT usando 'USDT en {pais} (venta)'. Devuelve 0 si no hay precio."""
    px = obtener_valor_usdt(pais)
    if not px or px <= 0:
        return 0.0
    return round(monto_local / px, 6)

# ========== HELPERS ==========
def now_utc_minus4_iso():
    return (datetime.utcnow() - timedelta(hours=4)).isoformat()

def safe_send_message(chat_id, text, **kwargs):
    """
    Envía un mensaje. Si el chat fue migrado a supergrupo, captura el nuevo ID (-100...),
    reintenta el envío y devuelve el nuevo id como str para que puedas persistirlo si quieres.
    """
    try:
        bot.send_message(chat_id, text, **kwargs)
        return str(chat_id)
    except apihelper.ApiTelegramException as e:
        params = {}
        try:
            params = getattr(e, "result_json", {}).get("parameters", {})
        except Exception:
            pass
        new_id = params.get("migrate_to_chat_id")
        if new_id:
            print(f"⚠️ Chat migrado a supergrupo. Nuevo ID: {new_id}. Reintentando envío…")
            bot.send_message(new_id, text, **kwargs)
            return str(new_id)
        else:
            print("❌ Error send_message:", repr(e))
            raise

# --- Helpers de formato + obtención de resumen de saldos ---
def _fmt_num(x):
    try:
        return f"{float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return str(x)

def obtener_resumen_saldos(paises_filtrar=None):
    """
    Lee saldos_pais_actual y arma un texto Markdown.
    Si paises_filtrar es lista, limita el resumen a esos países.
    """
    try:
        r = supabase.table("saldos_pais_actual").select("pais,moneda,saldo_local,saldo_usdt").execute()
        data = r.data or []
        if paises_filtrar:
            pf = set(paises_filtrar)
            data = [row for row in data if (row.get("pais") in pf)]

        if not data:
            return "📦 *Resumen de saldos*\n\n(No hay saldos cargados aún.)"

        data.sort(key=lambda x: x.get("pais") or "")
        lineas = ["📦 *Resumen de saldos (vivo)*\n"]
        for s in data:
            pais = s.get("pais")
            mon  = s.get("moneda")
            sl   = _fmt_num(s.get("saldo_local") or 0)
            su   = _fmt_num(s.get("saldo_usdt")  or 0)
            lineas.append(f"• *{pais}* — {sl} {mon}  |  ≈ {su} USDT")
        return "\n".join(lineas)
    except Exception as e:
        print("❌ obtener_resumen_saldos:", repr(e))
        return "❌ Error generando resumen de saldos."

# ========== Funciones de tasas ==========
def obtener_tasa(origen, destino, tipo_tasa):
    """Busca en 'tasas' el último valor para 'Tasa {tipo_tasa} {origen} - {destino}'."""
    try:
        nombre_tasa = f"Tasa {tipo_tasa} {origen} - {destino}"
        response = supabase.table("tasas").select("valor") \
            .eq("nombre_tasa", nombre_tasa) \
            .order("fecha_actual", desc=True).limit(1).execute()
        if response.data:
            return float(response.data[0]["valor"])
    except Exception as e:
        print(f"❌ Error obteniendo tasa '{tipo_tasa}' {origen}->{destino}: {repr(e)}")
        print(traceback.format_exc())
    return None

def obtener_tasa_full(origen, destino):
    return obtener_tasa(origen, destino, "full")

def obtener_valor_usdt(origen):
    """Busca 'USDT en {origen} (venta)' para convertir a USDT."""
    try:
        nombre_tasa = f"USDT en {origen} (venta)"
        response = supabase.table("tasas").select("valor") \
            .eq("nombre_tasa", nombre_tasa) \
            .order("fecha_actual", desc=True).limit(1).execute()
        if response.data:
            return float(response.data[0]["valor"])
    except Exception as e:
        print(f"❌ Error obteniendo USDT para {origen}: {repr(e)}")
        print(traceback.format_exc())
    return None

# ========== SALDOS VIVOS + LIBRO MAYOR ==========
def get_moneda(pais: str) -> str:
    return PAIS_MONEDA.get(pais, "N/A")

def get_saldo_actual(pais: str):
    """Lee el saldo actual de saldos_pais_actual; si no existe, lo crea en 0 y devuelve (local, usdt)."""
    try:
        r = supabase.table("saldos_pais_actual").select("*").eq("pais", pais).limit(1).execute()
        if r.data:
            row = r.data[0]
            return float(row.get("saldo_local") or 0), float(row.get("saldo_usdt") or 0)
        supabase.table("saldos_pais_actual").insert({
            "pais": pais, "moneda": get_moneda(pais), "saldo_local": 0, "saldo_usdt": 0
        }).execute()
        return 0.0, 0.0
    except Exception as e:
        print("❌ get_saldo_actual:", repr(e))
        return 0.0, 0.0

def actualizar_saldo_y_ledger(pais: str, delta_local: float, transaccion_id: str = None, motivo: str = "transaccion", meta: dict = None):
    """
    Aplica delta_local al país, inserta en 'movimientos_saldo' y actualiza 'saldos_pais_actual'.
    Usa tus columnas existentes y agrega delta/saldos en USDT.
    """
    try:
        moneda = get_moneda(pais)
        px = obtener_valor_usdt(pais)  # precio USDT venta
        delta_usdt = round(delta_local / px, 6) if px and px > 0 else 0.0

        saldo_local_antes, saldo_usdt_antes = get_saldo_actual(pais)
        saldo_local_despues = round(saldo_local_antes + delta_local, 6)
        saldo_usdt_despues  = round(saldo_usdt_antes + delta_usdt, 6)

        mov_payload = {
            "transaccion_id": transaccion_id,                    # alias FK
            "pais": pais,
            "moneda": moneda,
            "cuenta": CUENTA_POR_PAIS.get(pais, f"Operativa-{moneda}"),  # <-- requerido NOT NULL
            "delta": delta_local,
            "balance_antes": saldo_local_antes,
            "balance_despues": saldo_local_despues,
            "delta_usdt": delta_usdt,
            "saldo_usdt_antes":  saldo_usdt_antes,
            "saldo_usdt_despues": saldo_usdt_despues,
            "motivo": motivo,
            "notas": (meta or {}).get("nota")
        }
        supabase.table("movimientos_saldo").insert(mov_payload).execute()

        supabase.table("saldos_pais_actual").upsert({
            "pais": pais,
            "moneda": moneda,
            "saldo_local": saldo_local_despues,
            "saldo_usdt":  saldo_usdt_despues,
            "updated_at": now_utc_minus4_iso()
        }).execute()

        print(f"🧾 Movimiento {pais}: Δ{delta_local} {moneda} (~{delta_usdt} USDT)")
    except Exception as e:
        print("❌ actualizar_saldo_y_ledger:", repr(e))
        print(traceback.format_exc())

# ========== Persistencia ==========
def registrar_transaccion(data):
    """Inserta en 'transacciones' y retorna el id insertado."""
    try:
        payload = {
            "usuario": data["usuario"],
            "usuario_id": data.get("usuario_id"),
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
            "fecha": now_utc_minus4_iso()
        }
        response = supabase.table("transacciones").insert(payload).execute()
        print(f"✅ Transacción guardada: {response.data}")
        if response.data and isinstance(response.data, list):
            return response.data[0].get("id")
        return None
    except Exception as e:
        print("❌ Error guardando transacción:")
        print(repr(e))
        print(traceback.format_exc())
        return None

def registrar_ganancia(moneda, ganancia):
    """Actualiza o inserta en 'saldos_diarios' la ganancia del día (UTC-4) para 'moneda'."""
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
        print(f"❌ Error registrando ganancia: {repr(e)}")
        print(traceback.format_exc())

# ========== Handlers ==========
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
    tipo_tasa = message.text.lower()  # "público" o "mayorista"
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
    if tasa is None:
        bot.reply_to(message, f"❌ No se encontró tasa para: Tasa {tipo_tasa} {origen} - {destino}")
        return

    user_data[message.chat.id]["tasa"] = tasa

    # Regla especial CO -> VE
    if origen == "Colombia" and destino == "Venezuela":
        monto_recibir = round(monto_envio / tasa, 2)
    else:
        monto_recibir = round(monto_envio * tasa, 2)

    user_data[message.chat.id]["monto_recibir"] = monto_recibir
    user_data[message.chat.id]["usuario_id"] = message.from_user.id
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
    global CHAT_ID_OPERADORES, CHAT_ID_GANANCIAS

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

    # Enviar al usuario
    bot.send_message(message.chat.id, resumen, parse_mode="Markdown")

    # Guardar en BD y obtener ID
    transaccion_id = registrar_transaccion(data)

    # Libro mayor / saldos: Origen +, Destino -
    try:
        actualizar_saldo_y_ledger(
            pais=data['origen'],
            delta_local=float(data['monto_envio']),
            transaccion_id=transaccion_id,
            motivo="transaccion",
            meta={"tipo_tasa": data['tipo_tasa'], "codigo": data['codigo_transaccion']}
        )
        actualizar_saldo_y_ledger(
            pais=data['destino'],
            delta_local=-float(data['monto_recibir']),
            transaccion_id=transaccion_id,
            motivo="transaccion",
            meta={"tipo_tasa": data['tipo_tasa'], "codigo": data['codigo_transaccion']}
        )
    except Exception as e:
        print("❌ No se pudo registrar movimientos de saldo:", repr(e))

    # Notificar a Operadores
    CHAT_ID_OPERADORES = safe_send_message(
        CHAT_ID_OPERADORES,
        f"🚀 **Nueva Transacción:**\n\n{resumen}",
        parse_mode="Markdown"
    )

    # --- Resumen global de saldos al canal de Ganancias ---
    try:
        resumen_saldos = obtener_resumen_saldos()  # todos los países
        safe_send_message(CHAT_ID_GANANCIAS, resumen_saldos, parse_mode="Markdown")
    except Exception as e:
        print("⚠️ No se pudo enviar resumen de saldos:", repr(e))

    # Ganancia vs tasa_full
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

        CHAT_ID_GANANCIAS = safe_send_message(
            CHAT_ID_GANANCIAS,
            mensaje_ganancia,
            parse_mode="Markdown"
        )

        registrar_ganancia(data['origen'], ganancia_moneda_origen)

# --- /saldo para saldos diarios por operador ---
SALDO_STATE = {}

@bot.message_handler(commands=['saldo'])
def cmd_saldo(message):
    SALDO_STATE[message.chat.id] = {}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(paises), 2):
        markup.row(*paises[i:i+2])
    bot.send_message(message.chat.id, "🏦 Selecciona el país para registrar tu saldo actual:", reply_markup=markup)
    bot.register_next_step_handler(message, saldo_seleccionar_pais)

def saldo_seleccionar_pais(message):
    pais = message.text
    if pais not in paises:
        bot.reply_to(message, "⚠️ País inválido. Usa /saldo de nuevo.")
        SALDO_STATE.pop(message.chat.id, None)
        return
    SALDO_STATE[message.chat.id]["pais"] = pais
    bot.send_message(message.chat.id, f"💰 Ingresa el **saldo actual** en **{PAIS_MONEDA.get(pais,'?')}** para {pais} (solo número):", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, saldo_ingresar_monto)

def saldo_ingresar_monto(message):
    try:
        monto_local = float(message.text.replace(",", "."))
        if monto_local < 0:
            raise ValueError()
    except:
        bot.reply_to(message, "⚠️ Ingresa un número válido (>= 0). Usa /saldo para intentar de nuevo.")
        SALDO_STATE.pop(message.chat.id, None)
        return

    pais = SALDO_STATE[message.chat.id]["pais"]
    moneda = PAIS_MONEDA.get(pais, "N/A")
    usuario_id = message.from_user.id
    nombre_usuario = message.from_user.username or message.from_user.first_name

    monto_usdt = convertir_a_usdt(pais, monto_local)
    fecha = hoy_utc4_date_str()

    try:
        payload = {
            "fecha": fecha,
            "pais": pais,
            "usuario_id": usuario_id,
            "nombre_usuario": nombre_usuario,
            "monto_local": monto_local,
            "moneda": moneda,
            "monto_usdt": monto_usdt,
        }
        supabase.table("registros_saldos_capital").insert(payload).execute()
        bot.send_message(
            message.chat.id,
            f"✅ Saldo registrado:\n\nPaís: {pais}\nMoneda: {moneda}\nMonto local: {monto_local}\n≈ {monto_usdt} USDT\nFecha: {fecha}"
        )
        try:
            safe_send_message(
                CHAT_ID_GANANCIAS,
                f"📝 Registro de saldo — {pais}\nOperador: {nombre_usuario}\nLocal: {monto_local} {moneda}\n≈ {monto_usdt} USDT\nFecha: {fecha}"
            )
        except Exception:
            pass
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error registrando saldo: {repr(e)}")
    finally:
        SALDO_STATE.pop(message.chat.id, None)

# --- /precargar (solo admins) ---
PRECARGA_STATE = {}  # chat_id -> esperando texto

@bot.message_handler(commands=['precargar'])
def precargar_cmd(message):
    if message.from_user.id not in ADMINS:
        bot.reply_to(message, "⛔ No autorizado.")
        return
    PRECARGA_STATE[message.chat.id] = True
    bot.send_message(
        message.chat.id,
        "📥 Envía las líneas con formato:\n\n"
        "`Pais: monto`\n"
        "Un país por línea. Ejemplo:\n"
        "Chile: 1500000\nVenezuela: 800000\nUSA: 12000",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(message, precargar_procesar)

def precargar_procesar(message):
    if message.chat.id not in PRECARGA_STATE:
        return
    del PRECARGA_STATE[message.chat.id]

    texto = message.text.strip()
    if not texto:
        bot.reply_to(message, "⚠️ Texto vacío.")
        return

    resumen_lineas = []
    ok = 0
    fail = 0

    for linea in texto.splitlines():
        if ":" not in linea:
            continue
        pais, monto = linea.split(":", 1)
        pais = pais.strip()
        if pais not in paises:
            resumen_lineas.append(f"❌ {pais}: país no reconocido")
            fail += 1
            continue
        try:
            # Permite "1.500.000" o "1500000" o "1,5" (coma decimal)
            limpio = monto.strip().replace(".", "").replace(",", ".")
            monto_local = float(limpio)
        except:
            resumen_lineas.append(f"❌ {pais}: monto inválido")
            fail += 1
            continue

        # Calcular USDT con precio actual
        px = obtener_valor_usdt(pais)
        monto_usdt = round(monto_local / px, 6) if px and px > 0 else 0.0
        moneda = PAIS_MONEDA.get(pais, "N/A")

        try:
            # leer saldo antes para armar asiento
            saldo_local_antes, saldo_usdt_antes = get_saldo_actual(pais)
            # delta = (nuevo - actual)
            delta_local = round(monto_local - saldo_local_antes, 6)
            delta_usdt  = round(monto_usdt - saldo_usdt_antes, 6)

            # asiento de ajuste (si hay delta)
            if abs(delta_local) > 0 or abs(delta_usdt) > 0:
                supabase.table("movimientos_saldo").insert({
                    "transaccion_id": None,
                    "pais": pais,
                    "moneda": moneda,
                    "cuenta": CUENTA_POR_PAIS.get(pais, f"Operativa-{moneda}"),
                    "delta": delta_local,
                    "balance_antes": saldo_local_antes,
                    "balance_despues": monto_local,
                    "delta_usdt": delta_usdt,
                    "saldo_usdt_antes":  saldo_usdt_antes,
                    "saldo_usdt_despues": monto_usdt,
                    "motivo": "ajuste",
                    "notas": "precarga_inicial"
                }).execute()

            # upsert estado vivo
            supabase.table("saldos_pais_actual").upsert({
                "pais": pais,
                "moneda": moneda,
                "saldo_local": monto_local,
                "saldo_usdt":  monto_usdt,
                "updated_at": now_utc_minus4_iso()
            }).execute()

            resumen_lineas.append(f"✅ {pais}: { _fmt_num(monto_local) } {moneda} | ≈ { _fmt_num(monto_usdt) } USDT")
            ok += 1
        except Exception as e:
            resumen_lineas.append(f"❌ {pais}: error {repr(e)}")
            fail += 1

    texto_res = "📥 *Precarga de saldos*\n" + "\n".join(resumen_lineas) + f"\n\nTotal OK: {ok} | Errores: {fail}"
    bot.send_message(message.chat.id, texto_res, parse_mode="Markdown")

    # Enviar resumen global al canal de ganancias para confirmar estado final
    try:
        resumen_saldos_global = obtener_resumen_saldos()
        safe_send_message(CHAT_ID_GANANCIAS, resumen_saldos_global, parse_mode="Markdown")
    except Exception:
        pass

# ========== /resumen ==========
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
            resumen += (f"Moneda: {row.get('moneda')}\n"
                        f"Saldo final: {row.get('saldo_final')}\n"
                        f"Ganancia día: {row.get('ganancia_dia')}\n"
                        f"Ubicación: {row.get('ubicacion')}\n\n")

        bot.send_message(message.chat.id, resumen, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error consultando el resumen: {repr(e)}")
        print(traceback.format_exc())

# ========== Fallback ==========
@bot.message_handler(func=lambda message: True)
def fallback(message):
    bot.reply_to(message, "❓ No entendí tu mensaje. Usa /start para comenzar una operación.")

# ========== RUN ==========
print("🤖 Bot Calculadora de Envíos corriendo...")
bot.infinity_polling()

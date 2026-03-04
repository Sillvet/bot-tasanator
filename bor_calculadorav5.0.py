import os
import traceback
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta
import time

import telebot
from telebot import types, apihelper
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

# ==========================================
# 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ==========================================
dotenv_path = find_dotenv(usecwd=True) or Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=dotenv_path, override=True)

def _mask(v): return f"len={len(v)}" if v else "MISSING"

TELEGRAM_TOKEN = os.getenv("CALCULADORA_TOKEN") or os.getenv("TASANATOR_TOKEN")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

try:
    CHAT_ID_MATRIZ     = int(os.getenv("CHAT_ID_MATRIZ", "-5258532198"))
    CHAT_ID_OPERADORES = int(os.getenv("CHAT_ID_OPERADORES", "-4834814893"))
    CHAT_ID_GANANCIAS  = int(os.getenv("CHAT_ID_GANANCIAS", "-4867786872"))
    CHAT_ID_LOGS       = int(os.getenv("CHAT_ID_LOGS", "0")) 
except ValueError:
    raise RuntimeError("❌ Error: Los IDs de chat en .env deben ser números enteros.")

ADMINS = set()
if os.getenv("USUARIOS_AUTORIZADOS"):
    for x in os.getenv("USUARIOS_AUTORIZADOS").split(","):
        if x.strip().isdigit(): ADMINS.add(int(x.strip()))

USER_ALIAS = {
    6943221885: "Rolman",
    1334370923: "Gabriel",
    794327412:  "NATALY"
}

print(f"ENV: Token={_mask(TELEGRAM_TOKEN)} | Matriz={CHAT_ID_MATRIZ} | Logs={CHAT_ID_LOGS}")

if not all([TELEGRAM_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Faltan variables en .env")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Estado en memoria
user_data = {}        
operator_uploads = {} 
operator_confirmations = {} # NUEVO: Controla las confirmaciones de fotos de los operadores
DASHBOARD_MSG_ID = None 
SALDO_STATE = {}
PRECARGA_STATE = {}

paises = ["Chile", "Venezuela", "Colombia", "Argentina", "Perú", "Brasil", "Europa", "USA", "México", "Panamá", "Ecuador"]
PAIS_MONEDA = {"Chile": "CLP", "Venezuela": "VES", "Colombia": "COP", "Argentina": "ARS", "Perú": "PEN", "Brasil": "BRL", "Europa": "EUR", "USA": "USD", "México": "MXN", "Panamá": "USD", "Ecuador": "USD"}
CUENTA_POR_PAIS = {p: f"Operativa-{PAIS_MONEDA.get(p)}" for p in paises}

# ==========================================
# 2. SEGURIDAD Y HELPERS
# ==========================================
def es_chat_autorizado(message):
    if message.chat.id == CHAT_ID_MATRIZ: return True
    if message.from_user.id in ADMINS and message.chat.type == 'private': return True
    return False

def now_utc_minus4_iso(): return (datetime.utcnow() - timedelta(hours=4)).isoformat()
def hoy_utc4_date_str(): return (datetime.utcnow() - timedelta(hours=4)).date().isoformat()

def _fmt_num(x):
    try: return f"{float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(x)

def _norm(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn")

def safe_send_message(chat_id, text, **kwargs):
    if chat_id == 0: return None 
    try: return bot.send_message(chat_id, text, **kwargs)
    except apihelper.ApiTelegramException as e:
        new_id = getattr(e, "result_json", {}).get("parameters", {}).get("migrate_to_chat_id")
        if new_id: 
            return bot.send_message(new_id, text, **kwargs)
        print(f"❌ Error msg {chat_id}: {e}")
        return None

# ==========================================
# 3. LÓGICA DE NEGOCIO (SUPABASE)
# ==========================================
def obtener_resumen_saldos():
    try:
        r = supabase.table("saldos_pais_actual").select("*").execute()
        data = r.data or []
        if not data: return "📦 *Saldos:* Vacío"
        data.sort(key=lambda x: x.get("pais") or "")
        lineas = ["📦 *Resumen de saldos (vivo)*\n"]
        for s in data:
            lineas.append(f"• *{s['pais']}* — {_fmt_num(s['saldo_local'])} {s['moneda']} | ≈ {_fmt_num(s['saldo_usdt'])} USDT")
        return "\n".join(lineas)
    except: return "❌ Error saldos"

def obtener_tasa(origen, destino, tipo):
    try:
        t = _norm(tipo)
        m = {"publico": "público", "mayorista": "mayorista", "promedio publico": "público promedio", "promedio mayorista": "mayorista promedio"}
        nombre = f"Tasa {m.get(t, 'público')} {origen} - {destino}"
        res = supabase.table("tasas").select("valor").eq("nombre_tasa", nombre).order("fecha_actual", desc=True).limit(1).execute()
        return (float(res.data[0]["valor"]), nombre) if res.data else (None, None)
    except: return None, None

def obtener_tasa_full(origen, destino):
    try:
        nombre = f"Tasa full {origen} - {destino}"
        res = supabase.table("tasas").select("valor").eq("nombre_tasa", nombre).order("fecha_actual", desc=True).limit(1).execute()
        return float(res.data[0]["valor"]) if res.data else None
    except: return None

def obtener_valor_usdt(origen):
    try:
        res = supabase.table("tasas").select("valor").eq("nombre_tasa", f"USDT en {origen} (venta)").order("fecha_actual", desc=True).limit(1).execute()
        return float(res.data[0]["valor"]) if res.data else None
    except: return None

def next_tracking_code_monthly(message) -> str:
    user_id = message.from_user.id
    period = datetime.utcnow().strftime("%Y%m")
    alias = USER_ALIAS.get(user_id, message.from_user.first_name or "OPERADOR")
    alias = str(alias).upper()
    try:
        rpc = supabase.rpc("next_tracking_seq_month", {"p_period": period}).execute()
        if getattr(rpc, "data", None) is not None:
            return f"{alias}-{int(rpc.data if isinstance(rpc.data, int) else rpc.data):03d}"
    except Exception as e: print("⚠️ Fallo RPC secuencia:", e)
    return f"{alias}-{str(int(datetime.utcnow().timestamp()))[-5:]}"

def get_saldo_actual(pais):
    try:
        r = supabase.table("saldos_pais_actual").select("*").eq("pais", pais).limit(1).execute()
        if r.data: return float(r.data[0]["saldo_local"] or 0), float(r.data[0]["saldo_usdt"] or 0)
        supabase.table("saldos_pais_actual").insert({"pais": pais, "moneda": PAIS_MONEDA.get(pais), "saldo_local": 0, "saldo_usdt": 0}).execute()
        return 0.0, 0.0
    except: return 0.0, 0.0

def actualizar_saldo_y_ledger(pais, delta_local, tx_id=None, motivo="transaccion", meta=None):
    try:
        moneda = PAIS_MONEDA.get(pais)
        px = obtener_valor_usdt(pais)
        delta_usdt = round(delta_local / px, 6) if px and px > 0 else 0.0
        sl_antes, su_antes = get_saldo_actual(pais)
        
        supabase.table("movimientos_saldo").insert({
            "transaccion_id": tx_id, "pais": pais, "moneda": moneda,
            "cuenta": CUENTA_POR_PAIS.get(pais), "delta": delta_local,
            "balance_antes": sl_antes, "balance_despues": sl_antes + delta_local,
            "delta_usdt": delta_usdt, "saldo_usdt_antes": su_antes, "saldo_usdt_despues": su_antes + delta_usdt,
            "motivo": motivo, "notas": (meta or {}).get("codigo")
        }).execute()
        
        supabase.table("saldos_pais_actual").upsert({
            "pais": pais, "moneda": moneda, "saldo_local": sl_antes + delta_local,
            "saldo_usdt": su_antes + delta_usdt, "updated_at": now_utc_minus4_iso()
        }).execute()
    except Exception as e: print(f"❌ Error ledger: {e}")

def registrar_transaccion(data):
    try:
        payload = {
            "usuario": data["usuario"], "usuario_id": data.get("usuario_id"),
            "origen": data["origen"], "destino": data["destino"], "tipo_tasa": data["tipo_tasa"],
            "monto_envio": data["monto_envio"], "monto_recibir": data["monto_recibir"],
            "tipo_operacion": data.get("tipo_operacion"),
            "datos_cliente": data.get("datos_cliente"),
            "observaciones": data.get("observaciones"),
            "nombre_receptor": "Ver Datos Cliente",
            "documento_receptor": "-",
            "cuenta_receptor": "Ver Datos Cliente",
            "nombre_banco": data.get("tipo_operacion"), 
            "codigo_transaccion": data["codigo_transaccion"], "fecha": now_utc_minus4_iso(),
            "status": "NUEVA",
            "metodo_pago": data.get("metodo_pago"),     
            "input_image_id": data.get("input_image_id"), 
            "origin_msg_id": data.get("origin_msg_id")    
        }
        res = supabase.table("transacciones").insert(payload).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e: 
        print(f"Error Registro DB: {e}")
        return None

def registrar_ganancia(moneda, ganancia):
    hoy = hoy_utc4_date_str()
    try:
        res = supabase.table("saldos_diarios").select("*").eq("fecha", hoy).eq("moneda", moneda).execute()
        if res.data:
            reg = res.data[0]
            supabase.table("saldos_diarios").update({"saldo_final": reg["saldo_final"]+ganancia, "ganancia_dia": reg["ganancia_dia"]+ganancia}).eq("id", reg["id"]).execute()
        else:
            supabase.table("saldos_diarios").insert({"fecha": hoy, "moneda": moneda, "saldo_inicial": 0, "saldo_final": ganancia, "ganancia_dia": ganancia, "ubicacion": "Pendiente"}).execute()
    except: pass

# ==========================================
# 4. NAVEGACIÓN Y TECLADOS
# ==========================================
BTN_BACK, BTN_CANCEL = "⬅️ Atrás", "❌ Cancelar"

def _ensure_state(chat_id):
    if chat_id not in user_data: user_data[chat_id] = {"history": []}
    return user_data[chat_id]

def _reset_flow(chat_id): user_data.pop(chat_id, None)

def _nav_keyboard(include_back=True):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if include_back: kb.row(BTN_BACK, BTN_CANCEL)
    else: kb.row(BTN_CANCEL)
    return kb

def _handle_nav(message):
    if message.text == BTN_CANCEL:
        _reset_flow(message.chat.id)
        bot.send_message(message.chat.id, "✅ Cancelado.", reply_markup=types.ReplyKeyboardRemove())
        return "cancel"
    if message.text == BTN_BACK: return "back"
    return None

def _push_step(chat_id, step): _ensure_state(chat_id)["history"].append(step)
def _pop_step(chat_id): 
    st = _ensure_state(chat_id)
    if st["history"]: st["history"].pop()

def go_back(chat_id):
    _pop_step(chat_id)
    st = _ensure_state(chat_id)
    if not st["history"]: return start_manual(chat_id)
    prev = st["history"][-1]
    
    funcs = {
        "origen": show_origen, "destino": show_destino, "tipo_tasa": show_tipo_tasa, 
        "monto": ask_monto, 
        "tipo_operacion": ask_tipo_operacion,
        "datos_cliente": ask_datos_cliente,
        "observaciones": ask_observaciones,
        "metodo": ask_metodo_pago, "comprobante": ask_comprobante_entrada
    }
    
    if prev in funcs: funcs[prev](chat_id)

# ==========================================
# 5. FLUJO PRINCIPAL (/start)
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    if not es_chat_autorizado(message): return
    start_manual(message.chat.id)

def start_manual(chat_id):
    _reset_flow(chat_id)
    _ensure_state(chat_id)
    show_origen(chat_id)

def show_origen(chat_id):
    _push_step(chat_id, "origen")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(paises), 2): kb.row(*paises[i:i+2])
    kb.row(BTN_CANCEL)
    bot.send_message(chat_id, "👋 Selecciona origen:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(chat_id, select_origen)

def select_origen(message):
    if _handle_nav(message) == "cancel": return
    if message.text not in paises: return show_origen(message.chat.id)
    st = _ensure_state(message.chat.id)
    st["origen"] = message.text
    show_destino(message.chat.id)

def show_destino(chat_id):
    _push_step(chat_id, "destino")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(paises), 2): kb.row(*paises[i:i+2])
    kb.row(BTN_BACK, BTN_CANCEL)
    bot.send_message(chat_id, "📍 Selecciona destino:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(chat_id, select_destino)

def select_destino(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    if message.text not in paises: return show_destino(message.chat.id)
    st = _ensure_state(message.chat.id)
    st["destino"] = message.text
    show_tipo_tasa(message.chat.id)

def show_tipo_tasa(chat_id):
    _push_step(chat_id, "tipo_tasa")
    st = _ensure_state(chat_id)
    opts = ["Público", "Mayorista", "Promedio Público", "Promedio Mayorista"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    st["map_tasa"] = {}
    for o in opts:
        t, _ = obtener_tasa(st["origen"], st["destino"], o)
        lbl = f"{o} — {t if t else 'N/D'}"
        st["map_tasa"][lbl] = o
    
    lbls = list(st["map_tasa"].keys())
    kb.row(lbls[0], lbls[1])
    kb.row(lbls[2], lbls[3])
    kb.row(BTN_BACK, BTN_CANCEL)
    bot.send_message(chat_id, "💱 Selecciona tasa:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(chat_id, select_tipo_tasa)

def select_tipo_tasa(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    st = _ensure_state(message.chat.id)
    if "map_tasa" not in st:
        bot.send_message(message.chat.id, "⚠️ **Sesión reiniciada.** Por favor selecciona el destino nuevamente.")
        return show_destino(message.chat.id)

    raw = st["map_tasa"].get(message.text, message.text)
    tasa, nombre = obtener_tasa(st["origen"], st["destino"], raw)
    
    if not tasa: 
        bot.send_message(message.chat.id, "⚠️ Error leyendo tasa. Intenta de nuevo.")
        return show_tipo_tasa(message.chat.id)
        
    st.update({"tipo_tasa": raw, "tasa": tasa, "usuario_id": message.from_user.id, "usuario": message.from_user.first_name})
    bot.send_message(message.chat.id, f"📌 Tasa: *{tasa}*", parse_mode="Markdown")
    ask_monto(message.chat.id)

def ask_monto(chat_id):
    _push_step(chat_id, "monto")
    bot.send_message(chat_id, "💰 Monto a enviar:", reply_markup=_nav_keyboard())
    bot.register_next_step_handler_by_chat_id(chat_id, input_monto)

def input_monto(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    st = _ensure_state(message.chat.id)
    try:
        m = float(message.text.replace(",", "."))
        st["monto_envio"] = m
        st["monto_recibir"] = round(m / st["tasa"], 2) if st["origen"]=="Colombia" and st["destino"]=="Venezuela" else round(m * st["tasa"], 2)
        ask_tipo_operacion(message.chat.id)
    except: ask_monto(message.chat.id)

def ask_tipo_operacion(chat_id):
    _push_step(chat_id, "tipo_operacion")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📱 Pago Móvil", "🏦 Transferencia")
    kb.row(BTN_BACK, BTN_CANCEL)
    bot.send_message(chat_id, "📤 **¿Cómo enviaremos el dinero al cliente?**", reply_markup=kb, parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(chat_id, input_tipo_operacion)

def input_tipo_operacion(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    text = message.text
    if text not in ["📱 Pago Móvil", "🏦 Transferencia"]:
        return ask_tipo_operacion(message.chat.id)
    
    st = _ensure_state(message.chat.id)
    st["tipo_operacion"] = text
    ask_datos_cliente(message.chat.id)

def ask_datos_cliente(chat_id):
    _push_step(chat_id, "datos_cliente")
    st = _ensure_state(chat_id)
    tipo = st.get("tipo_operacion")
    
    msg_instruct = "📋 **Pega los datos bancarios AQUÍ (un solo mensaje):**"
    if tipo == "📱 Pago Móvil":
        msg_instruct += "\n\n_Formato sugerido: Teléfono - Cédula - Banco_"
    else:
        msg_instruct += "\n\n_Formato sugerido: Cuenta - Nombre - Cédula - Banco_"
        
    bot.send_message(chat_id, msg_instruct, reply_markup=_nav_keyboard(), parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(chat_id, input_datos_cliente)

def input_datos_cliente(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    st = _ensure_state(message.chat.id)
    st["datos_cliente"] = message.text 
    ask_observaciones(message.chat.id)

def ask_observaciones(chat_id):
    _push_step(chat_id, "observaciones")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🚫 Ninguna")
    kb.row(BTN_BACK, BTN_CANCEL)
    bot.send_message(chat_id, "📝 **¿Alguna observación para el operador?**", reply_markup=kb, parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(chat_id, input_observaciones)

def input_observaciones(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    st = _ensure_state(message.chat.id)
    obs = message.text
    st["observaciones"] = "" if obs == "🚫 Ninguna" else obs
    ask_metodo_pago(message.chat.id)

def ask_metodo_pago(chat_id):
    _push_step(chat_id, "metodo")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏦 Transferencia", "💵 Efectivo")
    kb.row(BTN_BACK, BTN_CANCEL)
    bot.send_message(chat_id, "💳 **¿Cómo recibiste el dinero?** (Entrada)", reply_markup=kb, parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(chat_id, input_metodo_pago)

def input_metodo_pago(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    if message.text not in ["🏦 Transferencia", "💵 Efectivo"]:
        return ask_metodo_pago(message.chat.id)
    
    st = _ensure_state(message.chat.id)
    st["metodo_pago"] = message.text
    
    if message.text == "🏦 Transferencia":
        ask_comprobante_entrada(message.chat.id)
    else:
        st["codigo_transaccion"] = next_tracking_code_monthly(message)
        st["input_image_id"] = None
        confirmar_datos(message.chat.id)

def ask_comprobante_entrada(chat_id):
    _push_step(chat_id, "comprobante")
    bot.send_message(chat_id, "📸 **Sube la foto del pago recibido (Cliente -> VIP):**", reply_markup=_nav_keyboard())
    bot.register_next_step_handler_by_chat_id(chat_id, recibir_comprobante_entrada)

def recibir_comprobante_entrada(message):
    if _handle_nav(message) == "back": return go_back(message.chat.id)
    if message.content_type not in ['photo', 'document']:
        bot.send_message(message.chat.id, "⚠️ Debes enviar una imagen.")
        return ask_comprobante_entrada(message.chat.id)
    
    st = _ensure_state(message.chat.id)
    st["codigo_transaccion"] = next_tracking_code_monthly(message) 
    
    try:
        # Ya no subimos a Supabase. Guardamos el file_id temporal de Telegram.
        file_id = message.photo[-1].file_id if message.content_type == 'photo' else message.document.file_id
        st["temp_file_id"] = file_id
        
        # Flujo de confirmación visual
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Confirmar Imagen", callback_data="img_in_ok"),
               types.InlineKeyboardButton("❌ Subir otra", callback_data="img_in_no"))
        
        bot.send_photo(message.chat.id, file_id, caption="👀 **¿Es correcta esta imagen del pago del cliente?**", reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error leyendo foto: {e}")
        return ask_comprobante_entrada(message.chat.id)

@bot.callback_query_handler(func=lambda c: c.data in ("img_in_ok", "img_in_no"))
def on_img_in_confirm(cb):
    chat_id = cb.message.chat.id
    try: bot.delete_message(chat_id, cb.message.message_id)
    except: pass

    if cb.data == "img_in_no":
        ask_comprobante_entrada(chat_id)
    else:
        st = _ensure_state(chat_id)
        st["input_image_id"] = st.get("temp_file_id") # Asignamos el file_id final
        confirmar_datos(chat_id)

def confirmar_datos(chat_id):
    st = _ensure_state(chat_id)
    
    if "codigo_transaccion" not in st:
        st["codigo_transaccion"] = f"TEMP-{int(time.time())}"

    obs_text = f"\n📝 **Obs:** {st['observaciones']}" if st['observaciones'] else ""

    resumen = (
        f"🧾 **CONFIRMACIÓN DE ENVÍO**\n"
        f"🆔 **{st['codigo_transaccion']}**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📤 **Salida:** {st['tipo_operacion']}\n"
        f"📥 **Entrada:** {st['metodo_pago']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🌍 {st['origen']} ➡️ {st['destino']}\n"
        f"💸 Envia: {_fmt_num(st['monto_envio'])} {PAIS_MONEDA.get(st['origen'])}\n"
        f"💰 Recibe: {_fmt_num(st['monto_recibir'])} {PAIS_MONEDA.get(st['destino'])}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 **DATOS CLIENTE:**\n"
        f"`{st['datos_cliente']}`"
        f"{obs_text}\n\n"
        f"⚠️ _Revisa antes de confirmar._"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Confirmar", callback_data="confirm_tx"), types.InlineKeyboardButton("❌ Cancelar", callback_data="cancel_tx"))
    
    msg = bot.send_message(chat_id, resumen, parse_mode="Markdown", reply_markup=kb)
    st["origin_msg_id"] = msg.message_id

@bot.callback_query_handler(func=lambda c: c.data in ("confirm_tx", "cancel_tx"))
def on_confirm(cb):
    chat_id = cb.message.chat.id
    if cb.data == "cancel_tx":
        _reset_flow(chat_id)
        bot.edit_message_text("❌ Cancelado", chat_id, cb.message.message_id)
        return

    bot.answer_callback_query(cb.id, "Procesando...")
    try:
        finalizar_transaccion(chat_id)
        bot.edit_message_reply_markup(chat_id, cb.message.message_id, reply_markup=None)
        bot.reply_to(cb.message, "✅ **Solicitud enviada a operadores.**", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")
    finally:
        _reset_flow(chat_id)

def finalizar_transaccion(chat_id):
    data = user_data[chat_id]
    tx_id = registrar_transaccion(data)
    
    actualizar_saldo_y_ledger(data['origen'], data['monto_envio'], tx_id, meta={"codigo": data['codigo_transaccion']})
    actualizar_saldo_y_ledger(data['destino'], -data['monto_recibir'], tx_id, meta={"codigo": data['codigo_transaccion']})

    bot.send_message(chat_id, f"🚀 **Orden Creada:** {data['codigo_transaccion']}", parse_mode="Markdown")

    obs_line = f"\n📝 **Obs:** {data['observaciones']}" if data.get('observaciones') else ""

    msg_op = (
        f"🚀 **NUEVA SOLICITUD: {data['codigo_transaccion']}**\n"
        f"👨‍💻 Operador: {data['usuario']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📤 **TIPO:** {data['tipo_operacion']}\n"
        f"💸 **MONTO A ENVIAR:**\n"
        f"👉 **{_fmt_num(data['monto_recibir'])} {PAIS_MONEDA.get(data['destino'])}**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📋 **DATOS:**\n"
        f"`{data['datos_cliente']}`"
        f"{obs_line}"
    )

    markup = types.InlineKeyboardMarkup()
    if tx_id:
        markup.add(types.InlineKeyboardButton("📸 Adjuntar Foto", callback_data=f"ok_{tx_id}"),
                   types.InlineKeyboardButton("⚠️ Reportar", callback_data=f"fail_{tx_id}"))
    
    try:
        # Enviar al grupo de operadores. Si hay foto (file_id), se envía junto con el texto.
        if data.get('input_image_id'):
            op_msg = bot.send_photo(CHAT_ID_OPERADORES, data['input_image_id'], caption=msg_op, reply_markup=markup, parse_mode="Markdown")
        else:
            op_msg = bot.send_message(CHAT_ID_OPERADORES, msg_op, reply_markup=markup, parse_mode="Markdown")
            
        if op_msg and tx_id: 
            supabase.table("transacciones").update({"group_message_id": op_msg.message_id}).eq("id", tx_id).execute()
    except Exception as e:
        print(f"Error mandando a operadores: {e}")

    # Calcular Ganancias
    t_f = obtener_tasa_full(data['origen'], data['destino'])
    if t_f and data['tasa'] < t_f:
        g = round((data['monto_envio'] * (t_f - data['tasa'])) / t_f, 2)
        v_u = obtener_valor_usdt(data['origen'])
        msg_g = f"💰 **Ganancia:** {g} {data['origen']} (≈ {round(g/v_u, 2) if v_u else 0} USDT)\nCódigo: {data['codigo_transaccion']}"
        safe_send_message(CHAT_ID_GANANCIAS, msg_g, parse_mode="Markdown")
        registrar_ganancia(data['origen'], g)
    
    safe_send_message(CHAT_ID_GANANCIAS, obtener_resumen_saldos(), parse_mode="Markdown")

def update_dashboard(chat_id):
    global DASHBOARD_MSG_ID
    try:
        r = supabase.table("transacciones").select("id, codigo_transaccion, pending_reason, operator_username, group_message_id").eq("status", "PENDIENTE").execute()
        pend = r.data or []
        hora = (datetime.utcnow() - timedelta(hours=4)).strftime('%H:%M')
        
        txt = f"🚨 **PENDIENTES ({len(pend)})**\nActualizado: {hora}\n\n"
        if not pend: txt += "✅ Todo al día."
        else:
            clean_chat_id = str(chat_id).replace("-100", "") 
            for p in pend:
                code = p.get('codigo_transaccion') or "SIN-CODIGO"
                msg_id = p.get('group_message_id')
                if msg_id:
                    link = f"https://t.me/c/{clean_chat_id}/{msg_id}"
                    txt += f"• [🔗 {code}]({link}) — {p.get('pending_reason','?')} ({p.get('operator_username','Op')})\n"
                else:
                    txt += f"• {code} — {p.get('pending_reason','?')} (Op: {p.get('operator_username')})\n"
        
        if DASHBOARD_MSG_ID:
            try: bot.edit_message_text(txt, chat_id, DASHBOARD_MSG_ID, parse_mode="Markdown")
            except: pass
        else:
            m = bot.send_message(chat_id, txt, parse_mode="Markdown")
            DASHBOARD_MSG_ID = m.message_id
            try: bot.pin_chat_message(chat_id, m.message_id)
            except: pass
    except Exception as e:
        print(f"Error dashboard: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("ok_") or c.data.startswith("fail_"))
def callback_ops(call):
    action, tx_id = call.data.split("_")
    user = call.from_user.username or call.from_user.first_name
    
    if action == "ok":
        operator_uploads[call.from_user.id] = tx_id
        cod_v = tx_id
        try:
            r = supabase.table("transacciones").select("codigo_transaccion").eq("id", tx_id).execute()
            if r.data: cod_v = r.data[0]['codigo_transaccion']
        except: pass
        
        text_resp = f"@{user} 📸 Envía la foto para **{cod_v}**"
        bot.send_message(call.message.chat.id, text_resp + ":", reply_markup=types.ForceReply(), parse_mode="Markdown")
        bot.answer_callback_query(call.id, "Esperando foto...")

    elif action == "fail":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🏦 Banco Caído", callback_data=f"pend_{tx_id}_banco"), types.InlineKeyboardButton("👤 Datos Malos", callback_data=f"pend_{tx_id}_datos"))
        kb.add(types.InlineKeyboardButton("✏️ Otro (Escribir)", callback_data=f"pend_{tx_id}_otro"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id, "Selecciona motivo")

@bot.callback_query_handler(func=lambda c: c.data.startswith("pend_"))
def callback_motivo(call):
    _, tx_id, mot = call.data.split("_")
    user = call.from_user.first_name
    
    if mot == "otro":
        msg = bot.send_message(call.message.chat.id, f"@{user} ✏️ Escribe la razón de la pausa:", reply_markup=types.ForceReply())
        bot.register_next_step_handler(msg, procesar_motivo_texto, tx_id, user, call.message.message_id)
        bot.answer_callback_query(call.id, "Escribiendo motivo...")
        return

    motivos_text = {"banco": "🏦 Banco Caído", "datos": "👤 Datos Incorrectos"}
    reason = motivos_text.get(mot, "Revisión")
    actualizar_mensaje_pausa(call.message, tx_id, reason, user, call.message.message_id)

def procesar_motivo_texto(message, tx_id, user, original_msg_id):
    reason = f"✏️ {message.text}"
    try:
        bot.delete_message(message.chat.id, message.message_id)
        bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    except: pass
    actualizar_mensaje_pausa(message, tx_id, reason, user, original_msg_id)

def actualizar_mensaje_pausa(message_obj, tx_id, reason, user, msg_id_to_edit):
    supabase.table("transacciones").update({"status": "PENDIENTE", "pending_reason": reason, "operator_username": user}).eq("id", tx_id).execute()
    
    cod_visual = f"#{tx_id}"
    tx_data = None
    try:
        r = supabase.table("transacciones").select("*").eq("id", tx_id).execute()
        if r.data: 
            tx_data = r.data[0]
            cod_visual = tx_data.get('codigo_transaccion', cod_visual)
    except: pass

    text = f"⚠️ **OPERACIÓN {cod_visual} EN PAUSA**\nMotivo: {reason}\n👨‍💻 Reportado por: {user}\n"
    
    if reason != "👤 Datos Incorrectos" and tx_data:
        try:
            monto = _fmt_num(tx_data['monto_recibir'])
            moneda = PAIS_MONEDA.get(tx_data['destino'], "$")
            datos_cliente = tx_data.get('datos_cliente', 'Sin datos')
            text += (
                f"\n➖➖➖➖➖➖➖➖➖➖\n"
                f"💸 **MONTO:** {monto} {moneda}\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"📋 **DATOS:**\n"
                f"`{datos_cliente}`"
            )
        except: pass

    text += "\n\n_👇 Usa los botones para resolverla:_"

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📸 Ya funcionó", callback_data=f"ok_{tx_id}"), types.InlineKeyboardButton("🗑️ Anular", callback_data=f"anular_{tx_id}"))

    try:
        bot.edit_message_text(text, message_obj.chat.id, msg_id_to_edit, parse_mode="Markdown", reply_markup=kb)
    except Exception as e: print(f"Error edit msg: {e}")
    update_dashboard(message_obj.chat.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("anular_"))
def callback_anular(call):
    _, tx_id = call.data.split("_")
    user = call.from_user.first_name
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    supabase.table("transacciones").update({"status": "CANCELADA", "operator_username": user, "updated_at": now_utc_minus4_iso()}).eq("id", tx_id).execute()
    update_dashboard(call.message.chat.id)
    bot.answer_callback_query(call.id, "Anulada")

@bot.message_handler(content_types=['photo', 'document'])
def recibir_foto(message):
    if message.from_user.id not in operator_uploads:
        if message.chat.id == CHAT_ID_OPERADORES:
            bot.reply_to(message, "⚠️ Toca '📸 Adjuntar Foto' primero.")
        return
    if message.chat.id != CHAT_ID_OPERADORES: return 

    try:
        bot.send_chat_action(message.chat.id, 'upload_photo')
        tx_id = operator_uploads.pop(message.from_user.id)
        
        file_id = message.photo[-1].file_id if message.content_type == 'photo' else message.document.file_id
        
        # Guarda estado temporal para esperar confirmación del operador
        operator_confirmations[message.from_user.id] = {
            'tx_id': tx_id,
            'file_id': file_id,
            'msg_id': message.message_id,
            'reply_to_id': message.reply_to_message.message_id if message.reply_to_message else None
        }

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Confirmar y Finalizar", callback_data="img_out_ok"),
               types.InlineKeyboardButton("❌ Subir otra", callback_data="img_out_no"))

        bot.send_photo(message.chat.id, file_id, caption="👀 **¿Es correcto este comprobante de salida?**", reply_markup=kb, parse_mode="Markdown", reply_to_message_id=message.message_id)

    except Exception as e:
        print(f"❌ Error Foto: {traceback.format_exc()}")
        bot.reply_to(message, "❌ Error procesando imagen.")
        if 'tx_id' in locals(): operator_uploads[message.from_user.id] = tx_id

@bot.callback_query_handler(func=lambda c: c.data in ("img_out_ok", "img_out_no"))
def on_img_out_confirm(cb):
    user_id = cb.from_user.id
    chat_id = cb.message.chat.id

    if user_id not in operator_confirmations:
        bot.answer_callback_query(cb.id, "⚠️ Sesión expirada. Vuelve a adjuntar.")
        try: bot.delete_message(chat_id, cb.message.message_id)
        except: pass
        return

    conf = operator_confirmations.pop(user_id)
    tx_id = conf['tx_id']
    file_id = conf['file_id']

    try: bot.delete_message(chat_id, cb.message.message_id) # Borra el mensaje de pregunta
    except: pass

    if cb.data == "img_out_no":
        operator_uploads[user_id] = tx_id # Devuelve el permiso para subir foto
        bot.send_message(chat_id, "❌ Subida cancelada. Vuelve a enviar la foto correcta.")
        return

    # Si fue OK, ejecuta la finalización
    bot.answer_callback_query(cb.id, "Finalizando operación...")
    finalizar_operador_db(chat_id, user_id, cb.from_user.first_name, tx_id, file_id, conf['msg_id'], conf['reply_to_id'])

def finalizar_operador_db(chat_id, user_id, operator_name, tx_id, file_id, msg_to_del_1, msg_to_del_2):
    try:
        # 1. Actualizar DB
        r = supabase.table("transacciones").update({
            "status": "REALIZADA",
            "proof_image_id": file_id, # Guardamos el ID de Telegram directo
            "operator_username": operator_name,
            "updated_at": now_utc_minus4_iso()
        }).eq("id", tx_id).execute()

        if r.data:
            tx_data = r.data[0]
            group_msg_id = tx_data.get('group_message_id')
            cod_v = tx_data.get('codigo_transaccion', f"#{tx_id}")
            in_img = tx_data.get('input_image_id')

            # 2. Borrar mensajes del grupo de operadores
            if group_msg_id:
                try: bot.delete_message(chat_id, group_msg_id)
                except: pass
            try: bot.delete_message(chat_id, msg_to_del_1)
            except: pass
            if msg_to_del_2:
                try: bot.delete_message(chat_id, msg_to_del_2)
                except: pass

            # 3. Mandar paquete completo a Contabilidad / Logs
            if CHAT_ID_LOGS != 0:
                try:
                    msg_log = (
                        f"📑 **REGISTRO DE OPERACIÓN** | {cod_v}\n"
                        f"➖➖➖➖➖➖➖➖➖➖\n"
                        f"📥 **Entrada:** {tx_data.get('metodo_pago', 'N/A')}\n"
                        f"📤 **Salida:** {tx_data.get('tipo_operacion', 'N/A')}\n"
                        f"💸 Recibimos: {_fmt_num(tx_data.get('monto_envio'))} {PAIS_MONEDA.get(tx_data.get('origen'), '$')}\n"
                        f"💸 Enviamos: {_fmt_num(tx_data.get('monto_recibir'))} {PAIS_MONEDA.get(tx_data.get('destino'), '$')}\n"
                        f"➖➖➖➖➖➖➖➖➖➖\n"
                        f"📋 **DATOS BANCARIOS:**\n"
                        f"`{tx_data.get('datos_cliente', '')}`\n"
                        f"📝 **Obs:** {tx_data.get('observaciones', 'Ninguna')}\n"
                        f"👤 Operador: {operator_name}"
                    )
                    
                    # Mandamos el texto primero
                    bot.send_message(CHAT_ID_LOGS, msg_log, parse_mode="Markdown")
                    
                    # Mandamos comprobante de entrada (Si existía foto)
                    if in_img:
                        bot.send_photo(CHAT_ID_LOGS, in_img, caption=f"📥 Comprobante Entrada Cliente ({cod_v})")
                    
                    # Mandamos comprobante de salida (Foto operador)
                    bot.send_photo(CHAT_ID_LOGS, file_id, caption=f"📤 Comprobante Salida Operador ({cod_v})")

                except Exception as e: print(f"Error enviando a logs: {e}")

            # 4. Actualizar tablero de pendientes
            update_dashboard(chat_id)

    except Exception as e:
        print(f"Error en finalizar_operador_db: {e}")
        bot.send_message(chat_id, f"❌ Hubo un error al finalizar: {e}")

# ==========================================
# 7. OTROS COMANDOS
# ==========================================
@bot.message_handler(commands=['saldo'])
def saldo(m):
    if not es_chat_autorizado(m): return
    SALDO_STATE[m.chat.id] = {}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True); [kb.row(*paises[i:i+2]) for i in range(0,len(paises),2)]; kb.row(BTN_CANCEL)
    bot.send_message(m.chat.id, "País:", reply_markup=kb)
    bot.register_next_step_handler(m, lambda msg: [SALDO_STATE[m.chat.id].update({"pais": msg.text}), bot.send_message(m.chat.id, "Monto:", reply_markup=types.ReplyKeyboardRemove()), bot.register_next_step_handler(msg, lambda mm: [supabase.table("registros_saldos_capital").insert({"fecha": hoy_utc4_date_str(), "pais": SALDO_STATE[m.chat.id]["pais"], "monto_local": float(mm.text), "usuario_id": mm.from_user.id}).execute(), bot.send_message(m.chat.id, "✅ Guardado")])][0] if msg.text != BTN_CANCEL else None)

@bot.message_handler(commands=['resumen'])
def resumen(m):
    if not es_chat_autorizado(m): return
    r = supabase.table("saldos_diarios").select("*").eq("fecha", hoy_utc4_date_str()).execute()
    bot.send_message(m.chat.id, "📊 " + ("\n".join([f"{x['moneda']}: {x['ganancia_dia']}" for x in r.data]) if r.data else "Sin datos"))

@bot.message_handler(commands=['precargar'])
def precargar(m):
    if not es_chat_autorizado(m) or m.from_user.id not in ADMINS: return
    PRECARGA_STATE[m.chat.id]=True
    bot.send_message(m.chat.id, "Datos (Pais: monto):")
    bot.register_next_step_handler(m, precargar_procesar)

def precargar_procesar(m):
    if m.chat.id not in PRECARGA_STATE: return
    del PRECARGA_STATE[m.chat.id]
    for l in m.text.splitlines():
        if ":" in l:
            p, v = l.split(":", 1)
            try: actualizar_saldo_y_ledger(p.strip(), float(v.strip()) - get_saldo_actual(p.strip())[0], motivo="ajuste")
            except: pass
    bot.send_message(m.chat.id, "✅ Precarga lista")

@bot.message_handler(func=lambda m: True)
def fallback(m):
    # Print ID para debug
    print(f"📢 ID DEL GRUPO: {m.chat.id}")  
    if es_chat_autorizado(m): bot.reply_to(m, "❓ Usa /start")

print(f"🤖 SISTEMA V.I.P 5.4 ONLINE (ARCHIVO CENTRAL - SIN STORAGE)")
print(f"🔐 Bloqueado para grupo: {CHAT_ID_MATRIZ}")
bot.infinity_polling()
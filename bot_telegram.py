import threading
import time
from datetime import datetime, timedelta
import os
import sys
import logging
import re
import requests
import telebot
from dotenv import load_dotenv
from decimal import Decimal, ROUND_DOWN
import unicodedata

# === 1) CARGA .ENV ANTES DE TODO ===
load_dotenv(override=True)

# === 2) CONFIG BÁSICA ===
MODO_TEST = False
EXPECTED_BOT_USERNAME = (os.getenv("TASANATOR_USERNAME") or "TasanatorBot").lstrip("@")

# Toma el token de Tasanator primero; si no, cae a TELEGRAM_TOKEN para compat
RAW_TOKEN = os.getenv("TASANATOR_TOKEN") or os.getenv("TELEGRAM_TOKEN")

def clean_token(tok: str) -> str:
    if not tok:
        return tok
    tok = tok.strip()
    tok = "".join(ch for ch in tok if ch.isalnum() or ch in (":", "_", "-"))
    return tok

TOKEN = clean_token(RAW_TOKEN)
if not TOKEN or ":" not in TOKEN:
    print("❌ Token vacío o con formato inválido. Define TASANATOR_TOKEN (recomendado) o TELEGRAM_TOKEN en tu .env.")
    sys.exit(1)

# === 3) LOGS DE TELEGRAM ===
telebot.logger.setLevel(logging.DEBUG)

# === 4) PREFLIGHT ===
def preflight_getme(token: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        r = requests.get(url, timeout=15)
        print(f"🌐 Preflight getMe -> HTTP {r.status_code} | body={r.text}")
        if r.status_code != 200:
            print("❌ /getMe no respondió 200. Revisa el token.")
            sys.exit(1)
        js = r.json()
        if not js.get("ok"):
            print("❌ ok=false. Revisa el token o regenera en BotFather.")
            sys.exit(1)
        return js["result"]
    except Exception as e:
        print(f"❌ Error de red al llamar getMe(): {e}")
        sys.exit(1)

me_pre = preflight_getme(TOKEN)
print(f"✔️ Preflight OK: @{me_pre.get('username')} (id={me_pre.get('id')})")

if EXPECTED_BOT_USERNAME and str(me_pre.get("username", "")).lower() != EXPECTED_BOT_USERNAME.lower():
    print("❌ ERROR: El token NO corresponde al bot esperado.")
    print(f"   Esperado: @{EXPECTED_BOT_USERNAME} | Actual: @{me_pre.get('username')}")
    sys.exit(1)

# === 5) IMPORTS QUE USAN .ENV ===
from supabase import create_client, Client
from dateutil import parser
from guardar_tasas import actualizar_todas_las_tasas

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Faltan SUPABASE_URL o SUPABASE_KEY en tu .env.")
    sys.exit(1)

# === 6) PARSEO DE AUTORIZADOS / RESTRINGIDOS ===
def _parse_ids(raw: str):
    out = []
    for x in (raw or "").split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.append(int(x))
        except Exception:
            print(f"⚠️ ID inválido en USUARIOS_AUTORIZADOS: {x!r}")
    return out

def _parse_id_set(raw: str):
    out = set()
    for x in re.split(r"[,\s]+", (raw or "").strip()):
        if not x:
            continue
        try:
            out.add(int(x))
        except Exception:
            print(f"⚠️ ID inválido en lista: {x!r}")
    return out

USUARIOS_AUTORIZADOS   = _parse_ids(os.getenv("USUARIOS_AUTORIZADOS", ""))
USUARIOS_LIMITADOS     = _parse_id_set(os.getenv("USUARIO_LIMITADO", "794327412"))
USUARIOS_RESTRINGIDOS  = _parse_id_set(os.getenv("USUARIO_RESTRINGIDO", "7278912173"))
USUARIOS_SOLO_PUBLICO  = _parse_id_set(os.getenv("USUARIOS_SOLO_PUBLICO", ""))  # súper restricción

# === 7) CLIENTES ===
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TOKEN)

try:
    me = bot.get_me()
    print(f"🤖 Autenticado como @{me.username} (id={me.id}) — listo para arrancar.")
except Exception as e:
    print(f"❌ get_me() vía TeleBot falló: {e}")
    sys.exit(1)

def safe_remove_webhook(b: telebot.TeleBot):
    try:
        import inspect
        sig = None
        try:
            sig = inspect.signature(b.remove_webhook)
        except Exception:
            sig = None
        if sig and "drop_pending_updates" in sig.parameters:
            try:
                b.remove_webhook(drop_pending_updates=True)
                return
            except Exception as e:
                print(f"ℹ️ remove_webhook(drop_pending_updates=True) falló: {e}")
        try:
            b.remove_webhook()
        except Exception as e:
            print(f"ℹ️ remove_webhook() falló (seguimos a polling): {e}")
    except Exception as e:
        print(f"ℹ️ safe_remove_webhook: error no crítico: {e}")

safe_remove_webhook(bot)

print(f"Conectado a: {SUPABASE_URL}")
print("USUARIOS_AUTORIZADOS =", USUARIOS_AUTORIZADOS)

# ============ NORMALIZADORES + DECIMALES POR PAR (TRUNCADO) ============
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _norm_pair(p: str) -> str:
    """
    Normaliza pares a: 'origen - destino' en minúsculas, sin acentos,
    y con sinónimos: zelle->usa, euros->europa, panama->panamá, peru->perú, mexico->méxico, argentin->argentina.
    También acepta 'origen destino' o 'origen- destino' etc.
    """
    if not p:
        return ""
    s = p.strip().lower()
    s = s.replace("/", " ").replace("  ", " ")
    # uniformar separador
    if " - " in s:
        partes = s.split(" - ")
    else:
        partes = s.split("-")
    if len(partes) == 2:
        a, b = partes[0].strip(), partes[1].strip()
    else:
        # también soporta "origen destino" sin guion
        toks = s.split()
        if len(toks) >= 2:
            a, b = " ".join(toks[:-1]), toks[-1]
        else:
            a, b = s, ""

    def std(word: str) -> str:
        w = word.strip()
        w = w.replace("euros", "europa")
        w = w.replace("zelle", "usa")
        w = w.replace("panama", "panamá")
        w = w.replace("peru", "perú")
        w = w.replace("mexico", "méxico")
        w = w.replace("argentin", "argentina")
        return w

    a, b = std(a), std(b)
    # quitar acentos para la clave del mapa
    a_key = _strip_accents(a)
    b_key = _strip_accents(b)
    return f"{a_key} - {b_key}".strip()

# Mapa de decimales por par normalizado (SIN ACENTOS, minúsculas)
DECIMALS_BY_PAIR = {
    "chile - venezuela": 4,
    "chile - colombia": 3,
    "chile - argentina": 3,
    "chile - usa": 5,
    "colombia - venezuela": 2,
    "colombia - chile": 3,
    "usa - venezuela": 1,
    "usa - chile": 1,
    "mexico - venezuela": 2,
    "chile - mexico": 4,
    "chile - peru": 4,
    "argentina - peru": 4,
    "argentina - venezuela": 3,
    "colombia - argentina": 3,
    "venezuela - colombia": 2,
    "venezuela - argentina": 2,
    "venezuela - usa": 5,
    "usa - colombia": 2,
    "venezuela - peru": 4,
    "mexico - colombia": 1,
    "mexico - argentina": 2,
    "colombia - mexico": 3,
    "argentina - chile": 3,
    "colombia - peru": 5,
    "panama - venezuela": 1,
    "argentina - colombia": 2,
    "ecuador - colombia": 1,
    "ecuador - venezuela": 1,
    "europa - venezuela": 1,
    "europa - chile": 1,
    "usa - peru": 3,
    "usa - argentina": 3,
    "uruguay - venezuela": 3,
    "chile - panama": 5,
    "chile - ecuador": 5,
    # especial sin guion:
    "colombia usdt": 2,
}

def _truncate_value(val, decs):
    """
    Trunca sin redondeo a 'decs' decimales usando Decimal + ROUND_DOWN.
    Devuelve float (y lo formateamos como string con f-string para mantener decs).
    """
    if val is None:
        return None
    if decs is None:
        return float(val)
    q = Decimal("1." + ("0" * int(decs)))
    d = Decimal(str(val)).quantize(q, rounding=ROUND_DOWN)
    return float(d)

def _fmt_trunc(val, decs):
    """
    Devuelve string del valor truncado con exactamente 'decs' decimales.
    Si val es None -> 'No disponible'
    """
    if val is None:
        return "No disponible"
    if decs is None:
        return str(val)
    tv = _truncate_value(val, decs)
    return f"{tv:.{decs}f}"

# ============ EMOJIS Y PAISES DEL MENÚ ============
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
    "uruguay": "🇺🇾",   # NUEVO
}

# === 8) BOTONES / MENÚ ===
SPECIAL_COPUSDT_BTN = "💱 COP USDT"  # NUEVO botón especial

def generar_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    # Botón COP USDT como fila propia arriba
    markup.row(telebot.types.KeyboardButton(SPECIAL_COPUSDT_BTN))
    # Países en filas de a 2
    botones = [telebot.types.KeyboardButton(f"{emoji} {pais.title()}") for pais, emoji in emojis_paises.items()]
    for i in range(0, len(botones), 2):
        markup.row(*botones[i:i+2])
    return markup

def _hoy_iso_ve():
    return (datetime.utcnow() - timedelta(hours=4)).date().isoformat()

def _listar_tasas_hoy():
    response = supabase.table("tasas").select("nombre_tasa, fecha_actual, valor").order("fecha_actual", desc=True).execute()
    return response.data or []

def obtener_pares_disponibles(nombre_pais):
    hoy = _hoy_iso_ve()
    data = _listar_tasas_hoy()
    pais_l = nombre_pais.lower()
    pares = set()
    for t in data:
        nt = (t.get("nombre_tasa") or "").lower()
        fa = t.get("fecha_actual") or ""
        if ("tasa full" in nt and "promedio" not in nt and fa.startswith(hoy) and pais_l in nt):
            par = t["nombre_tasa"].replace("Tasa full ", "")
            pares.add(par)

    # Si es Colombia y existe COP USDT hoy, añadirlo también
    if pais_l == "colombia":
        for t in data:
            nt = (t.get("nombre_tasa") or "").lower()
            fa = t.get("fecha_actual") or ""
            if fa.startswith(hoy) and nt in ("tasa full cop usdt", "tasa mayorista cop usdt",
                                             "tasa público cop usdt", "tasa público promedio cop usdt",
                                             "tasa mayorista promedio cop usdt", "tasa full promedio cop usdt"):
                pares.add("COP USDT")
                break

    return sorted(list(pares))

# === 9) CONSULTAS ===
def _buscar_valor_hoy(data, nombre_tasa_lower, hoy_iso):
    for row in data:
        if (row.get("nombre_tasa") or "").lower() == nombre_tasa_lower and (row.get("fecha_actual") or "").startswith(hoy_iso):
            valor = float(row["valor"])
            hora = parser.isoparse(row["fecha_actual"])
            return valor, hora.strftime("%H:%M")
    return None, None

def _card_cop_usdt_full_may(pair_key_norm, full_act, full_prom, may_act, may_prom, hora):
    line = "─" * 28
    # aplicar truncados:
    decs = DECIMALS_BY_PAIR.get(pair_key_norm)  # 'colombia usdt'
    f_act  = _fmt_trunc(full_act, decs)
    f_prom = _fmt_trunc(full_prom, decs) if full_prom is not None else "No disponible"
    m_act  = _fmt_trunc(may_act, decs)
    m_prom = _fmt_trunc(may_prom, decs) if may_prom is not None else "No disponible"

    return (
        f"┌{line}┐\n"
        f"│   💱  COP → USDT (P2P)        │\n"
        f"├{line}┤\n"
        f"│  • Tasa Full Actual: {f_act}        │\n"
        f"│  • Tasa Full Promedio: {f_prom} │\n"
        f"│  • Tasa Mayorista Actual: {m_act}     │\n"
        f"│  • Tasa Mayorista Promedio: {m_prom} │\n"
        f"├{line}┤\n"
        f"│  🕒 Última actualización: {hora}   │\n"
        f"└{line}┘"
    )

def _card_cop_usdt_may_only(pair_key_norm, may_act, may_prom, hora):
    line = "─" * 28
    decs = DECIMALS_BY_PAIR.get(pair_key_norm)
    m_act  = _fmt_trunc(may_act, decs)
    m_prom = _fmt_trunc(may_prom, decs) if may_prom is not None else "No disponible"

    return (
        f"┌{line}┐\n"
        f"│   💱  COP → USDT (P2P)        │\n"
        f"├{line}┤\n"
        f"│  • Tasa Mayorista Actual: {m_act}     │\n"
        f"│  • Tasa Mayorista Promedio: {m_prom} │\n"
        f"├{line}┤\n"
        f"│  🕒 Última actualización: {hora}   │\n"
        f"└{line}┘"
    )

def _apply_fmt_pair_lines(pair_key_norm, lines: list[tuple[str, float | None]]):
    """
    Recibe una lista de (label, value) y devuelve líneas con valores truncados por par.
    """
    decs = DECIMALS_BY_PAIR.get(pair_key_norm)
    out = []
    for label, val in lines:
        out.append(f"{label}: {_fmt_trunc(val, decs)}")
    return "\n".join(out)

def obtener_tasas_par(nombre_par, user_id):
    try:
        ahora = datetime.utcnow() - timedelta(hours=4)
        if ahora.hour < 9:
            return "🕒 Actualmente estamos fuera de horario laboral (9:00 a.m. - 9:00 p.m.). Por favor, consulta más tarde."

        resp = supabase.table("tasas").select("*").order("fecha_actual", desc=True).execute()
        data = resp.data or []
        hoy = ahora.date().isoformat()

        # --- Par único COP USDT con “recuadro” ---
        norm = nombre_par.strip().lower().replace("/", " ").replace("  ", " ")
        # clave normalizada para decimales
        pair_key_norm = _norm_pair(nombre_par if norm != "cop usdt" else "colombia usdt")

        if norm == "cop usdt":
            full_act, hora = _buscar_valor_hoy(data, "tasa full cop usdt", hoy)
            may_act,  _    = _buscar_valor_hoy(data, "tasa mayorista cop usdt", hoy)
            full_prom, _   = _buscar_valor_hoy(data, "tasa full promedio cop usdt", hoy)
            may_prom, _    = _buscar_valor_hoy(data, "tasa mayorista promedio cop usdt", hoy)

            if full_act is None and may_act is None:
                return "❌ No hay datos disponibles para COP USDT."

            # súper restricción (solo Público) -> no aplica público aquí
            if user_id in USUARIOS_SOLO_PUBLICO:
                decs = DECIMALS_BY_PAIR.get(pair_key_norm)
                return (
                    "┌────────────────────────────┐\n"
                    "│   💱  COP → USDT (P2P)     │\n"
                    "├────────────────────────────┤\n"
                    f"│  • Tasa Público: No disponible           │\n"
                    f"│  • Tasa Público Promedio: No disponible  │\n"
                    "├────────────────────────────┤\n"
                    f"│  🕒 Última actualización: {hora or '--:--'}   │\n"
                    "└────────────────────────────┘"
                )

            # limitados/restringidos -> solo mayorista
            if (user_id in USUARIOS_LIMITADOS) or (user_id in USUARIOS_RESTRINGIDOS):
                if may_act is None:
                    return "❌ No hay datos disponibles para COP USDT."
                return _card_cop_usdt_may_only(pair_key_norm, may_act, may_prom, hora or "--:--")

            # sin restricción -> full + mayorista
            if full_act is None or may_act is None:
                return "❌ No hay datos suficientes disponibles para COP USDT."
            return _card_cop_usdt_full_may(pair_key_norm, full_act, full_prom, may_act, may_prom, hora or "--:--")

        # --- Flujo normal de pares con " - " ---
        def buscar(n): return _buscar_valor_hoy(data, n.lower(), hoy)
        tasa_full_actual, hora_actual = buscar(f"Tasa full {nombre_par}")
        tasa_full_prom, _             = buscar(f"Tasa full promedio {nombre_par}")
        tasa_pub_actual, _            = buscar(f"Tasa público {nombre_par}")
        tasa_pub_prom, _              = buscar(f"Tasa público promedio {nombre_par}")
        tasa_may_actual, _            = buscar(f"Tasa mayorista {nombre_par}")
        tasa_may_prom, _              = buscar(f"Tasa mayorista promedio {nombre_par}")

        # clave normalizada para decimales (ej: "chile - venezuela")
        pair_key_norm = _norm_pair(nombre_par)

        if user_id in USUARIOS_SOLO_PUBLICO:
            if tasa_pub_actual is None:
                return "❌ No hay datos disponibles para ese par."
            cuerpo = _apply_fmt_pair_lines(pair_key_norm, [
                ("Tasa Público Actual", tasa_pub_actual),
                ("Tasa Público Promedio", tasa_pub_prom),
            ])
            return (
                f"📊 Tasas para {nombre_par}\n\n"
                f"{cuerpo}\n\n"
                f"🕒 Última actualización de datos: {hora_actual}"
            )

        if (user_id in USUARIOS_LIMITADOS) or (user_id in USUARIOS_RESTRINGIDOS):
            if tasa_pub_actual is None or tasa_may_actual is None:
                return "❌ No hay datos disponibles para ese par."
            cuerpo = _apply_fmt_pair_lines(pair_key_norm, [
                ("Tasa Mayorista Actual", tasa_may_actual),
                ("Tasa Mayorista Promedio", tasa_may_prom),
                ("Tasa Público Actual", tasa_pub_actual),
                ("Tasa Público Promedio", tasa_pub_prom),
            ])
            return (
                f"📊 Tasas para {nombre_par}\n\n"
                f"{cuerpo}\n\n"
                f"🕒 Última actualización de datos: {hora_actual}"
            )

        if tasa_full_actual is None or tasa_pub_actual is None or tasa_may_actual is None:
            return "❌ No hay datos suficientes disponibles para ese par."

        cuerpo = _apply_fmt_pair_lines(pair_key_norm, [
            ("Tasa Full Actual", tasa_full_actual),
            ("Tasa Full Promedio", tasa_full_prom),
            ("Tasa Mayorista Actual", tasa_may_actual),
            ("Tasa Mayorista Promedio", tasa_may_prom),
            ("Tasa Público Actual", tasa_pub_actual),
            ("Tasa Público Promedio", tasa_pub_prom),
        ])
        return (
            f"📊 Tasas para {nombre_par}\n\n"
            f"{cuerpo}\n\n"
            f"🕒 Última actualización de datos: {hora_actual}"
        )
    except Exception as e:
        return f"❌ Error obteniendo tasas: {e}"

def autorizado(message):
    ok = message.from_user.id in USUARIOS_AUTORIZADOS
    print(f"[auth] from={message.from_user.id} autorizado={ok}")
    if not ok:
        try:
            bot.reply_to(message, "⛔️ Acceso restringido. No estás autorizado.")
        except Exception as e:
            print(f"⚠️ No pude responder rechazo de auth: {e}")
        return False
    return True

# === 10) COMANDOS ===
@bot.message_handler(commands=["id"])
def cmd_id(message):
    bot.reply_to(message, f"🆔 chat_id: {message.chat.id}\n👤 user_id: {message.from_user.id}")

@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    bot.reply_to(message, "🏓 pong")

@bot.message_handler(commands=["copusdt"])
def cmd_copusdt(message):
    if not autorizado(message):
        return
    msg = obtener_tasas_par("COP USDT", message.from_user.id)
    bot.send_message(message.chat.id, msg)

# === MENÚ / START ===
@bot.message_handler(commands=["start"])
@bot.message_handler(commands=["tasas"])
@bot.message_handler(func=lambda m: (m.text or "").strip().lower() == "tasas")
def mostrar_menu(message):
    print(f"[menu] from={message.from_user.id} chat={message.chat.id}")
    if not autorizado(message):
        return
    bienvenida = "🔔 Selecciona un país o usa el acceso rápido:"
    bot.send_message(message.chat.id, bienvenida, reply_markup=generar_menu())

# === MANEJO MENSAJES ===
@bot.message_handler(func=lambda message: True)
def manejar_mensaje(message):
    texto = (message.text or "").strip()
    print(f"[msg] from={message.from_user.id} chat={message.chat.id} text={texto!r}")
    if not autorizado(message):
        return
    texto_l = texto.lower()

    # Botón especial COP USDT
    if texto == SPECIAL_COPUSDT_BTN or texto_l.replace("/", " ").replace("  ", " ") == "cop usdt":
        mensaje = obtener_tasas_par("COP USDT", message.from_user.id)
        bot.send_message(message.chat.id, mensaje)
        return

    if " - " in texto:
        mensaje = obtener_tasas_par(texto.strip(), message.from_user.id)
        bot.send_message(message.chat.id, mensaje)
        return

    # Búsqueda por país desde el menú
    for pais in emojis_paises:
        if pais in texto_l:
            pares = obtener_pares_disponibles(pais)
            if pares:
                markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
                for par in pares:
                    markup.add(telebot.types.KeyboardButton(par))
                bot.send_message(message.chat.id, f"🔍 Elige un par disponible con {pais.title()}:", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, f"❌ No se encontraron pares con {pais.title()}.")
            return

    bot.send_message(message.chat.id, "❌ Comando no reconocido. Escribe /tasas, /copusdt o selecciona una opción.")

# === 11) ACTUALIZACIÓN PERIÓDICA ===
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

# === 12) INICIO ===
print("✅ Modo:", "TEST" if MODO_TEST else "PRODUCCIÓN (9:00–21:00, cada hora)")
threading.Thread(target=actualizar_periodicamente, daemon=True).start()
print("🤖 Bot escuchando...")
bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)

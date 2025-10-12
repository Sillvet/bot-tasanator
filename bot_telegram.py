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

# === 1) CARGA .ENV ANTES DE TODO ===
load_dotenv(override=True)

# === 2) CONFIG BÁSICA ===
MODO_TEST = False
EXPECTED_BOT_USERNAME = (os.getenv("TASANATOR_USERNAME") or "TasanatorBot").lstrip("@")

# Toma el token de Tasanator primero; si no, cae a TELEGRAM_TOKEN para compat
RAW_TOKEN = os.getenv("TASANATOR_TOKEN") or os.getenv("TELEGRAM_TOKEN")

def clean_token(tok: str) -> str:
    """
    Limpia el token:
      - recorta espacios/saltos
      - deja solo [A-Za-z0-9:_-]
    """
    if not tok:
        return tok
    tok = tok.strip()
    # elimina caracteres invisibles (BOM/ZWSP) y cualquier cosa fuera del set permitido
    tok = "".join(ch for ch in tok if ch.isalnum() or ch in (":", "_", "-"))
    return tok

TOKEN = clean_token(RAW_TOKEN)
if not TOKEN or ":" not in TOKEN:
    print("❌ Token vacío o con formato inválido. Define TASANATOR_TOKEN (recomendado) o TELEGRAM_TOKEN en tu .env.")
    sys.exit(1)

# === 3) LOGS DE TELEGRAM (útil para diagnosis) ===
telebot.logger.setLevel(logging.DEBUG)

# === 4) PREFLIGHT: verifica el token directamente con requests ===
def preflight_getme(token: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        r = requests.get(url, timeout=15)
        print(f"🌐 Preflight getMe -> HTTP {r.status_code} | body={r.text}")
        if r.status_code != 200:
            print("❌ El endpoint /getMe no respondió 200. Revisa el token (espacios ocultos, token equivocado o revocado).")
            sys.exit(1)
        js = r.json()
        if not js.get("ok"):
            print("❌ Respuesta ok=false. Revisa el token o regenera en BotFather.")
            sys.exit(1)
        return js["result"]
    except Exception as e:
        print(f"❌ Error de red al llamar getMe(): {e}")
        sys.exit(1)

me_pre = preflight_getme(TOKEN)
print(f"✔️ Preflight OK: @{me_pre.get('username')} (id={me_pre.get('id')})")

# Validación de que sea Tasanator
if EXPECTED_BOT_USERNAME and str(me_pre.get("username", "")).lower() != EXPECTED_BOT_USERNAME.lower():
    print("❌ ERROR: El token NO corresponde al bot esperado.")
    print(f"   Esperado: @{EXPECTED_BOT_USERNAME} | Actual: @{me_pre.get('username')}")
    print("   -> Corrige TASANATOR_TOKEN en .env (o ajusta TASANATOR_USERNAME si renombraste el bot).")
    sys.exit(1)

# === 5) AHORA SÍ: IMPORTS QUE USAN .ENV ===
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
    # acepta: "123", "123,456", " 123  ,  456 \n789 "
    out = set()
    for x in re.split(r"[,\s]+", (raw or "").strip()):
        if not x:
            continue
        try:
            out.add(int(x))
        except Exception:
            print(f"⚠️ ID inválido en lista: {x!r}")
    return out

USUARIOS_AUTORIZADOS = _parse_ids(os.getenv("USUARIOS_AUTORIZADOS", ""))
USUARIOS_LIMITADOS = _parse_id_set(os.getenv("USUARIO_LIMITADO", "794327412"))
USUARIOS_RESTRINGIDOS = _parse_id_set(os.getenv("USUARIO_RESTRINGIDO", "7278912173"))
# --- NUEVO: súper restricción (solo Público actual + promedio) ---
USUARIOS_SOLO_PUBLICO = _parse_id_set(os.getenv("USUARIOS_SOLO_PUBLICO", ""))

# === 7) CLIENTES ===
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TOKEN)

# Validación con la lib también (por si el preflight fue ok y aquí falla)
try:
    me = bot.get_me()
    print(f"🤖 Autenticado como @{me.username} (id={me.id}) — listo para arrancar.")
except Exception as e:
    print(f"❌ get_me() vía TeleBot falló: {e}")
    sys.exit(1)

# Quitar webhook de forma segura (no bloqueante si falla)
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

# === 8) FUNCIONES DE BOT ===
def generar_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    botones = [telebot.types.KeyboardButton(f"{emoji} {pais.title()}") for pais, emoji in emojis_paises.items()]
    for i in range(0, len(botones), 2):
        markup.row(*botones[i:i+2])
    return markup

def obtener_pares_disponibles(nombre_pais):
    hoy = (datetime.utcnow() - timedelta(hours=4)).date().isoformat()
    response = supabase.table("tasas").select("nombre_tasa, fecha_actual").order("fecha_actual", desc=True).execute()
    data = response.data or []
    pares = set()
    for t in data:
        nt = (t.get("nombre_tasa") or "").lower()
        fa = t.get("fecha_actual") or ""
        if ("tasa full" in nt and "promedio" not in nt and fa.startswith(hoy) and nombre_pais.lower() in nt):
            par = t["nombre_tasa"].replace("Tasa full ", "")
            pares.add(par)
    return sorted(list(pares))

def obtener_tasas_par(nombre_par, user_id):
    try:
        ahora = datetime.utcnow() - timedelta(hours=4)
        hora_actual_num = ahora.hour
        hoy = ahora.date().isoformat()
        if hora_actual_num < 9:
            return "🕒 Actualmente estamos fuera de horario laboral (9:00 a.m. - 9:00 p.m.). Por favor, consulta más tarde."
        response = supabase.table("tasas").select("*").order("fecha_actual", desc=True).execute()
        data = response.data or []
        def buscar_valor(nombre_tasa):
            for row in data:
                if (row.get("nombre_tasa") or "").lower() == nombre_tasa.lower() and (row.get("fecha_actual") or "").startswith(hoy):
                    valor = float(row["valor"])
                    hora = parser.isoparse(row["fecha_actual"])
                    return valor, hora.strftime("%H:%M")
            return None, None
        tasa_full_actual, hora_actual = buscar_valor(f"Tasa full {nombre_par}")
        tasa_full_prom, _ = buscar_valor(f"Tasa full promedio {nombre_par}")
        tasa_pub_actual, _ = buscar_valor(f"Tasa público {nombre_par}")
        tasa_pub_prom, _ = buscar_valor(f"Tasa público promedio {nombre_par}")
        tasa_may_actual, _ = buscar_valor(f"Tasa mayorista {nombre_par}")
        tasa_may_prom, _ = buscar_valor(f"Tasa mayorista promedio {nombre_par}")

        # ---- NUEVO: súper restricción (solo Público) ----
        if user_id in USUARIOS_SOLO_PUBLICO:
            if tasa_pub_actual is None:
                return "❌ No hay datos disponibles para ese par."
            return (
                f"📊 Tasas para {nombre_par}\n\n"
                f"Tasa Público Actual: {tasa_pub_actual}\n"
                f"Tasa Público Promedio: {tasa_pub_prom if tasa_pub_prom is not None else 'No disponible'}\n\n"
                f"🕒 Última actualización de datos: {hora_actual}"
            )

        # ---- Limitados/restringidos: Público + Mayorista (como antes) ----
        if (user_id in USUARIOS_LIMITADOS) or (user_id in USUARIOS_RESTRINGIDOS):
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

        # ---- Usuarios sin restricción ----
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
    ok = message.from_user.id in USUARIOS_AUTORIZADOS
    print(f"[auth] from={message.from_user.id} autorizado={ok}")
    if not ok:
        try:
            bot.reply_to(message, "⛔️ Acceso restringido. No estás autorizado.")
        except Exception as e:
            print(f"⚠️ No pude responder rechazo de auth: {e}")
        return False
    return True

# === 9) COMANDOS DIAGNÓSTICO ===
@bot.message_handler(commands=["id"])
def cmd_id(message):
    bot.reply_to(message, f"🆔 chat_id: {message.chat.id}\n👤 user_id: {message.from_user.id}")

@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    bot.reply_to(message, "🏓 pong")

# === MENÚ / START ===
@bot.message_handler(commands=["start"])
@bot.message_handler(commands=["tasas"])
@bot.message_handler(func=lambda m: (m.text or "").strip().lower() == "tasas")
def mostrar_menu(message):
    print(f"[menu] from={message.from_user.id} chat={message.chat.id}")
    if not autorizado(message):
        return
    bienvenida = "🔔 Selecciona un país para ver los pares disponibles:"
    bot.send_message(message.chat.id, bienvenida, reply_markup=generar_menu())

# === MANEJO MENSAJES ===
@bot.message_handler(func=lambda message: True)
def manejar_mensaje(message):
    texto = (message.text or "").strip()
    print(f"[msg] from={message.from_user.id} chat={message.chat.id} text={texto!r}")
    if not autorizado(message):
        return
    texto_l = texto.lower()
    if " - " in texto:
        mensaje = obtener_tasas_par(texto.strip(), message.from_user.id)
        bot.send_message(message.chat.id, mensaje)
        return
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
    bot.send_message(message.chat.id, "❌ Comando no reconocido. Escribe /tasas o selecciona un país.")

# === 10) ACTUALIZACIÓN PERIÓDICA ===
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

# === 11) INICIO ===
print("✅ Modo:", "TEST" if MODO_TEST else "PRODUCCIÓN (9:00–21:00, cada hora)")
threading.Thread(target=actualizar_periodicamente, daemon=True).start()
print("🤖 Bot escuchando...")
bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)

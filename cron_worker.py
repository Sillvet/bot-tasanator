import os
import time
from datetime import datetime, timedelta
import pytz
import subprocess, sys

def ensure_playwright_browsers():
    # Verifica que el CLI existe y descarga chromium si hace falta (sin --with-deps)
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as e:
        print(f"⚠️ playwright CLI no disponible: {e}")
        # Último intento simple (sin --with-deps)
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )

ensure_playwright_browsers()

from guardar_tasas import actualizar_todas_las_tasas

try:
    from guardar_tasas import actualizar_todas_las_tasas
except Exception as e:
    print(f"⚠️ No se pudo importar actualizar_todas_las_tasas desde guardar_tasas: {e}")
    raise

# === Config desde entorno ===
# Lee TZ desde 'TZ' (Render) o 'TZ_NAME' (fallback)
TZ_NAME = os.getenv("TZ") or os.getenv("TZ_NAME", "America/Caracas")

# DEFAULT ahora 60 min (si CRON_INTERVAL_MIN no está en ENV, corre cada 1 hora)
CRON_INTERVAL_MIN = int(os.getenv("CRON_INTERVAL_MIN", "60"))

FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in ("1", "true", "yes", "on")

# Ventana configurable + modo siempre activo
WINDOW_START_HOUR = int(os.getenv("WINDOW_START_HOUR", "9"))
WINDOW_END_HOUR   = int(os.getenv("WINDOW_END_HOUR", "21"))   # inclusive
ALWAYS_ON = os.getenv("ALWAYS_ON", "").strip().lower() in ("1","true","yes","on")

INTERVAL = timedelta(minutes=CRON_INTERVAL_MIN)

def local_now(tzname=TZ_NAME):
    tz = pytz.timezone(tzname)
    return datetime.now(tz)

def in_window(dt):
    # Si activas ALWAYS_ON, ignoramos la ventana (para correr 24/7)
    if ALWAYS_ON:
        return True
    return WINDOW_START_HOUR <= dt.hour <= WINDOW_END_HOUR  # inclusive

def align_to_next_tick(dt):
    base = dt.replace(second=0, microsecond=0)
    # alinear al próximo múltiplo del intervalo
    next_min = ((dt.minute // CRON_INTERVAL_MIN) + 1) * CRON_INTERVAL_MIN
    if next_min >= 60:
        candidate = (base.replace(minute=0) + timedelta(hours=1))
    else:
        candidate = base.replace(minute=next_min)
    # si usamos ventana y cae fuera, muévelo a la apertura siguiente
    if not ALWAYS_ON:
        if candidate.hour < WINDOW_START_HOUR:
            candidate = candidate.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
        elif candidate.hour > WINDOW_END_HOUR:
            candidate = (candidate + timedelta(days=1)).replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    return candidate

def next_window_open(dt):
    if ALWAYS_ON:
        return align_to_next_tick(dt)
    today_open = dt.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    if dt <= today_open:
        return today_open
    return (dt + timedelta(days=1)).replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)

if __name__ == "__main__":
    print(f"⏱️ Cron activo. Ventana: "
          f"{'SIEMPRE' if ALWAYS_ON else f'{WINDOW_START_HOUR}:00–{WINDOW_END_HOUR}:00'} {TZ_NAME} "
          f"| intervalo={CRON_INTERVAL_MIN}min | FORCE_RUN={FORCE_RUN}")

    now = local_now()
    next_run = now if FORCE_RUN else (align_to_next_tick(now) if in_window(now) else next_window_open(now))

    while True:
        try:
            now = local_now()

            if not in_window(now):
                nr = next_window_open(now)
                if nr != next_run:
                    next_run = nr
                    print(f"⏸️ Fuera de ventana ({now.strftime('%H:%M')}), próximo run: {next_run.isoformat()}")
                time.sleep(60)
                continue

            if FORCE_RUN or now >= next_run:
                print(f"🔄 Ejecutando actualización {now.isoformat()}")
                try:
                    actualizar_todas_las_tasas()
                except Exception as e:
                    print(f"❌ Error en bucle principal: {e}")
                base = now if now > next_run else next_run
                next_run = align_to_next_tick(base)
                FORCE_RUN = False
                print(f"🗓️ Próxima ejecución programada: {next_run.isoformat()}")
                time.sleep(2)
            else:
                secs = max(1, min(30, int((next_run - now).total_seconds())))
                time.sleep(secs)

        except Exception as e:
            print(f"⚠️ Loop error: {e}")
            time.sleep(10)

import os
import time
from datetime import datetime, timedelta
import pytz

# Importa la tarea
try:
    from guardar_tasas import actualizar_todas_las_tasas
except Exception as e:
    print(f"⚠️ No se pudo importar actualizar_todas_las_tasas desde guardar_tasas: {e}")
    raise

# === Config desde entorno ===
TZ_NAME = os.getenv("TZ_NAME", "America/Caracas")
CRON_INTERVAL_MIN = int(os.getenv("CRON_INTERVAL_MIN", "15"))  # default 15
FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in ("1", "true", "yes", "on")

INTERVAL = timedelta(minutes=CRON_INTERVAL_MIN)

def local_now(tzname=TZ_NAME):
    tz = pytz.timezone(tzname)
    return datetime.now(tz)

def in_window(dt):
    # Ventana 09:00–21:00 (inclusive)
    return 9 <= dt.hour <= 21

def align_to_next_tick(dt):
    """Devuelve el próximo múltiplo de CRON_INTERVAL_MIN (segundos=0)."""
    base = dt.replace(second=0, microsecond=0)
    next_min = ((dt.minute // CRON_INTERVAL_MIN) + 1) * CRON_INTERVAL_MIN
    if next_min >= 60:
        candidate = (base.replace(minute=0) + timedelta(hours=1))
    else:
        candidate = base.replace(minute=next_min)
    # Si cae fuera de ventana, lo movemos a la siguiente apertura (hoy o mañana)
    if candidate.hour < 9:
        candidate = candidate.replace(hour=9, minute=0, second=0, microsecond=0)
    elif candidate.hour > 21:
        # siguiente día a las 09:00
        candidate = (candidate + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return candidate

def next_window_open(dt):
    """Primera marca de la ventana (09:00) hoy o mañana según corresponda."""
    today_open = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if dt <= today_open:
        return today_open
    return (dt + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

if __name__ == "__main__":
    print(f"⏱️ Cron activo. Ventana: 9:00–21:00 {TZ_NAME} | intervalo={CRON_INTERVAL_MIN}min | FORCE_RUN={FORCE_RUN}")

    now = local_now()
    # Programa el primer next_run
    if FORCE_RUN:
        next_run = now  # dispara ya y luego reprograma normal
    else:
        # Si estamos fuera de ventana, agenda a la próxima apertura
        next_run = align_to_next_tick(now) if in_window(now) else next_window_open(now)

    while True:
        try:
            now = local_now()

            # Si estamos fuera de ventana, reprograma a la próxima apertura y duerme un rato
            if not in_window(now):
                if next_run != next_window_open(now):
                    next_run = next_window_open(now)
                    print(f"⏸️ Fuera de ventana ({now.strftime('%H:%M')}), próximo run: {next_run.isoformat()}")
                time.sleep(60)
                continue

            # Disparo: cuando now >= next_run (o si quedó FORCE_RUN activo al arrancar)
            if FORCE_RUN or now >= next_run:
                print(f"🔄 Ejecutando actualización {now.isoformat()}")
                try:
                    actualizar_todas_las_tasas()
                except Exception as e:
                    print(f"❌ Error en bucle principal: {e}")
                # Reprograma siguiente run exacto a intervalos fijos
                base = now if now > next_run else next_run
                next_run = align_to_next_tick(base)
                FORCE_RUN = False  # consumir flag
                print(f"🗓️ Próxima ejecución programada: {next_run.isoformat()}")
                # Pequeño sleep para evitar doble disparo en el mismo instante
                time.sleep(2)
            else:
                # Duerme de forma cooperativa hasta aprox. el próximo tick (máx 30s)
                secs = max(1, min(30, int((next_run - now).total_seconds())))
                time.sleep(secs)

        except Exception as e:
            print(f"⚠️ Loop error: {e}")
            time.sleep(10)

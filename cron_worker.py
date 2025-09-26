import os
import time
from datetime import datetime, timedelta
import pytz

# === Config desde entorno ===
TZ_NAME = "America/Caracas"
CRON_INTERVAL_MIN = int(os.getenv("CRON_INTERVAL_MIN", "15"))  # default 15
FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in ("1", "true", "yes", "on")

# --- lo demás de tus imports / funciones auxiliares puede ir aquí (si los tienes) ---
# from guardar_tasas import actualizar_todas_las_tasas  # asegúrate de tener este import en tu archivo real

def local_now(tzname=TZ_NAME):
    tz = pytz.timezone(tzname)
    return datetime.now(tz)

def in_window(dt):
    # Ventana 9:00–21:00 inclusive
    return 9 <= dt.hour <= 21

def is_tick(dt):
    # Dispara a los minutos múltiplos del intervalo, en segundo 0
    return (dt.minute % CRON_INTERVAL_MIN == 0) and (dt.second == 0)

def seconds_until_next_tick(dt):
    # Próximo múltiplo de CRON_INTERVAL_MIN
    base = dt.replace(second=0, microsecond=0)
    next_min = ((dt.minute // CRON_INTERVAL_MIN) + 1) * CRON_INTERVAL_MIN
    if next_min >= 60:
        nxt = base.replace(minute=0) + timedelta(hours=1)
    else:
        nxt = base.replace(minute=next_min)
    return max(1, int((nxt - dt).total_seconds()))

if __name__ == "__main__":
    print(f"⏱️ Cron activo. Ventana: 9:00–21:00 {TZ_NAME} | intervalo={CRON_INTERVAL_MIN}min | FORCE_RUN={FORCE_RUN}")
    while True:
        now = local_now(TZ_NAME)

        if not in_window(now):
            # Fuera de ventana: duerme 60s y reintenta
            time.sleep(60)
            continue

        if FORCE_RUN or is_tick(now):
            try:
                print(f"🔄 Ejecutando actualización {now.isoformat()}")
                actualizar_todas_las_tasas()  # <- tu función existente
            except Exception as e:
                print(f"❌ Error en bucle principal: {e}")
            finally:
                # Una vez consumido, apágalo para no disparar en cada loop
                FORCE_RUN = False
                # Evita doble disparo en el mismo segundo
                time.sleep(2)
        else:
            # Duerme hasta el próximo tick, con límite de 30s para logs más “vivos”
            time.sleep(min(30, seconds_until_next_tick(now)))

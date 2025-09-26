import time
from datetime import datetime, timedelta
import pytz

# ... lo que ya tienes arriba ...

TICK_MINUTES = 15  # ← cada 15 minutos

def local_now(tzname="America/Caracas"):
    tz = pytz.timezone(tzname)
    # si aún usas utcnow + astimezone, mantenlo; esto es equivalente claro:
    return datetime.now(tz)

def in_window(dt):
    # Mantén tu ventana (9–21); ajusta si ya tienes otra función similar
    return 9 <= dt.hour <= 21

def is_tick(dt):
    # Dispara a los :00, :15, :30, :45 en segundo 0
    return (dt.minute % TICK_MINUTES == 0) and (dt.second == 0)

def seconds_until_next_tick(dt):
    # Calcula cuántos segundos faltan para el próximo múltiplo de 15
    base = dt.replace(second=0, microsecond=0)
    next_min = ((dt.minute // TICK_MINUTES) + 1) * TICK_MINUTES
    if next_min >= 60:
        nxt = base.replace(minute=0) + timedelta(hours=1)
    else:
        nxt = base.replace(minute=next_min)
    return max(1, int((nxt - dt).total_seconds()))

if __name__ == "__main__":
    print("⏱️ Cron activo. Ventana: 9:00–21:00 America/Caracas | intervalo=15min")
    while True:
        now = local_now("America/Caracas")

        if not in_window(now):
            # Fuera de ventana: duerme 60s y reintenta
            time.sleep(60)
            continue

        if FORCE_RUN or is_tick(now):
            try:
                print(f"🔄 Ejecutando actualización {now.isoformat()}")
                actualizar_todas_las_tasas()
            except Exception as e:
                print(f"❌ Error en bucle principal: {e}")
            finally:
                FORCE_RUN = False
                # Evita doble disparo en el mismo segundo
                time.sleep(2)
        else:
            # Duerme hasta el próximo cuarto de hora (máx 30s para logs más “vivos” si prefieres)
            time.sleep(min(30, seconds_until_next_tick(now)))

# cron_worker.py
import os, sys, time, signal, traceback
from datetime import datetime

def log(msg: str):
    print(msg, flush=True)

log(f"🚀 cron_worker arrancando | Python: {sys.version}")
try:
    log(f"📂 CWD: {os.getcwd()}")
    try:
        log(f"📄 Files: {os.listdir('.')}")
    except Exception as e:
        log(f"⚠️ No pude listar archivos: {e}")

    # Zona horaria
    TZ = os.getenv("TZ", "America/Caracas")
    os.environ["TZ"] = TZ
    try:
        import pytz
        tz = pytz.timezone(TZ)
        log(f"🕒 Zona horaria: {TZ}")
    except Exception:
        log("❌ Error importando pytz:")
        log(traceback.format_exc())
        raise

    # Import diferido para ver fallos reales
    try:
        from guardar_tasas import actualizar_todas_las_tasas
    except Exception:
        log("❌ Error importando guardar_tasas.actualizar_todas_las_tasas:")
        log(traceback.format_exc())
        raise

    # Ventana horaria (inclusive) y fuerza de corrida inicial
    RUN_START = int(os.getenv("RUN_START", "9"))   # 09:00
    RUN_END   = int(os.getenv("RUN_END",   "21"))  # 21:00
    FORCE_RUN = os.getenv("CRON_FORCE_RUN", "0") == "1"

    STOP = False
    def _stop(sig, frm):
        nonlocal_var = None  # sólo para que el editor no se queje
        # usamos global para no depender de 'nonlocal'
        global STOP
        STOP = True
        log(f"🛑 Señal recibida: {sig}")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    def now_vet():
        # Evita 'astimezone() cannot be applied to a naive datetime'
        return datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(tz)

    def should_run(dt):
        # ejecuta cuando el minuto sea 0, entre RUN_START y RUN_END (inclusive)
        return RUN_START <= dt.hour <= RUN_END and dt.minute == 0

    last_run_key = None
    log(f"⏱️ Cron activo. Ventana: {RUN_START}:00–{RUN_END}:00 {TZ} | FORCE_RUN={FORCE_RUN}")

    if FORCE_RUN:
        try:
            log("🔄 FORCE_RUN=1 → ejecutando actualización inicial…")
            actualizar_todas_las_tasas()
            log("✅ Actualización inicial OK.")
        except Exception:
            log("❌ Error en actualización inicial:")
            log(traceback.format_exc())

    while not STOP:
        try:
            dt = now_vet()
            key = (dt.date(), dt.hour)
            if should_run(dt) and key != last_run_key:
                log(f"🔄 Ejecutando actualización {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                actualizar_todas_las_tasas()
                log("✅ Tasas actualizadas.")
                last_run_key = key
            else:
                if dt.minute % 5 == 0 and dt.second == 0:
                    log(f"💤 Esperando hora exacta… ahora {dt.strftime('%H:%M:%S')}")
        except Exception:
            log("❌ Error en bucle principal:")
            log(traceback.format_exc())
        time.sleep(1)

    log("👋 Saliendo con gracia.")
except SystemExit:
    raise
except Exception:
    log("💥 Excepción fatal al iniciar cron_worker:")
    log(traceback.format_exc())
    sys.exit(1)

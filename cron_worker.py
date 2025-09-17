# cron_worker.py
import os, sys, time, signal, subprocess, traceback
from datetime import datetime, timezone

def log(msg):
    print(msg, flush=True)

# --- Playwright bootstrap: instala Chromium si falta ---
def ensure_playwright_chromium():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/render/.cache/ms-playwright")
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        log("❌ Playwright no está instalado (revisa requirements.txt).")
        return
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        log("✅ Chromium disponible.")
        return
    except Exception as e:
        log(f"ℹ️ Chromium no disponible aún: {e}")

    log("⬇️ Instalando navegadores de Playwright (chromium)…")
    try:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
        log(proc.stdout or "")
        if proc.returncode != 0:
            log(f"⚠️ 'playwright install chromium' terminó con código {proc.returncode}. STDERR:\n{proc.stderr}")
        else:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                b.close()
            log("✅ Chromium instalado y verificado.")
    except Exception:
        log("❌ Error instalando Chromium:")
        log(traceback.format_exc())

log(f"🚀 cron_worker arrancando | Python: {sys.version}")
try:
    log(f"📂 CWD: {os.getcwd()}")
    try:
        log(f"📄 Files: {os.listdir('.')}")
    except Exception as _e:
        log(f"⚠️ No pude listar archivos: {_e}")

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

    # Asegura Playwright antes de usarlo en guardar_tasas
    ensure_playwright_chromium()

    try:
        from guardar_tasas import actualizar_todas_las_tasas
    except Exception:
        log("❌ Error importando guardar_todas_las_tasas:")
        log(traceback.format_exc())
        raise

    RUN_START = int(os.getenv("RUN_START", "9"))     # 09:00 VET
    RUN_END   = int(os.getenv("RUN_END",   "21"))    # 21:00 VET
    FORCE_RUN = os.getenv("CRON_FORCE_RUN", "0") == "1"

    # >>> CORRECCIÓN AQUÍ: usar global, no nonlocal <<<
    stop = False
    def _stop(sig, frm):
        global stop
        stop = True
        log(f"🛑 Señal recibida: {sig}")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    def now_vet():
        return datetime.now(timezone.utc).astimezone(tz)

    def should_run(dt):
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

    while not stop:
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

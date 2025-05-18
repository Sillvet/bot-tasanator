import schedule
import time
import datetime
import subprocess

# Rango activo: entre 9:00 y 21:00
def esta_dentro_del_horario():
    ahora = datetime.datetime.now().time()
    return datetime.time(9, 0) <= ahora <= datetime.time(21, 0)

# Ejecutar el script solo si está dentro del horario permitido
def ejecutar_guardar_tasas():
    if esta_dentro_del_horario():
        print(f"[{datetime.datetime.now()}] ✅ Ejecutando guardar_tasas.py...")
        subprocess.run(["python", "guardar_tasas.py"])
    else:
        print(f"[{datetime.datetime.now()}] ⏸️ Fuera del horario. No se ejecuta.")

# Programar cada hora en punto
schedule.every().hour.at(":00").do(ejecutar_guardar_tasas)

print("🕒 Scheduler iniciado. Esperando intervalos...")

while True:
    schedule.run_pending()
    time.sleep(30)

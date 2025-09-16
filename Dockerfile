# Usa Python 3.10 como base
FROM python:3.10-slim

# Crea carpeta de trabajo
WORKDIR /app

# Copia todo el proyecto
COPY . .

# Instala dependencias
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Comando que ejecutará el bot
CMD ["python", "bot_telegram.py"]

FROM python:3.12-slim

LABEL org.opencontainers.image.title="CFIR Laboratories Framework"
LABEL org.opencontainers.image.description="Форензик-каркас для исследования артефактов ОС и приложений"

WORKDIR /app

# Зависимости ставятся отдельным слоем — переиспользуются при изменении кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем только код каркаса, чтобы образ не зависел от улик и результатов
COPY Run.py Settings.json ./
COPY Interfaces/ Interfaces/
COPY Common/ Common/
COPY Modules/ Modules/

# Каталоги для результатов, логов и временных файлов создаются при монтировании,
# но создаём заранее, чтобы пользователю не ловить PermissionError
RUN mkdir -p /app/Cases /app/Logs /app/Temp /app/Source

# Улики монтируются в /app/Source. Право на запись нам НЕ нужно:
# модули обязаны только читать. Если модуль попытается писать в Source —
# он упадёт с "Read-only file system", и это правильное поведение.
VOLUME ["/app/Cases", "/app/Logs"]

ENTRYPOINT ["python", "Run.py"]
CMD ["--source_folder", "Source", "--output_name", "result.sqlite"]

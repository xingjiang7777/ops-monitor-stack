FROM python:3.11-slim

WORKDIR /app

# 只依赖标准库，无需 pip install
COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8088

EXPOSE 8088

# 以非 root 运行；容器内只需访问网络
USER 65534:65534

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8088/healthz',timeout=2)"]

CMD ["python", "/app/app.py"]

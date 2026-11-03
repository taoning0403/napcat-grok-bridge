FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /opt/bridge
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && \
    addgroup --system --gid 10001 bridge && \
    adduser --system --uid 10001 --ingroup bridge bridge && \
    mkdir -p /data && chown bridge:bridge /data

USER bridge
VOLUME ["/data"]
EXPOSE 18080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:18080/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "napcat_grok_bridge.main:app", "--host", "0.0.0.0", "--port", "18080", "--no-access-log"]

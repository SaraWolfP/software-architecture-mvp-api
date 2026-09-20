# Dockerfile — API do simulador de financiamento imobiliário
#
# Três detalhes aqui não são cosméticos; sem eles o container sobe mas a
# aplicação não funciona:
#
#   1. DB_PATH aponta para /app/dados, que é um volume. Sem isso o banco
#      vive na camada da imagem e some a cada recriação do container.
#   2. inicializa_db() roda no escopo de módulo em app.py, e não dentro de
#      `if __name__ == '__main__'` — o gunicorn nunca executa esse bloco.
#   3. Um único worker com threads, e não vários processos: dois processos
#      escrevendo no mesmo arquivo SQLite produzem "database is locked".

FROM python:3.11-slim

LABEL org.opencontainers.image.title="Amortiza ou Investe? — API"
LABEL org.opencontainers.image.description="API REST de simulação de financiamento imobiliário com dados do Banco Central"
LABEL org.opencontainers.image.source="https://github.com/SaraWolfP/software-architecture-mvp-api"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/dados/banco_de_dados.db \
    BCB_TIMEOUT=10 \
    CACHE_TTL_HORAS=6

WORKDIR /app

# As dependências mudam menos que o código: instalar antes aproveita o cache
# de camadas do Docker nos builds seguintes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/dados

EXPOSE 5000

# O healthcheck usa /health, que responde 200 mesmo com o Banco Central fora
# do ar. Amarrá-lo à componente externa deixaria o container unhealthy — e o
# front sem subir — por um motivo alheio a esta aplicação.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=5)" || exit 1

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "app:app"]

# One table room. Build from the repo root:  docker build -f cloud/Dockerfile -t divinci-table .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends stockfish && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy pillow chess==1.11.2
WORKDIR /app
COPY table/ table/
COPY decks/ decks/
COPY cloud/ cloud/
RUN rm -rf table/.cache table/__pycache__ && mkdir -p table/.cache
# Card rules text, pre-built (12 MB). Building it in the container means parsing Scryfall's ~170 MB bulk
# JSON in memory, which killed a 1 GiB instance the first time a board was read.
COPY table/.cache/oracle.json table/.cache/oracle.json
ENV PYTHONUNBUFFERED=1 PORT=8800
EXPOSE 8800
CMD ["python", "cloud/entrypoint.py"]

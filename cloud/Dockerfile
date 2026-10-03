# One table room. Build from the repo root:  docker build -f cloud/Dockerfile -t divinci-table .
FROM python:3.12-slim
RUN pip install --no-cache-dir numpy pillow
WORKDIR /app
COPY table/ table/
COPY decks/ decks/
COPY cloud/ cloud/
RUN rm -rf table/.cache table/__pycache__ && mkdir -p table/.cache
ENV PYTHONUNBUFFERED=1 PORT=8800
EXPOSE 8800
CMD ["python", "cloud/entrypoint.py"]

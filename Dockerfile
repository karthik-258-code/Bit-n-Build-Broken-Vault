FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

VOLUME ["/data"]
EXPOSE 8765

ENTRYPOINT ["bv-server"]
CMD ["--data-dir", "/data", "--host", "0.0.0.0", "--port", "8765"]

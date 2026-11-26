FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY trefle_mcp ./trefle_mcp
RUN pip install --no-cache-dir .
ENV TREFLE_TOKEN="placeholder_glama_inspection_token"
ENTRYPOINT ["trefle-mcp"]

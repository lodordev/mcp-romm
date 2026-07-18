FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# stdio by default; set ROMM_MCP_TRANSPORT=http (+ ROMM_MCP_HOST=0.0.0.0)
# to serve remote MCP clients over Streamable HTTP on ROMM_MCP_PORT (8765).
EXPOSE 8765

CMD ["python", "server.py"]

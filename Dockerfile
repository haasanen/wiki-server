FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
ENV WIKI_DIR=/wiki WIKI_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)" || exit 1
CMD ["python", "/app/server.py"]

FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml .
COPY mock_server ./mock_server
RUN pip install --no-cache-dir .
ENV MOCK_PORT=8088
CMD ["sh", "-c", "uvicorn mock_server.main:app --host 0.0.0.0 --port ${MOCK_PORT}"]

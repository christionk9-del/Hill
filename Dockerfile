# Sentinel container image: runs inside the customer's network, read-only against mounted targets.
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 sentinel
WORKDIR /opt/sentinel
COPY pyproject.toml README.md ./
COPY sentinel ./sentinel
RUN pip install --no-cache-dir .
USER sentinel
WORKDIR /work
ENTRYPOINT ["sentinel"]
CMD ["--help"]

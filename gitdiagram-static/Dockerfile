# Offline, dependency-free image: the analyser needs only CPython and git.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir . && python -m unittest discover -s tests -q

WORKDIR /work
ENTRYPOINT ["staticdiagram"]
CMD ["--help"]

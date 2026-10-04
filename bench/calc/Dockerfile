# LibreOffice Calc without a GUI: checks the formula translator's claims
# (tests/test_lingua_excel.py). Runs with no network, like the bench sandbox.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m calc
USER calc
WORKDIR /work

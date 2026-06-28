FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    xz-utils \
    gcc-avr \
    avr-libc \
    avrdude \
    make \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install arduino-cli
RUN wget -q https://github.com/arduino/arduino-cli/releases/download/v0.35.3/arduino-cli_0.35.3_Linux_64bit.tar.gz -O /tmp/arduino-cli.tar.gz && \
    tar -xzf /tmp/arduino-cli.tar.gz -C /usr/local/bin/ && \
    rm /tmp/arduino-cli.tar.gz && \
    chmod +x /usr/local/bin/arduino-cli

# Configure arduino-cli and install cores + common built-in libraries
RUN arduino-cli config init && \
    arduino-cli core update-index && \
    arduino-cli core install arduino:avr && \
    arduino-cli core install arduino:samd && \
    arduino-cli core install esp32:esp32 --additional-urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json && \
    arduino-cli lib update-index && \
    arduino-cli lib install "Servo" "Stepper" "Ethernet" "SD" "WiFiNINA" "LiquidCrystal" "SPI" "Wire" "EEPROM"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

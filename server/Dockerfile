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
    && rm -rf /var/lib/apt/lists/*

# Download pre-built arduino-cli binary directly (more reliable than install script)
RUN wget -q https://github.com/arduino/arduino-cli/releases/download/v0.35.3/arduino-cli_0.35.3_Linux_64bit.tar.gz -O /tmp/arduino-cli.tar.gz && \
    tar -xzf /tmp/arduino-cli.tar.gz -C /usr/local/bin/ && \
    rm /tmp/arduino-cli.tar.gz && \
    chmod +x /usr/local/bin/arduino-cli

# Verify installation
RUN arduino-cli version

# Install Arduino AVR core
RUN arduino-cli core update-index && \
    arduino-cli core install arduino:avr

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
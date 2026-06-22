FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    xz-utils \
    gcc-avr \
    avr-libc \
    avrdude \
    make \
    && rm -rf /var/lib/apt/lists/*

# Install arduino-cli explicitly to /usr/local/bin
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh -s latest -- --bindir /usr/local/bin

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
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

# Install Arduino cores
RUN arduino-cli config init && \
    arduino-cli core update-index && \
    arduino-cli core install arduino:avr && \
    arduino-cli core install arduino:samd && \
    arduino-cli core install esp32:esp32 --additional-urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

# Pre-install popular libraries in ONE layer
RUN arduino-cli lib update-index && \
    arduino-cli lib install \
        "Servo" "Stepper" "Ethernet" "SD" "WiFiNINA" "LiquidCrystal" \
        "DHT sensor library" "Adafruit NeoPixel" "Adafruit GFX Library" \
        "Adafruit SSD1306" "Adafruit Unified Sensor" "OneWire" \
        "DallasTemperature" "IRremote" "FastLED" "NewPing" "RF24" \
        "MPU6050" "ArduinoJson" "PubSubClient" "Blynk" "ThingSpeak" \
        "WiFiManager" "ESPAsyncWebServer" "AsyncMqttClient" "MQTT" \
        "painlessMesh" "NimBLE-Arduino" "TFT_eSPI" "U8g2" "lvgl" \
        "LedControl" "TM1637Display" "PCF8574" "Adafruit MCP23017 Arduino Library" \
        "Adafruit_MCP3008" "Adafruit MCP4725" "Adafruit MCP9808 Library" \
        "Adafruit BMP280 Library" "Adafruit BME280 Library" "Adafruit BME680 Library" \
        "Adafruit MPU6050" "Adafruit VL53L0X" "Adafruit TCS34725" \
        "Adafruit AMG88xx Library" "Adafruit SHT31 Library" "Adafruit SGP30 Sensor" \
        "Adafruit CCS811 Library" "Adafruit seesaw Library" \
        "Adafruit Fingerprint Sensor Library" "Adafruit PN532" \
        "Adafruit Thermal Printer Library" "Adafruit GPS Library" \
        "Adafruit SleepyDog Library" "Adafruit Zero DMA Library" \
    || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# After core install, pre-install popular libraries in ONE layer
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

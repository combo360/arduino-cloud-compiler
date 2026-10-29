# i-Droid compile server: FastAPI + arduino-cli
FROM python:3.12-slim-trixie

ARG ARDUINO_CLI_VERSION=1.2.2
# Cores baked into the image so the first compile is fast. The ESP32 core
# adds well over 1 GB; for an AVR-only image build with
#   --build-arg PREINSTALL_CORES=arduino:avr --build-arg PREINSTALL_URLS=
ARG PREINSTALL_CORES="arduino:avr esp32:esp32"
ARG PREINSTALL_URLS="https://espressif.github.io/arduino-esp32/package_esp32_index.json"
# Board index URLs the server starts with (Boards Manager → URLs in the app).
ENV IDROID_DEFAULT_BOARD_URLS=${PREINSTALL_URLS}

# clang-format powers Auto Format (Arduino's style file is in app/).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates clang-format \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
    | BINDIR=/usr/local/bin sh -s ${ARDUINO_CLI_VERSION}

# Arduino's built-in examples (File > Examples in the desktop IDE).
ARG ARDUINO_EXAMPLES_REF=main
RUN mkdir -p /opt/arduino-examples \
 && curl -fsSL https://codeload.github.com/arduino/arduino-examples/tar.gz/${ARDUINO_EXAMPLES_REF} \
    | tar -xz -C /opt/arduino-examples --strip-components=1
ENV IDROID_BUILTIN_EXAMPLES=/opt/arduino-examples/examples

RUN useradd --create-home --uid 10001 idroid
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

COPY docker-entrypoint.sh /usr/local/bin/
# Strip Windows line endings in case the file was edited on Windows.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && chmod +x /usr/local/bin/docker-entrypoint.sh

# Cores, shared libraries and the core build cache are prepared under /opt at
# build time. A volume mounted on /data would hide anything baked there, so the
# entrypoint seeds /data from /opt on first start.
ENV IDROID_DATA_DIR=/data
RUN mkdir -p /data /opt/arduino15 /opt/arduino-shared /opt/build-cache \
 && chown idroid:idroid /data /opt/arduino15 /opt/arduino-shared /opt/build-cache
COPY preinstall-libraries.txt /opt/preinstall-libraries.txt
USER idroid
ENV ARDUINO_DIRECTORIES_DATA=/opt/arduino15 \
    ARDUINO_DIRECTORIES_USER=/opt/arduino-shared \
    ARDUINO_BUILD_CACHE_PATH=/opt/build-cache \
    ARDUINO_UPDATER_ENABLE_NOTIFICATION=false
RUN URLS=""; [ -n "${PREINSTALL_URLS}" ] && URLS="--additional-urls ${PREINSTALL_URLS}"; \
    arduino-cli core update-index $URLS \
 && for core in ${PREINSTALL_CORES}; do arduino-cli core install "$core" $URLS; done
# Shared libraries: a failed library is reported in the build log but does not
# stop the build (unlike the original server, the failure is visible).
RUN arduino-cli lib update-index \
 && sed 's/\r$//' /opt/preinstall-libraries.txt | grep -v '^#' | sed '/^$/d' | while IFS= read -r lib; do \
      arduino-cli lib install "$lib" || echo "WARNING: could not install library: $lib"; \
    done
# Compile an empty sketch once per preinstalled core so each core is already
# in the build cache (the first ESP32 compile otherwise takes minutes).
RUN mkdir -p /tmp/warmup && printf 'void setup(){}\nvoid loop(){}\n' > /tmp/warmup/warmup.ino \
 && arduino-cli compile --fqbn arduino:avr:uno /tmp/warmup \
 && if echo " ${PREINSTALL_CORES} " | grep -q " esp32:esp32"; then \
      arduino-cli compile --fqbn esp32:esp32:esp32 /tmp/warmup; fi \
 && rm -rf /tmp/warmup
# The server sets these per request; clear the build-time values.
ENV ARDUINO_DIRECTORIES_DATA= ARDUINO_DIRECTORIES_USER= ARDUINO_BUILD_CACHE_PATH=

# The entrypoint starts as root only to make the data disk writable (hosted
# disks are often mounted owned by root), then runs the server as "idroid".
USER root
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8080}"]

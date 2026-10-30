#!/bin/sh
# Prepares the data folder, then starts the server as the unprivileged
# "idroid" user. On first start it copies what was prepared in the image:
# board cores, shared libraries and the core build cache.
set -e
DATA="${IDROID_DATA_DIR:-/data}"
APP_USER=idroid

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA"
  # A newly mounted disk is usually owned by root: hand it to the app user.
  if [ "$(stat -c %U "$DATA")" != "$APP_USER" ]; then
    echo "Giving $DATA to $APP_USER..."
    chown -R "$APP_USER:$APP_USER" "$DATA"
  fi
  # Re-run this script as the app user.
  exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --clear-groups \
    env HOME="/home/$APP_USER" sh "$0" "$@"
fi

if ! touch "$DATA/.write-test" 2>/dev/null; then
  echo "ERROR: $DATA is not writable by $(id -un). Mount a writable disk at $DATA" >&2
  echo "or set IDROID_DATA_DIR to a writable folder." >&2
  exit 1
fi
rm -f "$DATA/.write-test"

seed() {  # seed <from> <to> <marker inside from>
  if [ ! -e "$2/$3" ] && [ -e "$1/$3" ]; then
    echo "Copying $1 to $2 (first start)..."
    mkdir -p "$2"
    cp -a "$1/." "$2/"
  fi
}
seed /opt/arduino15 "$DATA/arduino15" packages
seed /opt/arduino-shared "$DATA/shared" libraries
seed /opt/build-cache "$DATA/build-cache" .
# Keyless library downloads need $DATA/library_index.json. The image bakes one
# into /opt/arduino15; an existing disk skips the seeding above, so copy it here.
# Copy only -- never run arduino-cli here: a 57 MB download or a large core copy
# on the startup path exceeds a 512 MB instance and kills the deploy.
if [ ! -s "$DATA/library_urls.json" ] && [ -s /opt/arduino15/library_urls.json ]; then
  echo "Seeding the compact library index from the image..."
  cp /opt/arduino15/library_urls.json "$DATA/library_urls.json" || true
fi
if [ ! -s "$DATA/library_index.json" ] && [ -s /opt/arduino15/library_index.json ]; then
  echo "Seeding the library index from the image..."
  cp /opt/arduino15/library_index.json "$DATA/library_index.json" || true
fi
mkdir -p "$DATA/users" "$DATA/staging"
exec "$@"

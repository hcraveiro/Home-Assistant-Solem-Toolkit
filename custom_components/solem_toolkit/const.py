"""Constants for Solem Toolkit."""

DOMAIN = "solem_toolkit"

# Event fired whenever the device reports its status, which is after every
# command as well as on an explicit read. This integration has no config entry
# and therefore no entity platform to put a sensor on, so the event bus is how
# a reading reaches a dashboard: a trigger-based template sensor in
# configuration.yaml listens for it.
EVENT_DEVICE_STATUS = f"{DOMAIN}_status"

# Solem BLE characteristic used for command writes
CHARACTERISTIC_UUID = "108b0002-eab5-bc09-d0ea-0b8f467ce8ee"

# Solem BLE characteristic used for notifications (command ack / state readback)
NOTIFY_CHARACTERISTIC_UUID = "108b0003-eab5-bc09-d0ea-0b8f467ce8ee"

# Default Bluetooth connection timeout (seconds)
DEFAULT_BLUETOOTH_TIMEOUT = 15
MIN_BLUETOOTH_TIMEOUT = 5

# How long to wait for a device notification confirming a command was received.
# An expired response window leaves the outcome unknown; it does not prove
# the command failed or was never received.
COMMAND_ACK_TIMEOUT = 3

# How long to wait for the notify subscription to be enabled. This is a
# descriptor write, so it needs a working link: if it times out, the link is
# impaired (a WiFi scan on an ESPHome proxy starving the shared radio does
# exactly this) and the command must not be written blindly.
NOTIFY_SUBSCRIBE_TIMEOUT = 5

# How long to keep listening after the first reply when the point of the
# connection is to *read* the device status. A status reply comes in three
# chunks: in the capture they arrived within 3 ms of each other once and spread
# over 360 ms another time, so a window well above that costs nothing and
# avoids a truncated payload. Only `read_status()` waits this long - the
# command path still returns as soon as the device acknowledges, because making
# every button press a second slower to collect a log line is not a trade worth
# making.
STATUS_COLLECT_SECONDS = 1.5

# Connection acquisition only, shared by every model and diagnostic path.
# A short pause tolerates transient proxy contention without replaying writes.
# Slow teardown can still exhaust the single configured Bluetooth timeout;
# these limits never multiply that budget. Not user-facing configuration.
CONNECTION_RETRY_BACKOFF_SECONDS = 1.0
CONNECTION_MAX_ATTEMPTS = 3

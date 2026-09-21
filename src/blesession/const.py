"""Option keys and defaults shared across integrations.

Exported so config flows and options can use the same names; the library
never reads configuration itself.
"""

CONF_RETRY_COUNT = "retry_count"
CONF_KEEP_CONNECTION = "keep_connection"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ATTEMPT_TIMEOUT = "attempt_timeout"

DEFAULT_RETRY_COUNT = 3
DEFAULT_KEEP_CONNECTION = False
DEFAULT_RETRY_PAUSE_S = 1.0

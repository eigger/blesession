"""Finding the characteristic a protocol talks to, and saying why it is not there.

Every protocol opens by looking up its service and characteristics on the
connected client and checking what the device exposes: the characteristic
exists, it can do what the protocol needs (write without response, notify),
and the link's write size holds the protocol's frames. Each integration wrote
that by hand with its own wording; the lookup is the same everywhere, what
the device is called and what it needs is the integration's.
"""

from __future__ import annotations

from collections.abc import Iterable

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

from .errors import GattMismatch


def characteristic_or_raise(
    client: BleakClient,
    service_uuid: str,
    char_uuid: str,
    *,
    properties: Iterable[str] = (),
    min_write_size: int | None = None,
    label: str = "device",
) -> BleakGATTCharacteristic:
    """The characteristic `char_uuid` of `service_uuid`, or `GattMismatch`.

    `properties` are the bleak property names it must carry
    (`"write-without-response"`, `"notify"`, ...). `min_write_size` is the
    smallest write-without-response size the protocol can work with; the
    link's own limit comes from the characteristic. `label` names the device
    in the message ("ETAG", "XTE") so a report reads as the integration's own.

    `GattMismatch` is a `BleSessionError` in the `session` stage: the link is
    up, but what the device exposes is not what this protocol expects. A
    service or characteristic UUID that appears more than once, or services
    not discovered yet, is reported the same way.

    `min_write_size` reads bleak's `max_write_without_response_size`, which
    stays at the 20-byte default until the MTU is known (always, on BlueZ
    before 5.62). Check it once the link has settled. A missing service,
    characteristic or property is final (`GattMismatch.retryable` is False, so
    `default_retry_if` stops); a too-small write size and a failed lookup can be
    transient, so those are raised `retryable=True`.
    """
    if isinstance(properties, str):
        properties = (properties,)
    try:
        service = client.services.get_service(service_uuid)
        if service is None:
            raise GattMismatch(f"{label} service {service_uuid} is missing")
        char = service.get_characteristic(char_uuid)
    except BleakError as exc:
        raise GattMismatch(f"{label} GATT lookup failed: {exc}", retryable=True) from exc
    if char is None:
        raise GattMismatch(f"{label} characteristic {char_uuid} is missing")
    missing = [prop for prop in properties if prop not in char.properties]
    if missing:
        raise GattMismatch(
            f"{label} characteristic {char_uuid} lacks {', '.join(missing)} "
            f"(has {', '.join(char.properties) or 'none'})"
        )
    if min_write_size is not None:
        size = char.max_write_without_response_size
        if size < min_write_size:
            raise GattMismatch(
                f"{label} write size {size} is too small for the protocol's "
                f"{min_write_size}-byte frames",
                retryable=True,
            )
    return char

"""Finding the characteristic a protocol talks to, and saying why it is not there.

Every protocol opens by looking up its service and characteristics on the
connected client and checking what the device exposes: the characteristic
exists, it can do what the protocol needs (write without response, notify),
and the link's write size holds the protocol's frames. Each integration wrote
that by hand with its own wording; the lookup is the same everywhere, what
the device is called and what it needs is the integration's.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

from .errors import GattMismatch

ATT_OVERHEAD = 3
"""Bytes of an ATT write that are not payload: opcode and handle."""

DEFAULT_WRITE_SIZE = 20
"""What a write carries on a 23-byte MTU, and what bleak reports until it knows."""


@dataclass(frozen=True)
class WriteLimit:
    """How many payload bytes one write can carry, and how well that is known.

    `size` is always usable. `mtu` is the ATT MTU only when the backend
    reported it; it is `None` when it did not, or when the backend answered
    with its own placeholder (BlueZ warns that it is using the default).
    `source` says where `size` came from: `"characteristic"` (the
    characteristic's own limit), `"mtu"` (the reported MTU less the ATT
    header) or `"default"` (nothing was reported; `size` is the caller's
    fallback).
    """

    size: int
    mtu: int | None
    source: Literal["characteristic", "mtu", "default"]

    def facts(self) -> dict[str, int | str]:
        """The report facts for `trace.note(**limit.facts())`.

        `write_size` and `write_size_source` always, `mtu` only when the
        backend really reported one.
        """
        facts: dict[str, int | str] = {}
        if self.mtu is not None:
            facts["mtu"] = self.mtu
        facts["write_size"] = self.size
        facts["write_size_source"] = self.source
        return facts


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


def _reported_mtu(client: BleakClient) -> int | None:
    """The ATT MTU the backend reports, or `None` when it has no real answer.

    BlueZ answers 23 with a warning until the MTU is acquired; that warning
    is the signal the value is a placeholder, so it is swallowed here and the
    value dropped.
    """
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mtu = client.mtu_size
    except Exception:  # noqa: BLE001 - no MTU is an answer, not an error
        return None
    if caught or not isinstance(mtu, int) or isinstance(mtu, bool) or mtu <= ATT_OVERHEAD:
        return None
    return mtu


def _reported_write_size(char: BleakGATTCharacteristic | None) -> int | None:
    if char is None:
        return None
    try:
        size = char.max_write_without_response_size
    except Exception:  # noqa: BLE001 - no size is an answer, not an error
        return None
    return size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else None


def write_limit(
    client: BleakClient,
    char: BleakGATTCharacteristic | None = None,
    *,
    response: bool = False,
    default: int = DEFAULT_WRITE_SIZE,
) -> WriteLimit:
    """The payload one write to `char` can carry on this link.

    Without response the characteristic's own limit wins when it is above
    the 20-byte floor: that figure is what the backend will actually accept.
    At or below the floor it may only be bleak's placeholder (always so on
    BlueZ before 5.62, and until the MTU is known), so the reported MTU less
    the 3-byte ATT header is used instead when there is one. With response
    the characteristic's limit does not apply, and the MTU less the header is
    used. Where nothing real was reported, `default` is the size and
    `source` is `"default"`.

    The answer is only as good as the moment it is read: call this once the
    link has settled, when the transfer starts, not right after connecting.
    A backend that answers its default MTU of 23 without a warning (the
    ESPHome proxy before its exchange) reads as `source="mtu"`, size 20.
    Protocol overhead beyond the ATT header (a command header, say) is the
    caller's to subtract, and so are any caps and minimums.
    """
    mtu = _reported_mtu(client)
    from_mtu = mtu - ATT_OVERHEAD if mtu is not None else None
    if not response:
        size = _reported_write_size(char)
        if size is not None and size > DEFAULT_WRITE_SIZE:
            return WriteLimit(size, mtu, "characteristic")
        if from_mtu is not None:
            return WriteLimit(from_mtu, mtu, "mtu")
        return WriteLimit(default, mtu, "default")
    if from_mtu is not None:
        return WriteLimit(from_mtu, mtu, "mtu")
    return WriteLimit(default, mtu, "default")

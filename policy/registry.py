"""The admin-owned device list (name -> MAC address -> class, optional fixed IP).

Device names are resolved here, and only here. A hostname learned from DHCP or
ARP is never trusted as an identity: the class of a device is decided by the
MAC address the admin tied to a name in ``devices.yaml``.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern

from .config import PolicyConfigError, _load_yaml
from .models import DEVICE_CLASSES, Device

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
MIN_PARTIAL_LEN = 3


class DeviceRegistry:
    def __init__(self, devices: Iterable[Device]) -> None:
        self._by_name: Dict[str, Device] = {}
        self._by_mac: Dict[str, Device] = {}
        self._by_ip: Dict[str, Device] = {}
        for dev in devices:
            if dev.name in self._by_name:
                raise PolicyConfigError(f"devices.yaml: duplicate name '{dev.name}'")
            if dev.mac in self._by_mac:
                raise PolicyConfigError(f"devices.yaml: duplicate MAC '{dev.mac}'")
            if dev.ip and dev.ip in self._by_ip:
                raise PolicyConfigError(f"devices.yaml: duplicate IP '{dev.ip}'")
            self._by_name[dev.name] = dev
            if dev.mac:
                self._by_mac[dev.mac] = dev
            if dev.ip:
                self._by_ip[dev.ip] = dev

    @classmethod
    def from_file(cls, path: Path, name_pattern: Pattern[str]) -> "DeviceRegistry":
        doc = _load_yaml(Path(path))
        raw = doc.get("devices")
        if not isinstance(raw, list) or not raw:
            raise PolicyConfigError("devices.yaml: 'devices' must be a non-empty list")
        devices: List[Device] = []
        for i, item in enumerate(raw):
            where = f"devices.yaml entry {i}"
            if not isinstance(item, dict):
                raise PolicyConfigError(f"{where}: must be a mapping")
            unknown = set(item) - {"name", "mac", "class", "ip"}
            if unknown:
                raise PolicyConfigError(f"{where}: unknown keys {sorted(unknown)}")
            name, mac, dev_class, ip = (item.get(k) for k in ("name", "mac", "class", "ip"))
            if not isinstance(name, str) or not name_pattern.fullmatch(name):
                raise PolicyConfigError(f"{where}: invalid or non-lowercase name")
            if not isinstance(mac, str) or not _MAC_RE.match(mac.lower()):
                raise PolicyConfigError(f"{where}: invalid MAC address")
            if dev_class not in DEVICE_CLASSES:
                raise PolicyConfigError(f"{where}: unknown class {dev_class!r}")
            if ip is not None:
                try:
                    ip = str(ipaddress.IPv4Address(str(ip)))
                except ValueError as exc:
                    raise PolicyConfigError(f"{where}: invalid IP address") from exc
            devices.append(Device(name, mac.lower(), dev_class, ip))
        return cls(devices)

    def names(self) -> List[str]:
        return sorted(self._by_name)

    def devices(self) -> List[Device]:
        return [self._by_name[n] for n in self.names()]

    def resolve(self, name: str) -> Optional[Device]:
        """Exact (already normalised) name only. No guessing."""
        return self._by_name.get(name)

    def by_mac(self, mac: str) -> Optional[Device]:
        return self._by_mac.get(mac.lower())

    def by_ip(self, ip: str) -> Optional[Device]:
        return self._by_ip.get(ip)

    def candidates(self, partial: str) -> List[str]:
        """Names that merely contain ``partial``; used to ask, never to act."""
        if len(partial) < MIN_PARTIAL_LEN:
            return []
        return sorted(n for n in self._by_name if partial in n)

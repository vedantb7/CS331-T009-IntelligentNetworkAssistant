"""Pydantic schemas and data models for MCP tool arguments and responses in INA v2.

Defines typed input annotations and structured result models for network management.
Per MCP specification (revision 2026-07-28), tool outputs conform to outputSchema
and are serialized into structuredContent and text content blocks.

Covers all 8 canonical tools:
- block_client(client, target) -> BlockResult
- unblock_client(client, target) -> UnblockResult
- limit_bandwidth(client, download_kbit, upload_kbit) -> LimitResult
- remove_bandwidth_limit(client) -> RemoveLimitResult
- get_status(client?) -> StatusResult
- kick_client(client) -> KickResult
- ban_client(client, duration_minutes?, reason?, confirm?) -> BanResult
- unban_client(client) -> UnbanResult
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

try:
    from pydantic import BaseModel, ConfigDict, Field
    PYDANTIC_AVAILABLE = True
except ImportError:
    # Fallback dataclass shim for environments without pydantic
    from dataclasses import asdict, dataclass, field

    PYDANTIC_AVAILABLE = False

    class BaseModel:  # type: ignore[no-redef]
        def model_dump(self) -> Dict[str, Any]:
            res: Dict[str, Any] = {}
            for k, v in getattr(self, "__dict__", {}).items():
                if hasattr(v, "model_dump"):
                    res[k] = v.model_dump()
                elif isinstance(v, list):
                    res[k] = [i.model_dump() if hasattr(i, "model_dump") else i for i in v]
                else:
                    res[k] = v
            return res

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None


# Regex patterns matching INA network specification
# Client name: alphanumeric hostname (1-63 chars) or IPv4 in 192.168.50.0/24
CLIENT_REGEX = r"^([a-z0-9][a-z0-9-]{0,62}|192\.168\.50\.[0-9]{1,3})$"
TARGET_REGEX = r"^([a-z0-9][a-z0-9-]{0,62}|192\.168\.50\.[0-9]{1,3}|gateway|internet|all)$"
MAC_REGEX = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$"
CLIENT_OR_MAC_REGEX = r"^([a-z0-9][a-z0-9-]{0,62}|192\.168\.50\.[0-9]{1,3}|[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5})$"
REASON_REGEX = r"^[A-Za-z0-9 .,_-]{0,120}$"

MIN_BANDWIDTH_KBIT = 256
MAX_BANDWIDTH_KBIT = 20000

MAX_BAN_MINUTES = 10080          # 7 days
OPERATOR_MAX_BAN_MINUTES = 60


if PYDANTIC_AVAILABLE:

    # --------------------------------------------------------------------------
    # Tool Output Result Models
    # --------------------------------------------------------------------------

    class BlockResult(BaseModel):
        """Structured result returned by the block_client tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier (hostname or IP).")
        client_ip: str = Field(description="Resolved IPv4 address of the client.")
        target: str = Field(description="Target client or scope keyword ('gateway', 'internet', 'all').")
        blocked: bool = Field(description="True if traffic to target is blocked following the call.")
        changed: bool = Field(description="True if a new blocking rule was added; False if already present.")
        verified: bool = Field(description="True if the rule was read back from kernel state and confirmed.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class UnblockResult(BaseModel):
        """Structured result returned by the unblock_client tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier (hostname or IP).")
        client_ip: str = Field(description="Resolved IPv4 address of the client.")
        target: str = Field(description="Target client or scope keyword ('gateway', 'internet', 'all').")
        unblocked: bool = Field(description="True if traffic to target is unblocked following the call.")
        changed: bool = Field(description="True if a blocking rule was removed; False if already absent.")
        verified: bool = Field(description="True if rule absence was confirmed in kernel state.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class LimitResult(BaseModel):
        """Structured result returned by the limit_bandwidth tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier (hostname or IP).")
        client_ip: str = Field(description="Resolved IPv4 address of the client.")
        download_kbit: int = Field(description="Active download ceiling in kbit/s.")
        upload_kbit: int = Field(description="Active upload ceiling in kbit/s.")
        changed: bool = Field(description="True if class was added or updated; False if rate identical.")
        verified: bool = Field(description="True if tc classes were read back from kernel and matched.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class RemoveLimitResult(BaseModel):
        """Structured result returned by the remove_bandwidth_limit tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier (hostname or IP).")
        client_ip: str = Field(description="Resolved IPv4 address of the client.")
        changed: bool = Field(description="True if an active tc limit was removed; False if absent.")
        verified: bool = Field(description="True if class absence was confirmed in kernel state.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class KickResult(BaseModel):
        """Structured result returned by the kick_client tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier as given.")
        client_ip: Optional[str] = Field(default=None, description="Resolved IPv4 address, if known.")
        mac: str = Field(description="Station MAC address acted on.")
        was_associated: bool = Field(description="True if the station was associated before the call.")
        kicked: bool = Field(description="True if hostapd accepted the deauthentication.")
        reconnected: Optional[bool] = Field(default=None, description="True if it re-associated within the verify window.")
        changed: bool = Field(description="True if a deauthentication was sent.")
        verified: bool = Field(description="True if the previous association was confirmed gone.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class BanResult(BaseModel):
        """Structured result returned by the ban_client tool (preview or applied)."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier as given.")
        client_ip: Optional[str] = Field(default=None, description="Resolved IPv4 address, if known.")
        mac: str = Field(description="Station MAC address banned.")
        preview: bool = Field(description="True if this is a dry-run preview and nothing changed.")
        requires_confirmation: bool = Field(description="True if the call must be repeated with confirm=true.")
        banned: bool = Field(description="True if the MAC is denied following the call.")
        changed: bool = Field(description="True if a new ban was added; False if already banned or preview.")
        verified: bool = Field(description="True if deny ACL, deny file and station absence were confirmed.")
        currently_associated: bool = Field(description="True if the station is associated right now.")
        expires_at: Optional[str] = Field(default=None, description="ISO-8601 UTC expiry, or null if permanent.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class UnbanResult(BaseModel):
        """Structured result returned by the unban_client tool."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier as given.")
        mac: str = Field(description="Station MAC address unbanned.")
        unbanned: bool = Field(description="True if the MAC is no longer denied following the call.")
        changed: bool = Field(description="True if a ban was removed; False if already absent.")
        verified: bool = Field(description="True if absence from deny ACL and deny file was confirmed.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or verification message.")

    class BanEntry(BaseModel):
        """One active ban as reported by get_status."""

        model_config = ConfigDict(extra="forbid")

        client: str
        mac: str
        reason: Optional[str] = None
        banned_at: str
        expires_at: Optional[str] = None
        banned_by_role: str

    class ClientStatus(BaseModel):
        """Status information for a single managed client device."""

        model_config = ConfigDict(extra="forbid")

        client: str = Field(description="Client identifier name.")
        ip: str = Field(description="Assigned IPv4 address.")
        mac: Optional[str] = Field(default=None, description="Bound MAC address if known.")
        associated: bool = Field(description="True if client has an active 802.11/ARP association.")
        neighbor_state: Optional[str] = Field(default=None, description="ARP neighbor state (e.g. REACHABLE, STALE).")
        blocked_targets: List[str] = Field(default_factory=list, description="List of currently blocked targets/scopes.")
        download_kbit: Optional[int] = Field(default=None, description="Current download limit in kbit/s if shaped.")
        upload_kbit: Optional[int] = Field(default=None, description="Current upload limit in kbit/s if shaped.")
        banned: bool = Field(default=False, description="True if client MAC is currently banned on the AP.")

    class StatusResult(BaseModel):
        """Structured result returned by the get_status tool."""

        model_config = ConfigDict(extra="forbid")

        clients: List[ClientStatus] = Field(default_factory=list, description="Status list of network clients.")
        bans: List[BanEntry] = Field(default_factory=list, description="Active MAC bans.")
        drift_detected: bool = Field(default=False, description="True if kernel state diverges from desired state.")
        message: Optional[str] = Field(default=None, description="Optional diagnostic or status message.")

    # --------------------------------------------------------------------------
    # Tool Input Argument Models
    # --------------------------------------------------------------------------

    class BlockClientArgs(BaseModel):
        """Schema-validating arguments for block_client invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_REGEX,
                description="Client reservation name (e.g. 'laptop-a') or IPv4 address in 192.168.50.0/24.",
            ),
        ]
        target: Annotated[
            str,
            Field(
                pattern=TARGET_REGEX,
                description="Another client (name or IP), or scope keyword: 'gateway', 'internet', or 'all'.",
            ),
        ]

    class UnblockClientArgs(BaseModel):
        """Schema-validating arguments for unblock_client invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_REGEX,
                description="Client reservation name (e.g. 'laptop-a') or IPv4 address in 192.168.50.0/24.",
            ),
        ]
        target: Annotated[
            str,
            Field(
                pattern=TARGET_REGEX,
                description="Another client (name or IP), or scope keyword: 'gateway', 'internet', or 'all'.",
            ),
        ]

    class LimitBandwidthArgs(BaseModel):
        """Schema-validating arguments for limit_bandwidth invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_REGEX,
                description="Client reservation name (e.g. 'laptop-a') or IPv4 address in 192.168.50.0/24.",
            ),
        ]
        download_kbit: Annotated[
            int,
            Field(
                ge=MIN_BANDWIDTH_KBIT,
                le=MAX_BANDWIDTH_KBIT,
                description="Egress download rate ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).",
            ),
        ]
        upload_kbit: Annotated[
            int,
            Field(
                ge=MIN_BANDWIDTH_KBIT,
                le=MAX_BANDWIDTH_KBIT,
                description="Ingress upload rate ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).",
            ),
        ]

    class RemoveLimitArgs(BaseModel):
        """Schema-validating arguments for remove_bandwidth_limit invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_REGEX,
                description="Client reservation name (e.g. 'laptop-a') or IPv4 address in 192.168.50.0/24.",
            ),
        ]

    class GetStatusArgs(BaseModel):
        """Schema-validating arguments for get_status invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Optional[
            Annotated[
                str,
                Field(
                    default=None,
                    pattern=CLIENT_REGEX,
                    description="Optional client reservation name or IP to filter status.",
                ),
            ]
        ] = None

    class KickClientArgs(BaseModel):
        """Schema-validating arguments for kick_client invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_OR_MAC_REGEX,
                description="Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.",
            ),
        ]

    class BanClientArgs(BaseModel):
        """Schema-validating arguments for ban_client invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_OR_MAC_REGEX,
                description="Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.",
            ),
        ]
        duration_minutes: Optional[
            Annotated[
                int,
                Field(
                    default=None,
                    ge=1,
                    le=MAX_BAN_MINUTES,
                    description="Ban duration in minutes (1 to 10080), or None for permanent ban.",
                ),
            ]
        ] = None
        reason: Optional[
            Annotated[
                str,
                Field(
                    default=None,
                    pattern=REASON_REGEX,
                    description="Optional short reason (up to 120 chars).",
                ),
            ]
        ] = None
        confirm: bool = Field(
            default=False,
            description="Confirmation flag; without confirm=True only returns a preview.",
        )

    class UnbanClientArgs(BaseModel):
        """Schema-validating arguments for unban_client invocations."""

        model_config = ConfigDict(extra="forbid")

        client: Annotated[
            str,
            Field(
                pattern=CLIENT_OR_MAC_REGEX,
                description="Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.",
            ),
        ]

else:
    # --------------------------------------------------------------------------
    # Fallback Dataclass Shims (when pydantic is not installed)
    # --------------------------------------------------------------------------

    @dataclass
    class BlockResult(BaseModel):  # type: ignore[no-redef]
        client: str
        client_ip: str
        target: str
        blocked: bool
        changed: bool
        verified: bool
        message: Optional[str] = None

    @dataclass
    class UnblockResult(BaseModel):  # type: ignore[no-redef]
        client: str
        client_ip: str
        target: str
        unblocked: bool
        changed: bool
        verified: bool
        message: Optional[str] = None

    @dataclass
    class LimitResult(BaseModel):  # type: ignore[no-redef]
        client: str
        client_ip: str
        download_kbit: int
        upload_kbit: int
        changed: bool
        verified: bool
        message: Optional[str] = None

    @dataclass
    class RemoveLimitResult(BaseModel):  # type: ignore[no-redef]
        client: str
        client_ip: str
        changed: bool
        verified: bool
        message: Optional[str] = None

    @dataclass
    class KickResult(BaseModel):  # type: ignore[no-redef]
        client: str
        mac: str
        was_associated: bool
        kicked: bool
        changed: bool
        verified: bool
        client_ip: Optional[str] = None
        reconnected: Optional[bool] = None
        message: Optional[str] = None

    @dataclass
    class BanResult(BaseModel):  # type: ignore[no-redef]
        client: str
        mac: str
        preview: bool
        requires_confirmation: bool
        banned: bool
        changed: bool
        verified: bool
        currently_associated: bool
        client_ip: Optional[str] = None
        expires_at: Optional[str] = None
        message: Optional[str] = None

    @dataclass
    class UnbanResult(BaseModel):  # type: ignore[no-redef]
        client: str
        mac: str
        unbanned: bool
        changed: bool
        verified: bool
        message: Optional[str] = None

    @dataclass
    class BanEntry(BaseModel):  # type: ignore[no-redef]
        client: str
        mac: str
        banned_at: str
        banned_by_role: str
        reason: Optional[str] = None
        expires_at: Optional[str] = None

    @dataclass
    class ClientStatus(BaseModel):  # type: ignore[no-redef]
        client: str
        ip: str
        associated: bool
        mac: Optional[str] = None
        neighbor_state: Optional[str] = None
        blocked_targets: List[str] = field(default_factory=list)
        download_kbit: Optional[int] = None
        upload_kbit: Optional[int] = None
        banned: bool = False

    @dataclass
    class StatusResult(BaseModel):  # type: ignore[no-redef]
        clients: List[ClientStatus] = field(default_factory=list)
        bans: List[BanEntry] = field(default_factory=list)
        drift_detected: bool = False
        message: Optional[str] = None

    @dataclass
    class BlockClientArgs(BaseModel):  # type: ignore[no-redef]
        client: str
        target: str

        def __post_init__(self) -> None:
            if not re.match(CLIENT_REGEX, self.client):
                raise ValueError(f"Invalid client identifier: '{self.client}'")
            if not re.match(TARGET_REGEX, self.target):
                raise ValueError(f"Invalid target identifier: '{self.target}'")

    @dataclass
    class UnblockClientArgs(BaseModel):  # type: ignore[no-redef]
        client: str
        target: str

        def __post_init__(self) -> None:
            if not re.match(CLIENT_REGEX, self.client):
                raise ValueError(f"Invalid client identifier: '{self.client}'")
            if not re.match(TARGET_REGEX, self.target):
                raise ValueError(f"Invalid target identifier: '{self.target}'")

    @dataclass
    class LimitBandwidthArgs(BaseModel):  # type: ignore[no-redef]
        client: str
        download_kbit: int
        upload_kbit: int

        def __post_init__(self) -> None:
            if not re.match(CLIENT_REGEX, self.client):
                raise ValueError(f"Invalid client identifier: '{self.client}'")
            if not (MIN_BANDWIDTH_KBIT <= self.download_kbit <= MAX_BANDWIDTH_KBIT):
                raise ValueError(
                    f"download_kbit {self.download_kbit} out of bounds [{MIN_BANDWIDTH_KBIT}, {MAX_BANDWIDTH_KBIT}]"
                )
            if not (MIN_BANDWIDTH_KBIT <= self.upload_kbit <= MAX_BANDWIDTH_KBIT):
                raise ValueError(
                    f"upload_kbit {self.upload_kbit} out of bounds [{MIN_BANDWIDTH_KBIT}, {MAX_BANDWIDTH_KBIT}]"
                )

    @dataclass
    class RemoveLimitArgs(BaseModel):  # type: ignore[no-redef]
        client: str

        def __post_init__(self) -> None:
            if not re.match(CLIENT_REGEX, self.client):
                raise ValueError(f"Invalid client identifier: '{self.client}'")

    @dataclass
    class GetStatusArgs(BaseModel):  # type: ignore[no-redef]
        client: Optional[str] = None

        def __post_init__(self) -> None:
            if self.client is not None and not re.match(CLIENT_REGEX, self.client):
                raise ValueError(f"Invalid client identifier: '{self.client}'")

    @dataclass
    class KickClientArgs(BaseModel):  # type: ignore[no-redef]
        client: str

        def __post_init__(self) -> None:
            if not re.match(CLIENT_OR_MAC_REGEX, self.client):
                raise ValueError(f"Invalid client identifier or MAC: '{self.client}'")

    @dataclass
    class BanClientArgs(BaseModel):  # type: ignore[no-redef]
        client: str
        duration_minutes: Optional[int] = None
        reason: Optional[str] = None
        confirm: bool = False

        def __post_init__(self) -> None:
            if not re.match(CLIENT_OR_MAC_REGEX, self.client):
                raise ValueError(f"Invalid client identifier or MAC: '{self.client}'")
            if self.duration_minutes is not None and not (1 <= self.duration_minutes <= MAX_BAN_MINUTES):
                raise ValueError(f"duration_minutes out of bounds [1, {MAX_BAN_MINUTES}]: {self.duration_minutes}")
            if self.reason is not None and not re.match(REASON_REGEX, self.reason):
                raise ValueError(f"Invalid reason string: '{self.reason}'")

    @dataclass
    class UnbanClientArgs(BaseModel):  # type: ignore[no-redef]
        client: str

        def __post_init__(self) -> None:
            if not re.match(CLIENT_OR_MAC_REGEX, self.client):
                raise ValueError(f"Invalid client identifier or MAC: '{self.client}'")

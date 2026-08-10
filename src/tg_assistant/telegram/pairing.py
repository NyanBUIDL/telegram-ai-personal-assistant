from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class PairingCode:
    value: str
    owner_id: int
    expires_at: datetime
    used: bool = False

    @classmethod
    def create(cls, owner_id: int, ttl_seconds: int = 300) -> PairingCode:
        alphabet = string.ascii_uppercase + string.digits
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        return cls(
            f"{raw[:4]}-{raw[4:]}", owner_id, datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        )

    def consume(self, value: str, sender_id: int) -> bool:
        if self.used or datetime.now(UTC) >= self.expires_at:
            return False
        if not secrets.compare_digest(self.value, value.upper()) or sender_id != self.owner_id:
            return False
        self.used = True
        return True

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Dict


def calculate_sha256_digest(data: Any) -> str:
    """Вычисляет детерминированный шестнадцатеричный дайджест SHA-256 для полезной нагрузки, сериализуемой в формат JSON."""
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    timestamp: str
    run_id: str
    action_id: str
    tool: str
    target: str
    status: str
    exit_code: int
    duration_ms: int
    evidence_ref: str
    proposal_digest: str
    evidence_digest: str
    policy_digest: str
    runtime_backend: str
    network_mode: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        action_id: str,
        tool: str,
        target: str,
        status: str,
        exit_code: int,
        duration_ms: int,
        evidence_ref: str,
        action_proposal_dict: Dict[str, Any],
        evidence_dict: Dict[str, Any],
        policy_dict: Dict[str, Any],
        runtime_backend: str,
        network_mode: str,
    ) -> AuditRecord:
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            action_id=action_id,
            tool=tool,
            target=target,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            proposal_digest=calculate_sha256_digest(action_proposal_dict),
            evidence_digest=calculate_sha256_digest(evidence_dict),
            policy_digest=calculate_sha256_digest(policy_dict),
            runtime_backend=runtime_backend,
            network_mode=network_mode,
            evidence_ref=evidence_ref,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
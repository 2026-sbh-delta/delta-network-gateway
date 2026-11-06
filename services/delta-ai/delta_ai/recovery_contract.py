"""Suggestions only: graph owns the action catalog and validates again before effects."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from delta_ai.diagnostic_contract import (
    DiagnosisReport,
    DiagnosisRequest,
    Identifier,
    SafeModel,
    Text,
    validate_report,
)

Action = Literal['retry_same_job', 'redeploy_sandbox', 'wait_for_operator']


class RecoveryRequest(SafeModel):
    diagnosis: DiagnosisRequest
    report: DiagnosisReport
    allowed_actions: tuple[Action, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def grounded(self):
        validate_report(self.report, self.diagnosis)
        if 'wait_for_operator' not in self.allowed_actions:
            raise ValueError('Operator fallback is required')
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError('Duplicate action')
        return self


def recovery_hash(request):
    return hashlib.sha256(json.dumps(request.model_dump(mode='json'), sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class RecoveryDraft(SafeModel):
    action: Action
    explanation: Text
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=40)


class RecoveryProposal(RecoveryDraft):
    case_id: Identifier
    run_id: Identifier
    input_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['proposed', 'unavailable', 'invalid']
    requires_approval: Literal[True] = True
    model: str | None = Field(default=None, max_length=128)


def fallback(request, status='unavailable'):
    return RecoveryProposal(action='wait_for_operator', explanation='해결 제안을 확정하지 못했습니다. 관측과 허용된 조치를 사람이 확인합니다.',
        evidence_ids=(request.diagnosis.observations[0].evidence_id,),
        case_id=request.diagnosis.case_id, run_id=request.diagnosis.run_id,
        input_hash=recovery_hash(request), status=status)


def validate_proposal(proposal, request):
    if (proposal.case_id, proposal.run_id, proposal.input_hash) != (
            request.diagnosis.case_id, request.diagnosis.run_id, recovery_hash(request)):
        raise ValueError('Proposal binding mismatch')
    if proposal.action not in request.allowed_actions or not set(proposal.evidence_ids) <= {
            o.evidence_id for o in request.diagnosis.observations}:
        raise ValueError('Unknown action or evidence')
    return proposal

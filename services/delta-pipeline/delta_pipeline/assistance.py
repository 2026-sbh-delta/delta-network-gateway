"""Human remediation and revalidation: model output never clears a failed gate."""

import hashlib
import os
import tempfile

from delta_ai.diagnostic_contract import DiagnosisReport, DiagnosisRequest
from delta_ai.recovery_contract import (
    RecoveryProposal,
    RecoveryRequest,
    fallback,
    validate_proposal,
)
from langgraph.types import Overwrite, interrupt

from delta_pipeline.exception_flow import prepare_exception
from delta_pipeline.reports import ReportRef, ReportStore
from delta_pipeline.steps import assert_gates_passed

MANUAL_LIMIT = 2


class Assistance:
    def __init__(self, deps, steps):
        self.deps, self.steps = deps, steps
        self.reports = ReportStore(deps.report_dir)

    def allowed(self, state):
        node = (state.get('diagnosis_request') or {}).get('node_id')
        origin = state.get('assisted_origin') or {}
        code = origin.get('code') if state.get('failure_code') == 'manual_revalidation_failed' else state.get('failure_code')
        failure_type = origin.get('type') if state.get('failure_code') == 'manual_revalidation_failed' else state.get('failure_type')
        if (self.deps.cancelled() or state.get('manual_count', 0) >= MANUAL_LIMIT
                or self.deps.options.execution_cloud != 'onprem'):
            return []
        if node in ('onprem_start', 'onprem_wait') and failure_type in (
                'ServiceUnavailable', 'TransientServiceError'):
            return ['retry_same_job']
        if node == 'onprem_wait' and code in (
                'sandbox_health_failed', 'sandbox_provider_error'):
            return ['redeploy_sandbox']
        return []

    def request(self, state, allowed):
        return RecoveryRequest(diagnosis=DiagnosisRequest.model_validate(state['diagnosis_request']),
                               report=DiagnosisReport.model_validate(state['diagnosis_report']),
                               allowed_actions=(*allowed, 'wait_for_operator'))

    def plan(self, state):
        ref = ReportRef.model_validate(state['diagnosis_ref'])
        directory = self.reports.verify(ref)
        allowed = self.allowed(state)
        request = self.request(state, allowed)
        path = directory / 'recovery-proposal.json'
        if path.is_symlink():
            raise ValueError('Unsafe proposal file')
        if path.exists():
            proposal = validate_proposal(RecoveryProposal.model_validate_json(path.read_bytes()), request)
        else:
            proposal = fallback(request)
            if self.deps.diagnostic is not None and hasattr(self.deps.diagnostic, 'propose'):
                try:
                    proposal = validate_proposal(RecoveryProposal.model_validate(
                        self.deps.diagnostic.propose(request.model_dump(mode='json'))), request)
                except Exception:  # noqa: BLE001 — model/transport outage cannot block manual revalidation
                    proposal = fallback(request, 'invalid')
            raw = proposal.model_dump_json(indent=2).encode()
            fd, name = tempfile.mkstemp(prefix='.proposal-', dir=directory)
            temporary = directory / name
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        plan = {'allowed': allowed, 'proposal': proposal.model_dump(mode='json'),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.deps.board.add_detail('exception-review', recovery_plan=plan)
        if self.deps.inbox:
            identifier = self.deps.inbox.emit(run_id=ref.run_id, case_id=ref.case_id, kind='action_required',
                summary=state['diagnosis_report']['summary'], actions=allowed)
            if self.deps.notification_sender:
                self.deps.inbox.deliver(identifier, self.deps.notification_sender)
        return {'assisted_plan': plan, 'assisted_choice': None,
                'assisted_origin': state.get('assisted_origin', {}) if state.get('failure_code') == 'manual_revalidation_failed'
                else {'code': state.get('failure_code'), 'type': state.get('failure_type')}}

    def verify_plan(self, state):
        ref = ReportRef.model_validate(state['diagnosis_ref'])
        directory = self.reports.verify(ref)
        path = directory / 'recovery-proposal.json'
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != state['assisted_plan']['sha256']:
            raise ValueError('Proposal integrity failure')
        validate_proposal(RecoveryProposal.model_validate_json(path.read_bytes()),
                          self.request(state, state['assisted_plan']['allowed']))
        return ref

    def review(self, state):
        ref = self.verify_plan(state)
        labels = {'retry_same_job': '조치 후 같은 작업 재검사',
                  'redeploy_sandbox': '정리 확인 후 재배포·재검사'}
        choices = [(a, labels[a]) for a in state['assisted_plan']['allowed']]
        answer = interrupt({'node_id': 'exception-review', 'case_id': ref.case_id,
            'prompt': '원인을 조치한 후 재검사를 선택하세요. 조건을 다시 검증하며 실패를 건너뛰지 않습니다.',
            'options': [*choices, ('acknowledge', '보고서 확인·실행 종료'), ('stop', '중단')],
            'report': ref.model_dump(), 'proposal': state['assisted_plan']['proposal']})
        if answer not in state['assisted_plan']['allowed']:
            self.reports.decide(ref, 'stop' if answer == 'stop' else 'acknowledge')
            from delta_pipeline.case_learning import CaseLearning
            CaseLearning(self.deps).record(ref, sequence=1, outcome='stopped' if answer == 'stop' else 'unknown')
            self.deps.board.update('exception-review', state='fail' if answer == 'stop' else 'pass',
                                   summary='사람이 중단했습니다' if answer == 'stop' else '보고서 확인·실행 종료')
            return {'assisted_choice': 'stop'}
        self.reports.decide(ref, 'reverify', action=answer)
        self.deps.board.update('exception-review', state='pass', summary='조치 후 재검사를 선택했습니다')
        self.deps.board.add_detail('exception-review', operator_action=answer)
        return {'assisted_choice': answer}

    def revalidate(self, state):
        count = state.get('manual_count', 0)
        try:
            ref = self.verify_plan(state)
            action = state['assisted_choice']
            if action not in self.allowed(state) or count >= MANUAL_LIMIT or self.deps.cancelled():
                raise ValueError()
            assert_gates_passed(state.get('gates') or {}, ('sast',))
            previous = {}
            if action == 'redeploy_sandbox':
                if not self.deps.onprem.resume_ready(state['onprem_run_id'],
                        key=self.steps._key(state, 'onprem'), commit=self.steps._commit(state), image=state['image']):
                    raise ValueError()
                previous = {'recovery_previous_run_id': state['onprem_run_id'],
                            'recovery_previous_key': self.steps._key(state, 'onprem'), 'manual_redeploy': True,
                            'onprem_attempt': state.get('onprem_attempt', 0) + 1}
                resume = 'onprem_start'
            else:
                resume = state['diagnosis_request']['node_id']
                if resume == 'onprem_wait':
                    if not self.deps.onprem.observation_ready(state['onprem_run_id'],
                            key=self.steps._key(state, 'onprem'), commit=self.steps._commit(state), image=state['image']):
                        raise ValueError()
                    job = self.deps.onprem.get(state['onprem_run_id'])
                    if str(job.get('id')) != state['onprem_run_id'] or job.get('cancel_requested'):
                        raise ValueError()
            event = {'action': action, 'attempt': count + 1, 'source': 'operator_revalidated',
                     'previous_run_id': state.get('onprem_run_id'), 'resume': resume}
            events = [*state.get('recovery_events', []), event]
            self.deps.board.add_detail('recovery', events=events)
            self.deps.board.update('recovery', state='running', summary='조치 후 재검사 조건 확인 · 실패 단계 재개')
            if self.deps.inbox:
                identifier = self.deps.inbox.emit(run_id=ref.run_id, case_id=ref.case_id, kind='revalidation_started',
                    summary='조치 후 재검사를 시작합니다. 완료 결과를 다시 확인합니다.')
                if self.deps.notification_sender:
                    self.deps.inbox.deliver(identifier, self.deps.notification_sender)
            from delta_pipeline.case_learning import begin_attempt
            pending = begin_attempt(self.deps, ref.model_dump(), action=action,
                                    attempt=count + 1, target='onprem_wait')
            return {**previous, 'halt': Overwrite(None), 'failure_type': Overwrite(None), 'failure_code': None,
                    'manual_count': count + 1, 'manual_resume': resume, 'recovery_events': events,
                    'diagnosis_pending': False, 'recovery_pending': False, 'learning_pending': pending}
        except Exception:  # noqa: BLE001 — failed revalidation creates a new report, not a hidden retry loop
            result = {'halt': '조치 후 재검사 조건을 확인하지 못했습니다. 소유권·정리·검사 근거를 다시 확인하세요.',
                      'failure_type': 'Stopped', 'failure_code': 'manual_revalidation_failed',
                      'manual_count': min(count + 1, MANUAL_LIMIT)}
            name = state['diagnosis_request']['node_id']
            return prepare_exception(self.deps, name, result, 'halted', {**state, **result})

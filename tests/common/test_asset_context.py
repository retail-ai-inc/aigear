from datetime import datetime, timedelta, timezone
import multiprocessing

import pytest

from aigear.common.asset_context import RunOwnership, AssetContext, active_context
from aigear.db.bucket import LocalGCSMock
from aigear.management.asset_errors import ExecutionConflict, OperationIncomplete

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


@pytest.fixture
def ownership(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bucket = LocalGCSMock('project', 'train')
    yield RunOwnership(bucket, 'v1')
    bucket.close()


def reserve(ownership):
    e = ownership.acquire('run-a', NOW)
    return ownership.prepare_step(e.run_id, e.owner_token, 'management')


def test_same_version_exclusive_other_versions_independent(ownership):
    e = reserve(ownership)
    for run in ['run-a', 'run-b']:
        with pytest.raises(ExecutionConflict):
            ownership.acquire(run, NOW + timedelta(days=1))
    assert RunOwnership(ownership.bucket, 'v2').acquire('run-b', NOW)
    assert ownership.prepare_step(e.run_id, e.owner_token, e.current_step) == e


def test_claim_is_once_per_worker_and_context_cannot_be_forged(ownership):
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        assert active_context() is context
        fake = AssetContext(ownership, e.run_id, e.owner_token, e.current_step, e.step_attempt_token, context.worker)
        with pytest.raises(ExecutionConflict):
            fake.current()
        with pytest.raises(ExecutionConflict):
            with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token):
                pass
    with pytest.raises(ExecutionConflict):
        active_context()
    with pytest.raises(ExecutionConflict):
        with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token):
            pass


def test_commit_handoff_and_management_dedup(ownership):
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        context.commit()
    assert ownership.prepare_step(e.run_id, e.owner_token, e.current_step) is None
    with pytest.raises(ExecutionConflict):
        ownership.acquire('run-b', NOW + timedelta(days=1))
    second = ownership.prepare_step(e.run_id, e.owner_token, 'train')
    assert second.step_attempt_token != e.step_attempt_token
    with pytest.raises(ExecutionConflict):
        ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=lambda m: None)
    with ownership.claim(second.run_id, second.owner_token, second.current_step, second.step_attempt_token) as context:
        context.commit()
    ownership.finalize(second.run_id, second.owner_token, second.current_step, second.step_attempt_token,
                       succeeded=True, seal=lambda m: None)
    manifest, _ = ownership.read()
    assert manifest.execution.status == 'succeeded'
    assert manifest.execution.committed_steps == ['management', 'train']


@pytest.mark.parametrize('succeeded', [True, False])
def test_terminal_sealing_failure_keeps_owner_retry_and_late_terminal(ownership, succeeded):
    e = reserve(ownership)
    if succeeded:
        with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
            context.commit()
    def broken(manifest):
        raise RuntimeError('interrupted seal')
    with pytest.raises(OperationIncomplete) as failure:
        ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=succeeded, seal=broken)
    assert isinstance(failure.value.cause, RuntimeError)
    assert ownership.read()[0].execution.status == 'finalizing'
    with pytest.raises(ExecutionConflict):
        ownership.acquire('run-b', NOW + timedelta(seconds=1))
    def finish():
        return ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token,
                                  succeeded=succeeded, seal=lambda m: None)
    assert finish().execution.status == ('succeeded' if succeeded else 'failed')
    revision = finish().revision
    assert finish().revision == revision
    with pytest.raises(ExecutionConflict):
        ownership.acquire(e.run_id, NOW + timedelta(seconds=1))
    for timestamp in [NOW, NOW - timedelta(seconds=1)]:
        with pytest.raises(ExecutionConflict):
            ownership.acquire('run-b', timestamp)
    new = ownership.acquire('run-b', NOW + timedelta(seconds=1))
    with pytest.raises(ExecutionConflict):
        finish()
    assert ownership.read()[0].execution.owner_token == new.owner_token


def test_recovery_requires_stopped_rotates_attempt_and_fences_old_worker(ownership):
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        with pytest.raises(ExecutionConflict):
            ownership.recover_stopped(e.run_id, e.owner_token, e.current_step, e.step_attempt_token,
                                      confirm_stopped=lambda e: False)
        recovered = ownership.recover_stopped(e.run_id, e.owner_token, e.current_step, e.step_attempt_token,
                                              confirm_stopped=lambda e: True)
        with pytest.raises(ExecutionConflict):
            context.commit()
    assert recovered.step_attempt_token != e.step_attempt_token
    with ownership.claim(recovered.run_id, recovered.owner_token, recovered.current_step, recovered.step_attempt_token) as context:
        context.fail()
    ownership.finalize(recovered.run_id, recovered.owner_token, recovered.current_step, recovered.step_attempt_token,
                       succeeded=False, seal=lambda m: None)


def test_pending_writes_are_never_discarded(ownership):
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        manifest, generation = context.current()
        manifest.pending_step.writes_started = True
        ownership._write(manifest, generation, 'test-write')
        for action in [context.commit, context.fail]:
            with pytest.raises(OperationIncomplete):
                action()
        with pytest.raises(OperationIncomplete):
            ownership.recover_stopped(e.run_id, e.owner_token, e.current_step, e.step_attempt_token,
                                      confirm_stopped=lambda e: True)
        assert ownership.read()[0].pending_step.writes_started
        context.fail(cleanup=lambda m: setattr(m.pending_step, 'writes_started', False))


def test_success_cannot_skip_commit(ownership):
    e = reserve(ownership)
    with pytest.raises(ExecutionConflict):
        ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=True, seal=lambda m: None)


@pytest.mark.parametrize('success', [True, False])
def test_finished_attempt_cannot_be_claimed_again(ownership, success):
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        context.commit() if success else context.fail()
    with pytest.raises(ExecutionConflict):
        with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token):
            pass


def test_management_operation_reentry_and_failure_releases_only_own_run(ownership):
    with ownership.management_operation('run-a', NOW, seal=lambda m: None) as outer:
        with ownership.management_operation('nested', NOW, seal=lambda m: None) as nested:
            assert nested is outer
        assert outer.current()[0].execution.status == 'running'
        other = RunOwnership(ownership.bucket, 'v2')
        with pytest.raises(ExecutionConflict):
            with other.management_operation('other', NOW, seal=lambda m: None):
                pass
        assert other.read()[1] == '0'
    assert ownership.read()[0].execution.status == 'succeeded'
    with pytest.raises(RuntimeError, match='operation failed'):
        with ownership.management_operation('run-b', NOW + timedelta(days=1), seal=lambda m: None):
            raise RuntimeError('operation failed')
    assert ownership.read()[0].execution.status == 'failed'
    assert ownership.acquire('run-c', NOW + timedelta(days=2))


def test_cas_failure_preserves_winner_and_cause(ownership, monkeypatch):
    first, generation = ownership.read()
    ownership.acquire('winner', NOW)
    with pytest.raises(ExecutionConflict) as failure:
        ownership._write(first, generation, 'acquire')
    assert failure.value.cause is not None
    assert ownership.read()[0].execution.run_id == 'winner'


def test_transition_logs_identify_attempt_without_exposing_capabilities(ownership, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='aigear.common.asset_context')
    e = reserve(ownership)
    with ownership.claim(e.run_id, e.owner_token, e.current_step, e.step_attempt_token) as context:
        context.fail()
    records = [record for record in caplog.records if record.name == 'aigear.common.asset_context']
    failure = next(record for record in records if record.phase == 'failure')
    assert failure.run_id == 'run-a' and failure.step_name == 'management'
    assert failure.failure_layer == 'pipeline' and failure.attempt_id
    diagnostics = repr([record.__dict__ for record in records])
    assert e.owner_token not in diagnostics and e.step_attempt_token not in diagnostics


def test_terminal_persist_failure_can_retry_without_releasing(ownership, monkeypatch):
    e = reserve(ownership)
    original = ownership.bucket.write_json
    def fail_release(key, value, **kwargs):
        if value['execution']['status'] == 'failed':
            raise OSError('interrupted release')
        return original(key, value, **kwargs)
    monkeypatch.setattr(ownership.bucket, 'write_json', fail_release)
    with pytest.raises(OSError):
        ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=lambda m: None)
    assert ownership.read()[0].execution.status == 'finalizing'
    monkeypatch.setattr(ownership.bucket, 'write_json', original)
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=lambda m: None)


def _race_claim(directory, identity, ready, start, results):
    import os
    os.chdir(directory)
    ownership = RunOwnership(LocalGCSMock('project', 'train'), 'v1')
    ready.put(True)
    start.wait(20)
    try:
        with ownership.claim(*identity):
            results.put('claimed')
    except ExecutionConflict:
        results.put('conflict')


def test_independent_processes_only_one_worker_claims(ownership, tmp_path):
    e = reserve(ownership)
    ctx = multiprocessing.get_context('spawn')
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    identity = (e.run_id, e.owner_token, e.current_step, e.step_attempt_token)
    processes = [ctx.Process(target=_race_claim, args=(str(tmp_path), identity, ready, start, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=20)
        start.set()
        assert sorted(results.get(timeout=20) for _ in processes) == ['claimed', 'conflict']
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()

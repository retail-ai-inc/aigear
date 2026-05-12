import json
from unittest.mock import patch

from aigear.deploy.gcp.scheduler import Scheduler, _build_step_message


def _make_scheduler(**kwargs):
    defaults = dict(
        name="my-job",
        location="asia-northeast1",
        project_id="my-project",
        schedule="0 9 * * *",
        topic_name="my-topic",
        message={"key": "value"},
        time_zone="Asia/Tokyo",
    )
    defaults.update(kwargs)
    return Scheduler(**defaults)


# ── Scheduler.create ──────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_create_builds_pubsub_command(mock_run_sh):
    mock_run_sh.return_value = "created"
    s = _make_scheduler()
    s.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "scheduler" in cmd
    assert "jobs" in cmd
    assert "create" in cmd
    assert "pubsub" in cmd
    assert "my-job" in cmd


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_create_serializes_message_as_json(mock_run_sh):
    mock_run_sh.return_value = "created"
    s = _make_scheduler(message={"key": "value"})
    s.create()
    cmd = mock_run_sh.call_args[0][0]
    msg_idx = cmd.index("--message-body") + 1
    assert json.loads(cmd[msg_idx]) == {"key": "value"}


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_create_includes_schedule_and_topic(mock_run_sh):
    mock_run_sh.return_value = "created"
    s = _make_scheduler()
    s.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "--schedule" in cmd
    assert "0 9 * * *" in cmd
    assert "--topic" in cmd
    assert "my-topic" in cmd


# ── Scheduler.describe ────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_describe_returns_exists_true_and_state_when_enabled(mock_run_sh):
    mock_run_sh.return_value = "state: ENABLED\nschedule: 0 9 * * *\ntimeZone: Asia/Tokyo"
    s = _make_scheduler()
    exists, state = s.describe()
    assert exists is True
    assert state == "ENABLED"


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_describe_returns_exists_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    s = _make_scheduler()
    exists, state = s.describe()
    assert exists is False
    assert state == ""


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_describe_returns_exists_true_and_state_when_paused(mock_run_sh):
    mock_run_sh.return_value = "state: PAUSED\nschedule: 0 9 * * *"
    s = _make_scheduler()
    exists, state = s.describe()
    assert exists is True
    assert state == "PAUSED"


# ── Scheduler.delete ──────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_delete_passes_yes_to_confirmation_prompt(mock_run_sh):
    mock_run_sh.return_value = "deleted"
    s = _make_scheduler()
    s.delete()
    positional_args = mock_run_sh.call_args[0]
    assert positional_args[1] == "yes\n"


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = "deleted"
    s = _make_scheduler()
    s.delete()
    cmd = mock_run_sh.call_args[0][0]
    assert "jobs" in cmd
    assert "delete" in cmd
    assert "my-job" in cmd


# ── Scheduler.update ──────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_update_includes_schedule_and_topic(mock_run_sh):
    mock_run_sh.return_value = "updated"
    s = _make_scheduler()
    s.update()
    cmd = mock_run_sh.call_args[0][0]
    assert "update" in cmd
    assert "pubsub" in cmd
    assert "--schedule" in cmd
    assert "--topic" in cmd


# ── Scheduler.run ─────────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_run_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    s = _make_scheduler()
    s.run()
    cmd = mock_run_sh.call_args[0][0]
    assert "jobs" in cmd
    assert "run" in cmd
    assert "my-job" in cmd


# ── Scheduler.pause / resume ──────────────────────────────────────────────────

@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_pause_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    s = _make_scheduler()
    s.pause()
    cmd = mock_run_sh.call_args[0][0]
    assert "pause" in cmd
    assert "my-job" in cmd


@patch("aigear.deploy.gcp.scheduler.run_sh")
def test_resume_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    s = _make_scheduler()
    s.resume()
    cmd = mock_run_sh.call_args[0][0]
    assert "resume" in cmd
    assert "my-job" in cmd


# ── _build_step_message ───────────────────────────────────────────────────────

def test_build_step_message_includes_docker_image_and_version():
    step_config = {"resources": {"vm_name": "my-vm"}}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="my-image:v1",
        gke_cluster="my-cluster",
        gke_zone="asia-northeast1",
    )
    assert msg["docker_image"] == "my-image:v1"
    assert msg["pipeline_version"] == "v1"


def test_build_step_message_merges_resources():
    step_config = {"resources": {"vm_name": "my-vm", "spec": "e2-standard-4"}}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="img",
        gke_cluster="c",
        gke_zone="z",
    )
    assert msg["vm_name"] == "my-vm"
    assert msg["spec"] == "e2-standard-4"


def test_build_step_message_includes_step_name_for_workflow():
    step_config = {}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="img",
        gke_cluster="c",
        gke_zone="z",
        step_name="preprocess",
    )
    assert msg["step_name"] == "preprocess"


def test_build_step_message_omits_step_name_when_none():
    step_config = {}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="img",
        gke_cluster="c",
        gke_zone="z",
    )
    assert "step_name" not in msg


def test_build_step_message_includes_gke_fields_for_model_service():
    step_config = {"model_class_path": "my.module.MyModel", "resources": {}}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="ms-image:v1",
        gke_cluster="my-cluster",
        gke_zone="asia-northeast1",
        env="staging",
    )
    assert msg["model_class_path"] == "my.module.MyModel"
    assert msg["gke_cluster"] == "my-cluster"
    assert msg["gke_zone"] == "asia-northeast1"
    assert msg["env"] == "staging"


def test_build_step_message_omits_gke_fields_for_workflow_step():
    step_config = {"resources": {}}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="pl-image:v1",
        gke_cluster="my-cluster",
        gke_zone="asia-northeast1",
        step_name="preprocess",
    )
    assert "gke_cluster" not in msg
    assert "gke_zone" not in msg
    assert "model_class_path" not in msg


def test_build_step_message_includes_venv_when_provided():
    step_config = {}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="img",
        gke_cluster="c",
        gke_zone="z",
        venv="my-venv",
    )
    assert msg["venv"] == "my-venv"


def test_build_step_message_omits_venv_when_none():
    step_config = {}
    msg = _build_step_message(
        step_config=step_config,
        pipeline_version="v1",
        docker_image="img",
        gke_cluster="c",
        gke_zone="z",
    )
    assert "venv" not in msg

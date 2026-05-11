from unittest.mock import MagicMock

from aigear.infrastructure.gcp.infra import Infra


def _make_infra():
    """Return an Infra instance with all GCP dependencies mocked out."""
    infra = Infra.__new__(Infra)
    # Mock config
    cfg = MagicMock()
    cfg.gcp.cloud_build.on = True
    cfg.gcp.cloud_build.trigger_name = "my-trigger"
    cfg.gcp.kubernetes.on = True
    cfg.gcp.kubernetes.cluster_name = "my-cluster"
    infra.aigear_config = cfg
    infra.location = "asia-northeast1"
    # Mock resource objects
    infra.cloud_build = MagicMock()
    infra.kubernetes_cluster = MagicMock()
    return infra


def test_update_cloud_build_calls_update_when_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = True

    infra._update_cloud_build()

    infra.cloud_build.update.assert_called_once()


def test_update_cloud_build_skips_when_not_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = False

    infra._update_cloud_build()

    infra.cloud_build.update.assert_not_called()


def test_update_kubernetes_calls_update_when_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = True

    infra._update_kubernetes()

    infra.kubernetes_cluster.update.assert_called_once()


def test_update_kubernetes_skips_when_not_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = False

    infra._update_kubernetes()

    infra.kubernetes_cluster.update.assert_not_called()

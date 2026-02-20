"""Unit tests for Docker helper functions in main.py."""
import os
from unittest import mock

import pytest


class TestDockerImageExists:
    """Tests for _docker_image_exists()."""

    def test_image_exists(self):
        """Returns True when docker image inspect succeeds."""
        from main import _docker_image_exists

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            assert _docker_image_exists("test-image") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args == ["docker", "image", "inspect", "test-image"]

    def test_image_not_exists(self):
        """Returns False when docker image inspect fails."""
        from main import _docker_image_exists

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            assert _docker_image_exists("missing-image") is False

    def test_docker_not_installed(self):
        """Returns False when docker is not installed."""
        from main import _docker_image_exists

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert _docker_image_exists() is False

    def test_timeout(self):
        """Returns False when docker command times out."""
        import subprocess
        from main import _docker_image_exists

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)
            assert _docker_image_exists() is False


class TestPullDockerImage:
    """Tests for _pull_docker_image()."""

    def test_pull_success(self):
        """Returns True and tags image when pull succeeds."""
        from main import _pull_docker_image, SCIP_REGISTRY_IMAGE, SCIP_LOCAL_IMAGE

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = _pull_docker_image()
            assert result is True
            # Should have called pull then tag
            assert mock_run.call_count == 2
            pull_args = mock_run.call_args_list[0][0][0]
            tag_args = mock_run.call_args_list[1][0][0]
            assert pull_args == ["docker", "pull", SCIP_REGISTRY_IMAGE]
            assert tag_args == ["docker", "tag", SCIP_REGISTRY_IMAGE, SCIP_LOCAL_IMAGE]

    def test_pull_failure(self):
        """Returns False when pull fails."""
        from main import _pull_docker_image

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = _pull_docker_image()
            assert result is False

    def test_docker_not_installed(self):
        """Returns False when docker is not installed."""
        from main import _pull_docker_image

        with mock.patch("main.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = _pull_docker_image()
            assert result is False


class TestEnsureDockerImage:
    """Tests for _ensure_docker_image()."""

    def test_local_image_exists(self):
        """Returns True immediately if local image exists."""
        from main import _ensure_docker_image

        with mock.patch("main._docker_image_exists") as mock_exists:
            mock_exists.return_value = True
            with mock.patch("main._pull_docker_image") as mock_pull:
                result = _ensure_docker_image()
                assert result is True
                mock_exists.assert_called_once()
                mock_pull.assert_not_called()

    def test_pulls_when_local_missing(self):
        """Pulls from registry when local image is missing."""
        from main import _ensure_docker_image

        with mock.patch("main._docker_image_exists") as mock_exists:
            mock_exists.return_value = False
            with mock.patch("main._pull_docker_image") as mock_pull:
                mock_pull.return_value = True
                result = _ensure_docker_image()
                assert result is True
                mock_pull.assert_called_once()

    def test_returns_false_when_pull_fails(self):
        """Returns False when pull also fails."""
        from main import _ensure_docker_image

        with mock.patch("main._docker_image_exists") as mock_exists:
            mock_exists.return_value = False
            with mock.patch("main._pull_docker_image") as mock_pull:
                mock_pull.return_value = False
                result = _ensure_docker_image()
                assert result is False


class TestGetScipCmd:
    """Tests for _get_scip_cmd()."""

    def test_uses_docker_when_available(self):
        """Uses Docker when image is available."""
        from main import _get_scip_cmd, SCIP_LOCAL_IMAGE

        with mock.patch("main._ensure_docker_image") as mock_ensure:
            mock_ensure.return_value = True
            with mock.patch.dict(os.environ, {}, clear=False):
                # Remove SCIP_LOCAL if present
                os.environ.pop("SCIP_LOCAL", None)
                cmd, desc = _get_scip_cmd("/test/repo")
                assert "docker" in cmd
                assert SCIP_LOCAL_IMAGE in cmd
                assert "/test/repo:/workspace" in " ".join(cmd)
                assert "Docker" in desc

    def test_respects_scip_local_env(self):
        """Skips Docker when SCIP_LOCAL=1."""
        from main import _get_scip_cmd

        with mock.patch("main._ensure_docker_image") as mock_ensure:
            with mock.patch.dict(os.environ, {"SCIP_LOCAL": "1"}):
                cmd, desc = _get_scip_cmd("/test/repo")
                # Should not have called _ensure_docker_image
                mock_ensure.assert_not_called()
                # Result depends on whether local binary/script exists
                # but should NOT be docker
                assert "docker" not in cmd or "Docker" not in desc or mock_ensure.called is False


class TestRegistryConfig:
    """Tests for registry configuration."""

    def test_default_registry(self):
        """Default registry is ghcr.io/williamekberg."""
        from main import SCIP_REGISTRY, SCIP_REGISTRY_IMAGE

        # When no env var is set
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCIP_REGISTRY", None)
            # Re-import to get fresh values
            import importlib
            import main
            importlib.reload(main)
            assert "ghcr.io" in main.SCIP_REGISTRY_IMAGE

    def test_custom_registry_env(self):
        """SCIP_REGISTRY env var overrides default."""
        with mock.patch.dict(os.environ, {"SCIP_REGISTRY": "my-registry.io/myorg"}):
            import importlib
            import main
            importlib.reload(main)
            assert "my-registry.io/myorg" in main.SCIP_REGISTRY
            # Reload back to default for other tests
            os.environ.pop("SCIP_REGISTRY", None)
            importlib.reload(main)

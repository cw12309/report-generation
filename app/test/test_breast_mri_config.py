import os
import sys
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from config import get_breast_mri_host, get_breast_mri_port, get_breast_mri_base_url


class TestBreastMRIConfig:
    def test_default_port(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_breast_mri_port() == 4173

    def test_custom_port(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "4174"}, clear=True):
            assert get_breast_mri_port() == 4174

    def test_default_host(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_breast_mri_host() == "127.0.0.1"

    def test_custom_host(self):
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "0.0.0.0"}, clear=True):
            assert get_breast_mri_host() == "0.0.0.0"

    def test_default_base_url(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_breast_mri_base_url() == "http://127.0.0.1:4173"

    def test_custom_port_base_url(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "4174"}, clear=True):
            assert get_breast_mri_base_url() == "http://127.0.0.1:4174"

    def test_custom_host_base_url(self):
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "0.0.0.0"}, clear=True):
            assert get_breast_mri_base_url() == "http://0.0.0.0:4173"

    def test_custom_host_and_port_base_url(self):
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "0.0.0.0", "BREAST_MRI_PORT": "4175"}, clear=True):
            assert get_breast_mri_base_url() == "http://0.0.0.0:4175"

    def test_base_url_no_trailing_slash(self):
        url = get_breast_mri_base_url()
        assert not url.endswith("/")


class TestBreastMRILauncher:
    def test_port_conflict_raises_runtime_error(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        with patch.dict(os.environ, {}, clear=True):
            from services.breast_mri_report.launcher import BreastMRILauncher
            launcher = BreastMRILauncher()

            with patch.object(launcher, "_check_node"):
                with patch.object(launcher, "_port_in_use", return_value=True):
                    with patch.object(launcher, "_verify_service", return_value=False):
                        with pytest.raises(RuntimeError, match="BREAST_MRI_PORT_CONFLICT"):
                            launcher.start()

    def test_port_conflict_message_contains_port(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "4174"}, clear=True):
            from services.breast_mri_report.launcher import BreastMRILauncher
            launcher = BreastMRILauncher()

            with patch.object(launcher, "_check_node"):
                with patch.object(launcher, "_port_in_use", return_value=True):
                    with patch.object(launcher, "_verify_service", return_value=False):
                        with pytest.raises(RuntimeError, match="4174"):
                            launcher.start()

    def test_known_service_reuses_without_kill(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        with patch.dict(os.environ, {}, clear=True):
            from services.breast_mri_report.launcher import BreastMRILauncher
            launcher = BreastMRILauncher()

            with patch.object(launcher, "_check_node"):
                with patch.object(launcher, "_port_in_use", return_value=True):
                    with patch.object(launcher, "_verify_service", return_value=True):
                        with patch.object(launcher, "_start_process") as mock_start:
                            launcher.start()
                            mock_start.assert_not_called()

    def test_no_kill_port_occupant_method(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from services.breast_mri_report.launcher import BreastMRILauncher
        assert not hasattr(BreastMRILauncher, "_kill_port_occupant"), (
            "_kill_port_occupant 方法应该已被删除"
        )
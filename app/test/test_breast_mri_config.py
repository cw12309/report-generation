import os
import sys
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest


# =========================================================================
# 工具函数
# =========================================================================

def _make_resolve_patch(host="127.0.0.1", port=4173):
    """返回一个 _resolve mock，模拟 config.yml 的 breast_mri 配置。"""

    def _resolve(path, default=None):
        if path == "breast_mri.host":
            return host
        if path == "breast_mri.port":
            return port
        return default

    return _resolve


# =========================================================================
# TestBreastMRIConfig — 配置优先级 / 校验
# =========================================================================

class TestBreastMRIConfig:

    # ── Case A: env 不存在，使用 config.yml 默认值 ──────────────────────

    def test_case_a_yaml_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
                from config import get_breast_mri_host, get_breast_mri_port, get_breast_mri_base_url
                assert get_breast_mri_host() == "127.0.0.1"
                assert get_breast_mri_port() == 4173
                assert get_breast_mri_base_url() == "http://127.0.0.1:4173"

    # ── Case B: env 为空，仍使用 config.yml ─────────────────────────────

    def test_case_b_env_empty_uses_yaml(self):
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "", "BREAST_MRI_PORT": ""}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
                from config import get_breast_mri_host, get_breast_mri_port, get_breast_mri_base_url
                assert get_breast_mri_host() == "127.0.0.1"
                assert get_breast_mri_port() == 4173
                assert get_breast_mri_base_url() == "http://127.0.0.1:4173"

    # ── Case C: env 非空，覆盖 config.yml ───────────────────────────────

    def test_case_c_env_overrides_yaml(self):
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "localhost", "BREAST_MRI_PORT": "4174"}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
                from config import get_breast_mri_host, get_breast_mri_port, get_breast_mri_base_url
                assert get_breast_mri_host() == "localhost"
                assert get_breast_mri_port() == 4174
                assert get_breast_mri_base_url() == "http://localhost:4174"

    # ── Case D: env 空，YAML 为 port=4175 ───────────────────────────────

    def test_case_d_env_empty_yaml_port_4175(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4175)):
                from config import get_breast_mri_port
                assert get_breast_mri_port() == 4175

    # ── Case E: env=4174，YAML=4175 → env 生效 ──────────────────────────

    def test_case_e_env_4174_overrides_yaml_4175(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "4174"}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4175)):
                from config import get_breast_mri_port
                assert get_breast_mri_port() == 4174

    # ── Case F: 非法端口 ────────────────────────────────────────────────

    def test_case_f_invalid_port_abc(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "abc"}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", "abc")):
                from config import get_breast_mri_port
                with pytest.raises(ValueError, match="BREAST_MRI_PORT.*无效"):
                    get_breast_mri_port()

    def test_case_f_invalid_port_zero(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "0"}, clear=True):
            from config import get_breast_mri_port
            with pytest.raises(ValueError, match="BREAST_MRI_PORT.*1-65535"):
                get_breast_mri_port()

    def test_case_f_invalid_port_65536(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "65536"}, clear=True):
            from config import get_breast_mri_port
            with pytest.raises(ValueError, match="BREAST_MRI_PORT.*1-65535"):
                get_breast_mri_port()

    def test_case_f_invalid_port_negative(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "-1"}, clear=True):
            from config import get_breast_mri_port
            with pytest.raises(ValueError, match="BREAST_MRI_PORT.*1-65535"):
                get_breast_mri_port()

    # ── Case G: YAML 缺失 host/port ────────────────────────────────────

    def test_case_g_yaml_host_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", return_value=None):
                from config import get_breast_mri_host
                with pytest.raises(ValueError, match="breast_mri.host"):
                    get_breast_mri_host()

    def test_case_g_yaml_port_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", return_value=None):
                from config import get_breast_mri_port
                with pytest.raises(ValueError, match="breast_mri.port"):
                    get_breast_mri_port()

    # ── 边界: 合法端口边界 ──────────────────────────────────────────────

    def test_valid_port_1(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "1"}, clear=True):
            from config import get_breast_mri_port
            assert get_breast_mri_port() == 1

    def test_valid_port_65535(self):
        with patch.dict(os.environ, {"BREAST_MRI_PORT": "65535"}, clear=True):
            from config import get_breast_mri_port
            assert get_breast_mri_port() == 65535

    # ── base_url 无尾部斜杠 ────────────────────────────────────────────

    def test_base_url_no_trailing_slash(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
                from config import get_breast_mri_base_url
                url = get_breast_mri_base_url()
                assert not url.endswith("/")

    def test_base_url_api_health_no_double_slash(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
                from config import get_breast_mri_base_url
                url = f"{get_breast_mri_base_url()}/api/health"
                assert url == "http://127.0.0.1:4173/api/health"


# =========================================================================
# TestBreastMRILauncher — 生命周期 / 端口冲突
# =========================================================================

class TestBreastMRILauncher:
    def test_port_conflict_raises_runtime_error(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        with patch.dict(os.environ, {}, clear=True):
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
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
            with patch("config._resolve", side_effect=_make_resolve_patch("127.0.0.1", 4173)):
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

    def test_launcher_uses_config_not_hardcoded(self):
        PROJECT_ROOT = Path(__file__).resolve().parents[2]
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        with patch.dict(os.environ, {"BREAST_MRI_HOST": "10.0.0.1", "BREAST_MRI_PORT": "4180"}, clear=True):
            from services.breast_mri_report.launcher import BreastMRILauncher
            launcher = BreastMRILauncher()
            assert launcher.host == "10.0.0.1"
            assert launcher.port == 4180
            assert launcher.base_url == "http://10.0.0.1:4180"
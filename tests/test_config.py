"""config 加载测试。"""
import os
import tempfile
import textwrap

import pytest

from feishu_relay_bot.config import Config, load_config


class TestConfigValidation:
    def test_minimal_config(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://localhost:8800
              api_key: test-key
            bots:
              - name: test-bot
                app_id: cli_test
                app_secret: secret123
        """))
        cfg = load_config(str(cfg_file))
        assert cfg.upstream.base_url == "http://localhost:8800"
        assert len(cfg.bots) == 1
        assert cfg.bots[0].name == "test-bot"

    def test_env_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "my-secret-key")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://localhost:8800
              api_key: ${TEST_API_KEY}
            bots:
              - name: bot1
                app_id: cli_aaa
                app_secret: sec
        """))
        cfg = load_config(str(cfg_file))
        assert cfg.upstream.api_key == "my-secret-key"

    def test_env_default(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: ${NONEXISTENT_VAR:-http://fallback:8800}
              api_key: test
            bots:
              - name: bot1
                app_id: cli_aaa
                app_secret: sec
        """))
        cfg = load_config(str(cfg_file))
        assert cfg.upstream.base_url == "http://fallback:8800"

    def test_trailing_slash_stripped(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://localhost:8800/
              api_key: test
            bots:
              - name: bot1
                app_id: cli_aaa
                app_secret: sec
        """))
        cfg = load_config(str(cfg_file))
        assert cfg.upstream.base_url == "http://localhost:8800"

    def test_no_bots_raises(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://localhost:8800
              api_key: test
            bots: []
        """))
        with pytest.raises(Exception):
            load_config(str(cfg_file))

    def test_effective_upstream_override(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://global:8800
              api_key: global-key
            bots:
              - name: bot1
                app_id: cli_aaa
                app_secret: sec
                upstream:
                  base_url: http://override:9900
        """))
        cfg = load_config(str(cfg_file))
        effective = cfg.effective_upstream_for(cfg.bots[0])
        assert effective.base_url == "http://override:9900"
        assert effective.api_key == "global-key"  # not overridden → inherited

    def test_center_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(textwrap.dedent("""\
            upstream:
              base_url: http://localhost:8800
              api_key: test
            bots:
              - name: bot1
                app_id: cli_aaa
                app_secret: sec
        """))
        cfg = load_config(str(cfg_file))
        assert cfg.center.enabled is True
        assert cfg.center.node_id == "auto"
        assert cfg.center.interval_s == 30

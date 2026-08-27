from __future__ import annotations

from pathlib import Path

from luxar.adapters.espidf_sdk_probe import EspIdfSdkProbe
from luxar.domain.agent.sdk_probe import (
    SdkMigrationSnippet,
    changed_api_names,
    missing_include_names,
)
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence


def _build_evidence(message: str) -> BuildEvidence:
    return BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=2,
        error_category="source",
        diagnostics=[
            BuildDiagnostic(
                file="main/main.c",
                line=3,
                column=10,
                severity="error",
                message=message,
            )
        ],
    )


def test_missing_include_names_extracts_header() -> None:
    evidence = _build_evidence("fatal error: driver/i2c.h: No such file or directory")
    assert missing_include_names(evidence) == ["driver/i2c.h"]


def test_missing_include_names_ignores_other_diagnostics() -> None:
    evidence = _build_evidence("error: expected ';' before '}'")
    assert missing_include_names(evidence) == []


def test_resolve_include_reports_existing_header(tmp_path: Path) -> None:
    include = tmp_path / "components" / "driver" / "include" / "driver"
    include.mkdir(parents=True)
    (include / "i2c_master.h").write_text("", encoding="utf-8")

    resolution = EspIdfSdkProbe().resolve_include(
        "driver/i2c_master.h",
        str(tmp_path),
    )

    assert resolution.exists is True


def test_resolve_include_suggests_candidates_when_missing(tmp_path: Path) -> None:
    include = tmp_path / "components" / "driver" / "include" / "driver"
    include.mkdir(parents=True)
    (include / "i2c_master.h").write_text("", encoding="utf-8")
    (include / "i2c_slave.h").write_text("", encoding="utf-8")

    resolution = EspIdfSdkProbe().resolve_include("driver/i2c.h", str(tmp_path))

    assert resolution.exists is False
    assert "driver/i2c_master.h" in resolution.candidates


def test_resolve_include_returns_empty_for_missing_sdk(tmp_path: Path) -> None:
    resolution = EspIdfSdkProbe().resolve_include("driver/i2c.h", str(tmp_path))

    assert resolution.exists is False
    assert resolution.candidates == []


class _FakeProbe:
    def __init__(self, resolution):
        self._resolution = resolution

    def resolve_include(self, include_name, idf_path):
        return self._resolution


def test_sdk_include_hints_guides_repair() -> None:
    from luxar.application.agent_graph import _sdk_include_hints
    from luxar.domain.agent.sdk_probe import SdkIncludeResolution

    evidence = _build_evidence(
        "fatal error: driver/i2c.h: No such file or directory"
    ).model_copy(update={"idf_path": "C:/esp/esp-idf"})
    probe = _FakeProbe(
        SdkIncludeResolution(
            include_name="driver/i2c.h",
            exists=False,
            candidates=["driver/i2c_master.h"],
        )
    )

    hints = _sdk_include_hints(probe, evidence)

    assert any("driver/i2c_master.h" in hint for hint in hints)
    assert any("不存在" in hint for hint in hints)


def test_sdk_include_hints_returns_empty_without_probe() -> None:
    from luxar.application.agent_graph import _sdk_include_hints

    evidence = _build_evidence(
        "fatal error: driver/i2c.h: No such file or directory"
    )

    assert _sdk_include_hints(None, evidence) == []


def test_changed_api_names_extracts_symbols() -> None:
    evidence = _build_evidence(
        "warning: implicit declaration of function 'i2c_param_config' "
        "[-Wimplicit-function-declaration]"
    )
    assert changed_api_names(evidence) == ["i2c_param_config"]


def test_changed_api_names_handles_deprecated_quoted() -> None:
    evidence = _build_evidence(
        "'i2c_param_config' is deprecated [-Wdeprecated-declarations]"
    )
    assert changed_api_names(evidence) == ["i2c_param_config"]


class _FakeMigrationProbe:
    def search_migration(self, api_name, idf_path, limit=3):
        return [
            SdkMigrationSnippet(
                guide="docs/en/migration-guides/release-5.x/5.0.rst",
                snippet=f"{api_name} 已移除，请改用 i2c_master_bus_add_device。",
            )
        ]


def test_sdk_api_hints_guides_repair() -> None:
    from luxar.application.agent_graph import _sdk_api_hints

    evidence = _build_evidence(
        "warning: implicit declaration of function 'i2c_param_config'"
    ).model_copy(update={"idf_path": "C:/esp/esp-idf"})

    hints = _sdk_api_hints(_FakeMigrationProbe(), evidence)

    assert any("i2c_master_bus_add_device" in hint for hint in hints)
    assert any("迁移指南" in hint for hint in hints)


def test_search_migration_returns_snippets(tmp_path: Path) -> None:
    guides = tmp_path / "docs" / "en" / "migration-guides" / "release-5.x"
    guides.mkdir(parents=True)
    (guides / "5.0.rst").write_text(
        ".. _i2c:\n\nLegacy i2c_param_config was removed in ESP-IDF v5.\n",
        encoding="utf-8",
    )

    snippets = EspIdfSdkProbe().search_migration(
        "i2c_param_config",
        str(tmp_path),
    )

    assert len(snippets) == 1
    assert "i2c_param_config" in snippets[0].snippet
    assert snippets[0].guide.endswith("5.0.rst")

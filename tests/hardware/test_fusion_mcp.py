import importlib.util
from pathlib import Path

# hardware/enclosure/tools はパッケージではないので、ファイルの場所から読み込む
_PATH = Path(__file__).resolve().parents[2] / "hardware/enclosure/tools/fusion_mcp.py"
_spec = importlib.util.spec_from_file_location("fusion_mcp", _PATH)
fusion_mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fusion_mcp)


def test_build_script_source_drops_future_import_and_sets_out_dir(tmp_path):
    source = fusion_mcp.build_script_source(out_dir=tmp_path / "out")
    assert "from __future__ import annotations" not in source
    assert f"_DEFAULT_OUT = {str(tmp_path / 'out')!r}" in source
    assert fusion_mcp.DEFAULT_OUT_LINE not in source
    # 書き換えたあとも Python として読める（MCP 側で構文エラーにならない）
    compile(source, "futamaru_pot.py", "exec")


def test_build_script_source_fails_when_out_line_is_missing(tmp_path):
    script = tmp_path / "x.py"
    script.write_text("from __future__ import annotations\nprint(1)\n", encoding="utf-8")
    try:
        fusion_mcp.build_script_source(script_path=script, out_dir=tmp_path)
    except RuntimeError as e:
        assert "書き出し先" in str(e)
    else:
        raise AssertionError("RuntimeError が出なかった")


def test_main_without_command_prints_usage(capsys):
    assert fusion_mcp.main(["fusion_mcp.py"]) == 2
    assert "使い方" in capsys.readouterr().out

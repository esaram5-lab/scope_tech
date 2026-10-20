"""상태 정리: 매 측정 시작 전 통신·오류·남은 Act On Event·결과 바 정리 ([measure] reset_each_run)."""
from __future__ import annotations


def run(bench) -> None:
    if bench.cfg.getbool("measure", "reset_each_run"):
        bench.scope.reset_state()
        bench.echo("  스코프 상태 정리 완료(통신 버퍼·오류·Act On Event)")

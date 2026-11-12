from types import SimpleNamespace

from api.research_runs import response_run_status


def test_recovered_synthesis_is_persisted_as_completed():
    response = SimpleNamespace(
        summary="结论：已有证据支持长期分批观察。",
        payload={"_meta": {
            "llm_research_recovery": True,
            "evidence_synthesis_recovery": True,
            "synthesis_incomplete": False,
        }},
    )
    assert response_run_status(response, "incomplete") == "completed"


def test_unrecovered_synthesis_keeps_incomplete_status():
    response = SimpleNamespace(
        summary="研究汇总未能完成。",
        payload={"_meta": {"synthesis_incomplete": True}},
    )
    assert response_run_status(response, "incomplete") == "incomplete"

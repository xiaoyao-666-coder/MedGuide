"""Issue #2: leg/joint pain must not cite UTI material.

Root cause: BM25 matched the UTI document through the generic token
"疼痛" alone (and cold/cough/diarrhoea documents through "持续"), so a
question about a topic the knowledge base does not cover was answered
with unrelated material presented as the most relevant source.

The knowledge base gained a knee-joint document on 2026-11-20
(disease-knee-001, NHS knee pain page), so leg/joint pain now has
topical coverage: the gate still blocks the UTI document, but the
knee document is cited instead of answering "insufficient-data".
"""
from app.retrieval import HybridRetriever
from app.safety import SafetyEngine
from app.sql_guard import ReadOnlySQLGuard
from app.workflow import MedGuideWorkflow


def make_workflow() -> MedGuideWorkflow:
    return MedGuideWorkflow(HybridRetriever(), ReadOnlySQLGuard(), SafetyEngine())


def _ids(result: dict) -> list[str]:
    return [item["id"] for item in result.get("citations", [])]


# --- retrieval level: leg/joint pain now has topical coverage ---

def test_leg_pain_queries_retrieve_knee_document() -> None:
    # Knowledge base expanded 2026-11-20 (disease-knee-001): these queries
    # now cite the knee document instead of returning no citations. The
    # UTI document must still never appear for them.
    retriever = HybridRetriever()
    for query in ("腿关节持续疼痛", "腿关节时长疼痛", "膝盖疼了几天"):
        results = retriever.search(query, "disease")
        assert results, query
        assert results[0].id.startswith("disease-knee-001"), query
        assert not any(item.id.startswith("disease-uti-001") for item in results), query


def test_severity_only_query_returns_no_citations() -> None:
    # "严重" alone justified citing the loratadine/emergency documents.
    assert HybridRetriever().search("掉头发很严重", "unknown") == []


def test_urinary_query_still_retrieves_uti_document() -> None:
    results = HybridRetriever().search("尿频尿急并伴排尿疼痛", "disease")
    assert results
    assert results[0].id.startswith("disease-uti-001")


def test_topical_queries_are_unaffected_by_the_gate() -> None:
    retriever = HybridRetriever()
    assert retriever.search("咳嗽", "disease")[0].id.startswith("disease-cough-001")
    assert retriever.search("头痛", "disease")[0].id.startswith("disease-headache-001")
    # "发烧" is only matched through the temperature token today; the gate
    # must not take that citation away.
    assert retriever.search("发烧38.5度三天", "unknown")[0].id.startswith("disease-fever-001")


# --- workflow level: knee document cited, no UTI citation ---

def test_leg_pain_answer_cites_knee_document() -> None:
    # Knowledge base expanded 2026-11-20: leg pain now has topical
    # coverage, so the workflow cites the knee document instead of
    # answering "insufficient-data". The UTI document stays out.
    result = make_workflow().run(
        {"session_id": "leg-pain", "user_message": "腿关节持续疼痛", "profile": {}}
    )
    assert any(item.startswith("disease-knee-001") for item in _ids(result))
    assert not any(item.startswith("disease-uti-001") for item in _ids(result))
    assert result["answer_source"] != "insufficient-data"
    assert "没有与你描述的情况直接相关的资料" not in result["response"]
    assert "尿路感染" not in result["response"]
    assert "尿频" not in result["response"]


def test_knee_pain_answer_cites_knee_document() -> None:
    result = make_workflow().run(
        {"session_id": "knee-pain", "user_message": "膝盖疼了几天", "profile": {}}
    )
    assert result["citations"]
    assert any(item.startswith("disease-knee-001") for item in _ids(result))
    assert result["answer_source"] != "insufficient-data"
    assert "没有与你描述的情况直接相关的资料" not in result["response"]
    assert "尿路感染" not in result["response"]


def test_urinary_query_workflow_still_cites_uti() -> None:
    result = make_workflow().run(
        {"session_id": "uti", "user_message": "尿频尿急并伴排尿疼痛", "profile": {}}
    )
    assert any(item.startswith("disease-uti-001") for item in _ids(result))
    assert result["answer_source"] != "insufficient-data"
    assert "尿路感染" in result["response"]


def test_cough_workflow_still_cites_cough_document() -> None:
    result = make_workflow().run(
        {"session_id": "cough", "user_message": "咳嗽", "profile": {}}
    )
    assert any(item.startswith("disease-cough-001") for item in _ids(result))
    assert result["answer_source"] != "insufficient-data"

from app.retrieval import HybridRetriever, IntentRouter, tokenize
import pytest


@pytest.mark.parametrize("query,document", [
    ("咳嗽超过三周怎么办", "disease-cough-001"),
    ("头痛头疼怎么办", "disease-headache-001"),
    ("腹泻拉肚子呕吐怎样补水", "disease-diarrhoea-001"),
    ("尿频尿急尿痛", "disease-uti-001"),
    # Knowledge base expanded 2026-11-20 (26 documents).
    ("腿关节时长疼痛", "disease-knee-001"),
    ("膝盖疼了几天", "disease-knee-001"),
    ("腰背痛怎么办", "disease-backpain-001"),
    ("胃痛肚子疼", "disease-stomachache-001"),
    ("咽痛喉咙痛怎么办", "disease-sorethroat-001"),
    ("头晕怎么办", "disease-dizziness-001"),
    ("流感发烧全身酸痛", "disease-flu-001"),
    ("过敏身上起疹子", "disease-allergy-001"),
    ("哮喘喘不上气", "disease-asthma-001"),
    ("失眠睡不着怎么办", "disease-insomnia-001"),
    ("牙痛怎么办", "disease-toothache-001"),
    ("耳痛耳朵疼", "disease-earache-001"),
])
def test_common_symptom_topics_retrieve_the_relevant_document(query, document):
    results = HybridRetriever().search(query, "disease", top_k=2)
    assert any(item.id.startswith(document) for item in results)


def test_router_rewrites_with_session_context() -> None:
    router = IntentRouter()
    assert router.classify("布洛芬有什么副作用") == "drug"
    assert "32岁" in router.rewrite("咳嗽", {"age": 32, "duration": "3天"})


def test_hybrid_retrieval_routes_and_returns_citations() -> None:
    retriever = HybridRetriever()
    results = retriever.search("咳嗽和咽痛怎么办", "disease", top_k=2)
    assert results
    assert all(item.category == "disease" for item in results)
    assert all(item.source and item.updated_at for item in results)
    assert "咳" in tokenize("咳嗽")

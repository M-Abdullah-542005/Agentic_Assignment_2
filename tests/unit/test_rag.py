"""Tests for Part B: ingestion, incremental indexing, every retrieval variant, factory, claim verification, eval."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
import yaml

from evals.runners.rag_eval import (load_dataset, mrr, ndcg_at_k, normalize_item, recall_at_k, run_benchmark, to_markdown,
                                    write_outputs)
from rag.embeddings import HashingEmbedder, get_embedder
from rag.ingest import build_index, load_persona_docs
from rag.ingest.chunking import chunk_fixed, chunk_structured
from rag.ingest.loaders import normalise_status, parse_case_study
from rag.retriever import RagConfig, Retriever, load_retriever
from rag.variants import VARIANTS
from rag.variants.base import rrf
from rag.variants.v1_decomp import extract_requirements
from rag.variants.v3_rerank import LexicalCrossScorer, get_cross_scorer
from rag.verify import PARTLY, SUPPORTED, UNSUPPORTED, verify_claims, verify_proposal
from tests.unit.rag_fixtures import EVIDENCE, case_study_md, make_dev_set, make_persona

JOB = {"title": "RAG chatbot engineer", "skills": ["RAG Systems", "LangGraph", "Qdrant"],
       "description": "We need a RAG chatbot built with LangGraph and Qdrant. Experience deploying with FastAPI is a plus."}
REAL_CS = """# Case Study CS-AI_-001: Full Stack Developer
**Domain**: AI & Machine Learning
**Skills Used**: RAG Systems, LangGraph, Qdrant
**Date**: 2024-02-10

### Client Requirement
Job Title: Full Stack DeveloperWe are seeking a talented and experienced Fullstack Developer...

### Architecture & Implementation
Architected a modular solution leveraging RAG Systems and LangGraph. Ensured strict adherence to client requirements with comprehensive unit testing and CI verification.

### Measurable Outcome
Delivered 100% on schedule with 45% measured performance optimization and positive 5-star client review.
"""


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    base = tmp_path_factory.mktemp("personas")
    make_persona(base, "ai_ml_engineer")
    return base


@pytest.fixture
def emb():
    return HashingEmbedder(512)


# ------------------------------------------------------------------ ingestion
def test_parse_real_case_study_format():
    cs = parse_case_study(REAL_CS, "x")
    assert cs["id"] == "CS-AI_-001" and cs["title"] == "Full Stack Developer"
    assert cs["meta"]["date"] == "2024-02-10" and cs["meta"]["skills used"] == "RAG Systems, LangGraph, Qdrant"
    assert [n for n, _ in cs["sections"]] == ["client requirement", "architecture & implementation", "measurable outcome"]


def test_required_metadata_on_every_chunk(corpus, emb):
    docs = load_persona_docs(corpus / "ai_ml_engineer")
    assert {d.type for d in docs} == {"case_study", "cv", "proposal"}
    for chunker in (chunk_structured, chunk_fixed):
        for d in docs:
            for c in chunker(d):
                assert c.persona == "ai_ml_engineer" and c.type and c.project and c.client_industry and c.text
                assert isinstance(c.skills, list) and c.chunk_id.startswith(d.source_id)
    cs = next(d for d in docs if d.evidence_id == "CS-AI_-001")
    assert cs.skills == ["RAG Systems", "LangGraph", "Qdrant"] and cs.date.startswith("2024-02-")
    assert "45%" in cs.outcome and cs.project == "Full Stack Developer"


def test_structured_chunks_split_by_section_and_fixed_by_size(corpus):
    cs = next(d for d in load_persona_docs(corpus / "ai_ml_engineer") if d.evidence_id == "CS-AI_-001")
    assert {c.section for c in chunk_structured(cs)} == {"client requirement", "architecture & implementation", "measurable outcome"}
    fixed = chunk_fixed(cs, size=200, overlap=40)
    assert len(fixed) > 1 and all(len(c.text) <= 200 for c in fixed) and {c.section for c in fixed} == {"fixed"}


def test_proposal_status_and_industry(corpus):
    docs = {d.evidence_id: d for d in load_persona_docs(corpus / "ai_ml_engineer")}
    assert docs["proposal:b-won"].outcome == "won" and docs["proposal:c-replied"].outcome == "replied"
    assert normalise_status("Hired") == "won" and normalise_status("no reply") == "ignored" and normalise_status(None) == "unknown"
    assert docs["CS-AI_-005"].client_industry == "design"      # "Technical Fashion Designer..."


def test_incremental_reindexing(tmp_path, emb):
    root = make_persona(tmp_path, "p1", n_case=5)
    idx, s1 = build_index(root, emb, "structured", tmp_path / "idx", "p1")
    assert len(s1.added) == len(idx.manifest) and s1.chunks_embedded == len(idx.chunks) and not s1.updated
    n0 = len(idx.chunks)
    _, s2 = build_index(root, emb, "structured", tmp_path / "idx", "p1")      # reload from disk, nothing changed
    assert s2.chunks_embedded == 0 and not (s2.added or s2.updated or s2.removed) and s2.unchanged == len(idx.manifest)
    new = json.loads(EVIDENCE.read_text())[5]                                  # a NEW case study appears
    ev = json.loads((root / "portfolio_evidence.json").read_text()); ev.append(new)
    (root / "portfolio_evidence.json").write_text(json.dumps(ev))
    (root / "case_studies" / f"{new['evidence_id']}.md").write_text(case_study_md(new))
    idx3, s3 = build_index(root, emb, "structured", tmp_path / "idx", "p1")
    assert s3.added == [f"case:{new['evidence_id']}"] and 0 < s3.chunks_embedded < n0 and not s3.updated
    p = root / "proposals" / "history.json"                                    # a NEW proposal is added
    props = json.loads(p.read_text()); props.append({"proposal_id": "d-new", "status": "won", "job_title": "X", "text": "I shipped a FastAPI service with Docker."})
    p.write_text(json.dumps(props))
    _, s4 = build_index(root, emb, "structured", tmp_path / "idx", "p1")
    assert s4.added == ["proposal:d-new"] and not s4.updated
    (root / "case_studies" / "CS-AI_-001.md").write_text(case_study_md(json.loads(EVIDENCE.read_text())[0], day=20))   # edited
    _, s5 = build_index(root, emb, "structured", tmp_path / "idx", "p1")
    assert s5.updated == ["case:CS-AI_-001"] and not s5.added
    (root / "case_studies" / "CS-AI_-002.md").unlink()                          # deleted (and evidence entry too)
    ev = [e for e in json.loads((root / "portfolio_evidence.json").read_text()) if e["evidence_id"] != "CS-AI_-002"]
    (root / "portfolio_evidence.json").write_text(json.dumps(ev))
    idx6, s6 = build_index(root, emb, "structured", tmp_path / "idx", "p1")
    assert s6.removed == ["case:CS-AI_-002"] and not any(c.evidence_id == "CS-AI_-002" for c in idx6.chunks)


def test_embedder_change_rebuilds(tmp_path):
    root = make_persona(tmp_path, "p1", n_case=3)
    build_index(root, HashingEmbedder(256), "structured", tmp_path / "idx", "p1")
    idx, st = build_index(root, HashingEmbedder(512), "structured", tmp_path / "idx", "p1")
    assert st.chunks_embedded == len(idx.chunks) and idx.emb.shape[1] == 512


def test_get_embedder_specs():
    assert get_embedder("hashing").dim == 1024 and get_embedder("hashing-64").dim == 64
    with pytest.raises(ValueError):
        get_embedder("nope")


# ------------------------------------------------------------------ variants
def make_retriever(corpus, variant, tmp_path=None, **kw):
    cfg = RagConfig(variant=variant, data_dir=str(corpus), index_dir=str(tmp_path) if tmp_path else None, embedder="hashing-512",
                    max_chunks_per_evidence=1, **kw)
    return Retriever(cfg)


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_every_variant_finds_the_obvious_evidence(corpus, variant):
    res = make_retriever(corpus, variant).retrieve(JOB, k=5)
    assert len(res) == 5 and [c.rank for c in res] == [1, 2, 3, 4, 5]
    assert [c.score for c in res] == sorted((c.score for c in res), reverse=True)
    assert "CS-AI_-001" in [c.evidence_id for c in res]            # the only case study with all 3 required skills
    assert all(c.meta["variant"] == variant for c in res)


def test_v0_uses_fixed_chunks_others_structured(corpus):
    assert all(c.section == "fixed" for c in make_retriever(corpus, "v0_naive").retrieve(JOB, 5))
    assert all(c.section != "fixed" for c in make_retriever(corpus, "v2_hybrid").retrieve(JOB, 5))


def test_v0_functional_entrypoint(corpus):
    from rag.ingest import build_index as bi
    from rag.variants.v0_naive import retrieve
    idx, _ = bi(corpus / "ai_ml_engineer", get_embedder("hashing"), "fixed")
    assert retrieve("RAG LangGraph Qdrant", idx, 3)


def test_v1_decomposition_extracts_and_tags_requirements(corpus):
    reqs = extract_requirements({"title": "T", "skills": ["Docker", "Qdrant"],
                                 "description": "We need a RAG chatbot. Must have experience with LangGraph.\n- Deploy with FastAPI\nGreat team."})
    assert any("LangGraph" in r for r in reqs) and any("FastAPI" in r for r in reqs) and any("Docker" in r for r in reqs) and 2 <= len(reqs) <= 8
    assert extract_requirements("short text") == ["short text"]
    res = make_retriever(corpus, "v1_decomp").retrieve(JOB, 3)
    assert all(c.meta["matched_requirements"] for c in res)
    custom = make_retriever(corpus, "v1_decomp").variant("ai_ml_engineer")
    custom.decomposer = lambda job: ["Qdrant vector store", "LangGraph agents"]           # LLM decomposer hook
    assert custom.retrieve(JOB, 3)[0].meta["matched_requirements"]


def test_rrf_rewards_agreement():
    s = rrf([[1, 2, 3], [3, 1, 4]], k=60)
    assert s[1] > s[2] and s[3] > s[2] and set(s) == {1, 2, 3, 4}


def test_v2_reports_both_rank_sources(corpus):
    top = make_retriever(corpus, "v2_hybrid").retrieve(JOB, 3)[0]
    assert top.meta["dense_rank"] is not None or top.meta["bm25_rank"] is not None


def test_v3_cross_scorer(corpus):
    sc = LexicalCrossScorer()
    good, bad = sc.score("RAG chatbot with Qdrant", ["Built a RAG chatbot using Qdrant", "Shopify theme colours"])
    assert good > bad
    res = make_retriever(corpus, "v3_rerank").retrieve(JOB, 3)
    assert res[0].meta["cross_scorer"] == "lexical"
    with pytest.raises(ValueError):
        get_cross_scorer("bogus")
    with pytest.raises(RuntimeError):
        get_cross_scorer("st:definitely/not-a-real-model-xyz")   # must fail loudly, not silently fall back


def test_v4_boosts_winning_proposals(corpus):
    job = {"title": "RAG assistant", "description": "Build a RAG assistant with LangGraph and Qdrant for a support team."}
    ranked = lambda v: [c.evidence_id for c in make_retriever(corpus, v).retrieve(job, 10)]
    r4 = ranked("v4_outcome")
    assert r4.index("proposal:b-won") < r4.index("proposal:a-ignored")      # identical text, only the outcome differs
    won = next(c for c in make_retriever(corpus, "v4_outcome").retrieve(job, 10) if c.evidence_id == "proposal:b-won")
    ign = next(c for c in make_retriever(corpus, "v4_outcome").retrieve(job, 10) if c.evidence_id == "proposal:a-ignored")
    assert won.meta["outcome_boost"] == pytest.approx(0.35) and ign.meta["outcome_boost"] == 0.0
    assert abs(won.meta["relevance"] - ign.meta["relevance"]) < 0.05      # same relevance: the boost decides


def test_max_chunks_per_evidence_cap(corpus):
    cfg = RagConfig(variant="v2_hybrid", data_dir=str(corpus), index_dir=None, embedder="hashing-512", max_chunks_per_evidence=2)
    res = Retriever(cfg).retrieve(JOB, 10)
    from collections import Counter
    assert max(Counter(c.evidence_id for c in res).values()) <= 2


# ------------------------------------------------------------------ factory
def test_factory_reads_variant_from_config_and_routes_persona(tmp_path):
    base = tmp_path / "personas"; make_persona(base, "alice", 6); make_persona(base, "bob", 6)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"rag": {"variant": "v3_rerank", "data_dir": str(base), "index_dir": None,
                                           "default_persona": "alice", "embedder": "hashing-256"}}))
    r = load_retriever(cfg)
    a = r.retrieve(JOB, 3)
    assert a[0].meta["variant"] == "v3_rerank" and {c.persona for c in a} == {"alice"}
    assert {c.persona for c in r.retrieve({**JOB, "persona_id": "bob"}, 3)} == {"bob"}
    assert load_retriever(cfg, variant="v0_naive").retrieve(JOB, 2)[0].meta["variant"] == "v0_naive"
    with pytest.raises(ValueError):
        load_retriever(cfg, variant="v9_magic")


def test_shipped_config_has_valid_rag_section():
    cfg = RagConfig.from_yaml(Path(__file__).resolve().parents[2] / "agent/config.yaml")
    assert cfg.variant in VARIANTS


def test_reindex_picks_up_new_case_study(tmp_path):
    base = tmp_path / "personas"; root = make_persona(base, "alice", 4)
    r = Retriever(RagConfig(variant="v2_hybrid", data_dir=str(base), index_dir=str(tmp_path / "ix"), default_persona="alice", embedder="hashing-256"))
    assert r.retrieve(JOB, 5)
    new = {**json.loads(EVIDENCE.read_text())[0], "evidence_id": "CS-NEW-1", "title": "Brand new Qdrant LangGraph build"}
    ev = json.loads((root / "portfolio_evidence.json").read_text()) + [new]
    (root / "portfolio_evidence.json").write_text(json.dumps(ev))
    (root / "case_studies" / "CS-NEW-1.md").write_text(case_study_md(new))
    stats = r.reindex("alice")
    assert stats["alice/structured"].added == ["case:CS-NEW-1"]
    assert "CS-NEW-1" in [c.evidence_id for c in r.retrieve(JOB, 10)]


# ------------------------------------------------------------------ verification
EVID = [{"evidence_id": "CS-1", "project": "Full Stack Developer", "skills": ["RAG Systems", "LangGraph", "Qdrant"],
         "outcome": "45% performance improvement, 5-star client review", "section": "architecture & implementation",
         "text": "Architected a modular solution leveraging RAG Systems and LangGraph with comprehensive unit testing and CI verification."},
        {"evidence_id": "CS-1", "project": "x", "skills": [], "section": "client requirement", "text": "Client wants Kubernetes and AWS experts."}]


def labels(draft):
    return {c["sentence"]: c["label"] for c in verify_claims(draft, EVID)}


def test_labels_supported_partly_unsupported():
    got = labels("I built a RAG system with LangGraph and Qdrant that improved performance by 45%. "
                 "I improved performance by 45% and reduced costs by 30%. I led a team of 12 engineers.")
    assert list(got.values()) == [SUPPORTED, PARTLY, UNSUPPORTED]


def test_non_claims_are_ignored():
    out = verify_claims("Hi there! Happy to chat? Your project sounds exciting.", EVID)
    assert all(not c["is_claim"] and c["label"] is None for c in out)


def test_client_requirement_text_is_not_evidence():
    assert labels("I have Kubernetes and AWS experience.")["I have Kubernetes and AWS experience."] == UNSUPPORTED


def test_reasons_are_reported():
    c = verify_claims("I led a team of 12 engineers.", EVID)[0]
    assert c["label"] == UNSUPPORTED and any("12" in r for r in c["reasons"])


def test_strip_and_rewrite_with_rates():
    draft = ("Hi! I built a RAG system with LangGraph and Qdrant that improved performance by 45%. "
             "I improved performance by 45% and reduced costs by 30%. I led a team of 12 engineers. Happy to chat.")
    rep = verify_proposal(draft, EVID)
    assert rep.n_claims_before == 3 and rep.unsupported_before == 1 and rep.partly_before == 1
    assert rep.unsupported_rate_before == pytest.approx(1 / 3) and rep.unsupported_rate_after == 0.0
    assert "12 engineers" not in rep.verified_draft and "30%" not in rep.verified_draft
    assert "I improved performance by 45%." in rep.verified_draft and rep.verified_draft.startswith("Hi!")
    actions = {c.sentence[:12]: c.action for c in rep.claims}
    assert "removed" in actions.values() and "rewritten" in actions.values()


def test_clean_draft_is_untouched_and_judge_hook():
    d = "I built a RAG system with LangGraph and Qdrant."
    rep = verify_proposal(d, EVID)
    assert rep.verified_draft == d and rep.unsupported_rate_before == 0.0
    rep2 = verify_proposal("I have extensive experience in testing.", EVID, judge=lambda c, ev: {"label": UNSUPPORTED, "reason": "llm says no"})
    assert rep2.unsupported_before == 1 and rep2.verified_draft == ""


def test_verify_accepts_evidence_chunks(corpus):
    chunks = make_retriever(corpus, "v2_hybrid").retrieve(JOB, 5)
    rep = verify_proposal("I delivered a RAG system with LangGraph and Qdrant. I led a team of 40 engineers.", chunks)
    assert rep.unsupported_before == 1 and "40 engineers" not in rep.verified_draft


# ------------------------------------------------------------------ evaluation
def test_metric_functions():
    gold = {"a": 1, "b": 1}
    assert recall_at_k(["x", "a", "y", "b"], gold, 5) == 1.0 and recall_at_k(["x", "a"], gold, 5) == 0.5
    assert mrr(["x", "y", "b"], gold) == pytest.approx(1 / 3) and mrr(["x"], gold) == 0.0
    assert ndcg_at_k(["a", "b"], gold, 10) == pytest.approx(1.0)
    assert ndcg_at_k(["x", "a"], gold, 10) == pytest.approx((1 / math.log2(3)) / (1 + 1 / math.log2(3)))
    assert ndcg_at_k(["b", "a"], {"a": 3, "b": 1}, 10) < 1.0


def test_normalize_item_variants():
    it = normalize_item({"id": "z", "persona": "p", "title": "T", "description": "D", "skills": ["x"], "gold": ["e1", "e2"]})
    assert it["job"]["title"] == "T" and it["gold"] == {"e1": 1.0, "e2": 1.0} and it["persona_id"] == "p"
    assert normalize_item({"job": "plain text", "relevant_evidence_ids": {"e": 3}})["gold"] == {"e": 3.0}


def test_benchmark_end_to_end_writes_json_and_markdown(tmp_path, corpus):
    ev = json.loads(EVIDENCE.read_text())
    dev = make_dev_set(ev, "ai_ml_engineer")
    dev[0]["draft"] = "I built a RAG system with LangGraph and Qdrant. I led a team of 12 engineers."
    path = tmp_path / "dev.json"; path.write_text(json.dumps({"items": dev}))
    res = run_benchmark(load_dataset(path), str(corpus), index_dir=str(tmp_path / "ix"), embedder="hashing-512")
    assert list(res["variants"]) == list(VARIANTS) and res["n_queries"] == len(dev)
    for v, d in res["variants"].items():
        o = d["overall"]
        assert 0 <= o["recall@5"] <= 1 and 0 <= o["mrr"] <= 1 and 0 <= o["ndcg@10"] <= 1 and o["latency_p95_ms"] >= o["latency_p50_ms"] >= 0
        assert o["unsupported_per_proposal_after"] <= o["unsupported_per_proposal_before"]
        assert o["recall@5"] > 0.3, v                      # sanity: every variant retrieves something relevant on this corpus
    assert res["variants"]["v2_hybrid"]["per_query"][0]["unsupported_before"] >= 1   # the injected hallucination is caught
    md, js = write_outputs(res, tmp_path / "out" / "rag_benchmark.md")
    text = md.read_text()
    assert "| Variant | Recall@5 | MRR | nDCG@10 |" in text and all(f"| {v} |" in text for v in VARIANTS)
    assert json.loads(js.read_text())["n_queries"] == len(dev) and to_markdown(res) == text


def test_cli_runs(tmp_path, corpus):
    from evals.runners.rag_eval import main
    ds = tmp_path / "d.json"; ds.write_text(json.dumps(make_dev_set(json.loads(EVIDENCE.read_text()), "ai_ml_engineer")[:2]))
    out = tmp_path / "r" / "rag_benchmark.md"
    main(["--dataset", str(ds), "--data-dir", str(corpus), "--variants", "v0_naive", "v2_hybrid", "--index-dir", str(tmp_path / "i"),
          "--embedder", "hashing-512", "--out", str(out)])
    assert out.exists() and out.with_suffix(".json").exists()

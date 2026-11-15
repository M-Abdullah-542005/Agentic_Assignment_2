"""Job Analyst: free-text job post -> JobSpec."""
import re

from agent.deps import Deps
from agent.handoffs import JobSpec
from agent.llm import ask_json

VOCAB = ["python", "pytorch", "tensorflow", "langchain", "langgraph", "rag", "sql", "postgres", "docker", "aws",
         "fastapi", "django", "react", "celery", "dbt", "pinecone", "airflow", "spark", "kubernetes", "llm", "nlp",
         "opencv", "computer vision", "machine learning"]
DELIVERABLE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*(.+)$")


def heuristic_spec(job: dict) -> JobSpec:
    text = f"{job.get('title','')}\n{job.get('description','')}"
    low = text.lower()
    deliverables = [m.group(1).strip() for line in job.get("description", "").splitlines()
                    if (m := DELIVERABLE.match(line))]
    # Support both "skills" and "required_skills" from Kaggle / stream
    raw_skills = job.get("required_skills") or job.get("skills") or []
    skills = list(dict.fromkeys([s.lower() for s in raw_skills] + [v for v in VOCAB if re.search(rf"\b{re.escape(v)}\b", low)]))
    m = re.search(r"(\d+)\s*(day|week|month)s?", low)
    questions = [q.get("text") or q.get("question") or "" if isinstance(q, dict) else str(q)
                 for q in (job.get("screening_questions") or [])] or \
        [l.strip() for l in job.get("description", "").splitlines() if l.strip().endswith("?")]
    
    # Support nested budget object or flat budget_min/max
    b_min = job.get("budget_min")
    b_max = job.get("budget_max")
    if b_min is None and isinstance(job.get("budget"), dict):
        b_min = job["budget"].get("min")
        b_max = job["budget"].get("max")
    elif b_min is None and isinstance(job.get("budget"), (int, float)):
        b_max = float(job["budget"])
        b_min = round(b_max * 0.7, 2)
        
    return JobSpec(job_id=str(job.get("job_id", "")), title=job.get("title", ""), deliverables=deliverables,
                   required_skills=skills, budget_min=b_min, budget_max=b_max,
                   timeline=f"{m.group(1)} {m.group(2)}s" if m else None, screening_questions=questions)


INSTRUCTIONS = (
    "Read the job post in <untrusted> tags and extract what the client needs. Reply with JSON only: "
    '{"deliverables": [short strings], "required_skills": [lowercase skill names, include skills that are '
    'implied but not named], "timeline": "short string or empty"}.'
)


def analyst_node(state, deps: Deps):
    job = state["job"]
    spec = heuristic_spec(job)                                   # always works, also the fallback
    data = ask_json(deps, "analyst", {"instructions": INSTRUCTIONS, "job": job})
    if isinstance(data, dict):
        update = {}
        if isinstance(data.get("deliverables"), list) and data["deliverables"]:
            update["deliverables"] = [str(d) for d in data["deliverables"]][:10]
        if isinstance(data.get("required_skills"), list):
            llm_skills = [str(s).lower() for s in data["required_skills"] if str(s).strip()]
            update["required_skills"] = list(dict.fromkeys(spec.required_skills + llm_skills))
        if isinstance(data.get("timeline"), str) and data["timeline"].strip():
            update["timeline"] = data["timeline"].strip()
        spec = spec.model_copy(update=update)
    return {"job_spec": spec.model_dump()}

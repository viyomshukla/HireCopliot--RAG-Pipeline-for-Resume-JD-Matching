"""
Controlled skill vocabulary: many surface forms -> one canonical name.

WHY THIS EXISTS
---------------
The resume says "Torch". The job description says "PyTorch". A filter for
"PyTorch" has to match that candidate, and string equality will not do it.

This is the `canonical` field that schemas.py deliberately left empty. It is
filled HERE, by a lookup table, and not by the LLM. Three reasons:

  DETERMINISM  The same input always maps the same way. A language model asked
               to normalise can map "Torch" to PyTorch today and to Torch
               tomorrow, and your database quietly grows two rows for one skill.
  CORRECTABLE  When a mapping is wrong you edit one line here and reload. You
               cannot patch a model's judgement.
  FREE         No tokens, no latency, no rate limit.

WHAT "CANONICAL" ACTUALLY MEANS
-------------------------------
It is a decision, not a fact. Is "PostgreSQL" its own skill or a kind of "SQL"?
Is "FastAPI" separate from "Python"? There is no correct answer -- it depends on
whether a recruiter would filter on them separately. We keep them separate,
because a JD asking for PostgreSQL specifically should not match someone who
only wrote "SQL". Broader relationships (PostgreSQL implies SQL) belong in the
semantic half of the system, not here.

THE UNKNOWN-SKILL DECISION
--------------------------
A skill not in this table is kept, not dropped, using its cleaned surface form
as its own canonical name. Dropping unknown skills would silently discard every
technology invented after this file was written. Instead `unknown_skills()`
reports them so the vocabulary can grow from real data -- the same feedback loop
as the chunker's unknown-headings report.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Real systems use an ontology like ESCO or O*NET, or a learned embedding-based
matcher, with thousands of entries and hierarchy. This is a hand-written flat
map covering the skills our corpus actually contains. It is enough to learn what
normalisation does and where it breaks.
"""

from __future__ import annotations

import re
from collections import Counter

# canonical name -> every surface form that should map to it.
# The canonical name itself does not need repeating in its own alias list.
SKILL_VOCABULARY: dict[str, list[str]] = {
    # languages
    "Python": ["python3", "python 3", "py", "python (pandas, asyncio)"],
    "Java": ["java 17", "core java", "java8", "java 8"],
    "JavaScript": ["js", "ecmascript", "vanilla js"],
    "TypeScript": ["ts"],
    "Go": ["golang"],
    "C++": ["cpp", "c plus plus"],
    "SQL": ["advanced sql", "sql queries", "structured query language"],

    # data stores
    "PostgreSQL": ["postgres", "psql", "postgresql (partitioning, indexing)"],
    "MySQL": [],
    "MongoDB": ["mongo", "document databases", "document databases (mongodb)"],
    "Redis": ["redis caching", "in-memory caching", "in-memory caching (redis)"],
    "Snowflake": [],
    "BigQuery": ["google bigquery"],

    # ml / ds
    "Machine Learning": ["ml", "applied ml", "ml modelling", "ml modeling"],
    "Deep Learning": ["dl", "neural networks", "neural nets"],
    "NLP": ["natural language processing", "text mining", "text analytics"],
    "PyTorch": ["torch", "pytorch (training & inference)", "pytorch lightning"],
    "TensorFlow": ["tf", "tf/keras", "tensorflow 2", "keras"],
    "Scikit-learn": ["sklearn", "scikit learn", "scikit-learn pipelines"],
    "Pandas": ["pandas / numpy", "pandas dataframes"],
    "NumPy": ["numpy vectorisation", "numpy vectorization"],
    "Statistics": ["statistical inference", "applied statistics", "stats"],
    "MLOps": ["ml platform engineering", "model deployment & monitoring",
              "model deployment and monitoring"],
    "Hugging Face": ["huggingface", "hugging face transformers", "transformers"],

    # analytics
    "Power BI": ["microsoft power bi", "powerbi", "power bi (dax)", "power bi / dax"],
    "Tableau": ["tableau desktop", "tableau dashboards"],
    "Looker": [],
    "Excel": ["advanced excel", "ms excel", "excel (pivot tables, power query)"],
    "Data Visualization": ["data viz", "dashboarding", "data visualisation"],
    "A/B Testing": ["ab testing", "split testing", "online experimentation"],
    "Experimentation": ["experiment design", "causal inference"],
    "ETL": ["data pipelines", "elt", "elt pipelines", "etl pipelines"],

    # backend / infra
    "REST APIs": ["rest", "restful services", "rest/http api design",
                  "rest api", "restful apis"],
    "FastAPI": ["fastapi + pydantic", "async python apis", "async python apis (fastapi)"],
    "Node.js": ["nodejs", "node", "node (express)", "express"],
    "Docker": ["containerisation", "containerization", "containerisation (docker)",
               "docker & docker-compose", "docker compose"],
    "Kubernetes": ["k8s", "container orchestration", "container orchestration (kubernetes)"],
    "AWS": ["amazon web services", "ecs", "eks", "s3", "lambda"],
    "Azure": ["microsoft azure"],
    "GCP": ["google cloud", "google cloud platform"],
    "Microservices": ["microservice design", "service-oriented architecture", "soa"],
    "Message Queues": ["kafka", "rabbitmq", "async messaging", "kafka / rabbitmq"],
    "Git": ["git / github", "github", "version control", "version control (git)"],
    "CI/CD": ["ci", "cd", "jenkins", "github actions", "continuous integration"],
    "Unit Testing": ["pytest", "automated testing", "testing", "junit"],
}


def _clean(text: str) -> str:
    """Lowercase and strip punctuation, keeping + and # so C++ and C# survive.

    Without that exception both collapse to "c" and match each other, which is
    the kind of bug that only shows up when a C++ role is posted.
    """
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9+#./ -]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .-")


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in SKILL_VOCABULARY.items():
        lookup[_clean(canonical)] = canonical
        for alias in aliases:
            lookup[_clean(alias)] = canonical
    return lookup


LOOKUP = _build_lookup()

# Skills seen that the vocabulary does not know. Reported after a load so the
# table can grow from real data rather than guesswork.
_unknown: Counter = Counter()


def canonicalise(surface: str) -> tuple[str, bool]:
    """surface form -> (canonical name, was_known).

    Matching runs in three passes, most confident first:
      1. exact match on the cleaned string
      2. the surface form with a parenthetical stripped -- "Power BI (DAX)"
         is Power BI, and resumes love parentheses
      3. containment, longest match wins, but ONLY for aliases of 4+ characters

    That length guard matters. Without it "ML" matches inside "HTML" and every
    frontend developer acquires a machine-learning skill. Short aliases are
    exact-match only.
    """
    cleaned = _clean(surface)
    if not cleaned:
        return surface.strip(), False

    if cleaned in LOOKUP:
        return LOOKUP[cleaned], True

    stripped = _clean(re.sub(r"\(.*?\)", " ", surface))
    if stripped in LOOKUP:
        return LOOKUP[stripped], True

    best, best_len = None, 0
    for alias, canonical in LOOKUP.items():
        if len(alias) < 4:
            continue
        if alias in cleaned and len(alias) > best_len:
            best, best_len = canonical, len(alias)
    if best:
        return best, True

    # Unknown: keep it, titled for display, and record it for review.
    _unknown[cleaned] += 1
    return surface.strip(), False


def unknown_skills(limit: int = 20) -> list[tuple[str, int]]:
    """The skills the vocabulary could not place, most common first.

    This is the feedback loop: run a load, read this list, add the ones that
    matter to SKILL_VOCABULARY, reload. Same pattern as the chunker's
    unknown-headings report.
    """
    return _unknown.most_common(limit)


def reset_unknown() -> None:
    _unknown.clear()
"""Conservative matching rules for class deadlines and reviewed comparisons."""

import re

from app.generation.safety import folded

_DEADLINE_QUESTION = re.compile(
    r"(?:cho em hoi )?bai tap(?: (?:cua )?lop minh)? "
    r"(?:phai |can )?nop (?:truoc |cham nhat )?(?:khi nao|luc nao|may gio|ngay nao|gio nao)"
)
_DEADLINE = re.compile(
    r"\bbai tap(?: sau buoi hoc)? (?:can |phai )?nop\s+(?:truoc|cham nhat(?: la)?|vao)\s+"
    r"((?:[01]\d|2[0-3]):[0-5]\d)\s+(chu nhat|thu (?:hai|ba|tu|nam|sau|bay))\b"
)
_FINE_TUNING = r"fine[ -]?tuning"
_COMPARISON_QUESTION = re.compile(
    rf"(?:rag khac {_FINE_TUNING}(?: o diem nao| nhu the nao)?|"
    rf"rag va {_FINE_TUNING} khac nhau (?:o diem nao|nhu the nao)|"
    rf"so sanh rag (?:va|voi|vs\.?|versus) {_FINE_TUNING}|"
    rf"rag (?:vs\.?|versus) {_FINE_TUNING})"
)
_RAG_MECHANISM = re.compile(
    r"\brag (?:la phuong phap |hoat dong bang cach )?truy xuat (?:tai lieu|hoc lieu|thong tin)\b"
)
_TUNING_MECHANISM = re.compile(
    rf"\b{_FINE_TUNING} (?:tiep tuc |la qua trinh )?huan luyen\b[^.!?;]{{0,80}}"
    r"\b(?:dieu chinh|cap nhat|thay doi) trong so\b"
)
_CAUSAL_OR_COMPARATIVE = re.compile(
    rf"\b(?:so sanh|khac|versus|vs|compare|{_FINE_TUNING}|tai sao|vi sao|why)\b"
)


def asks_submission_deadline(question: str) -> bool:
    return bool(_DEADLINE_QUESTION.fullmatch(folded(question).strip(" ?!.")))


def asks_rag_comparison(question: str) -> bool:
    return bool(_COMPARISON_QUESTION.fullmatch(folded(question).strip(" ?!.")))


def requires_comparative_or_causal_evidence(question: str) -> bool:
    return bool(_CAUSAL_OR_COMPARATIVE.search(folded(question)))


def submission_deadlines(paragraph: str) -> set[tuple[str, str]]:
    return set(_DEADLINE.findall(folded(paragraph)))


def explains_rag_comparison(paragraph: str) -> bool:
    # FAQ question lines may name both concepts without explaining either of them.
    body = "\n".join(line for line in paragraph.splitlines() if not line.startswith("Q:"))
    text = folded(body)
    return bool(_RAG_MECHANISM.search(text) and _TUNING_MECHANISM.search(text))

import json
import re
from typing import Any

from groq import Groq

AUDIT_MODEL = "llama-3.1-8b-instant"
_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq()
    return _client

AUDITOR_SYSTEM = """You are an independent code-answer auditor reviewing a response from an AI investigator that analyzed a GitHub repository.

You receive:
1. The user's question
2. The investigator's answer
3. Citation verification results — actual file content pulled from the repo at each cited location
4. Prior claims log — facts established in earlier turns of this investigation

YOUR ROLE: Determine whether this answer is trustworthy, not as the author but as a reviewer.

WHAT TO ASSESS:

CITATIONS: For each citation, you have the actual file content at that location. Does the content support what was claimed? A citation that exists but doesn't back the claim is a problem. A non-existent file is a hallucination.

OVERCLAIMING: Confident assertions made without any citation. "The system uses bcrypt" with no `*.py:N-M` backing it is an overclaim.

GAPS: Did the answer fail to address something the question explicitly asked? Note omissions that matter.

CONTRADICTIONS: Does anything here conflict with prior established claims? If Turn 1 established "auth uses JWT" and this answer says "auth uses session cookies," flag it.

REASONING: Sound logic? Or jumps not supported by evidence?

TRUST CALIBRATION — be calibrated, not harsh:
- HIGH: Core claims are verified by citations, reasoning is sound, question is fully addressed
- MEDIUM: Minor gaps or one-two uncited assertions, but the main answer is reliable
- LOW: Key claims unverified, significant gaps, or reasoning has notable holes
- UNRELIABLE: Hallucinated citations, contradicts verified prior claims, or fundamentally misleads

Be specific. "OVERCLAIM: says auth uses bcrypt at line 120 but nothing at that location mentions bcrypt" is useful. "The answer looks mostly fine" is not.

Return ONLY valid JSON — no markdown fences, no preamble:
{
  "trust_level": "HIGH|MEDIUM|LOW|UNRELIABLE",
  "citation_issues": [
    {"citation": "path:lines", "issue": "what is wrong"}
  ],
  "reasoning_issues": [
    {
      "type": "OVERCLAIM|GAP|CONTRADICTION|HALLUCINATION|REASONING_GAP",
      "description": "Specific, concrete description",
      "severity": "HIGH|MEDIUM|LOW"
    }
  ],
  "contradictions_with_prior": [
    {"prior_claim": "...", "current_claim": "...", "turn": 0}
  ],
  "new_claims": [
    {"claim": "One concrete factual assertion", "citation": "path:lines or null", "confidence": "high|medium|low"}
  ],
  "summary": "Two sentences. What is reliable and what is not."
}"""


def extract_citations(text: str) -> list[dict[str, Any]]:
    """Extract `path:N-M` style citations from answer text."""
    pattern = r"`([\w/.-]+\.[\w]+):(\d+)(?:-(\d+))?`"
    seen: set[str] = set()
    citations: list[dict] = []
    for m in re.finditer(pattern, text):
        path = m.group(1)
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else min(start + 15, start + 1)
        key = f"{path}:{start}-{end}"
        if key not in seen:
            seen.add(key)
            citations.append({"path": path, "start_line": start, "end_line": end})
    return citations


def verify_citations(citations: list[dict], tools) -> list[dict[str, Any]]:
    """Programmatically check each citation against the actual repo."""
    results = []
    for c in citations:
        result = tools.read_file(c["path"], c["start_line"], c["end_line"])
        cite_str = f"{c['path']}:{c['start_line']}-{c['end_line']}"
        if "error" in result:
            results.append({"citation": cite_str, "exists": False, "content": None, "issue": result["error"]})
        else:
            results.append({
                "citation": cite_str,
                "exists": True,
                "content": result.get("content", "")[:600],
                "total_lines": result.get("total_lines", 0),
            })
    return results


def _format_verification(verifications: list[dict]) -> str:
    if not verifications:
        return "No citations found in the answer."
    parts = []
    for v in verifications:
        if v["exists"]:
            parts.append(f"CITATION {v['citation']}: EXISTS (file has {v['total_lines']} lines)\nContent at location:\n{v['content']}")
        else:
            parts.append(f"CITATION {v['citation']}: NOT FOUND — {v.get('issue', 'unknown error')}")
    return "\n\n".join(parts)


def _format_prior_claims(claims_log: list[dict]) -> str:
    if not claims_log:
        return "No prior claims."
    lines = []
    for c in claims_log:
        lines.append(f"[Turn {c.get('turn','?')}] {c['claim']} (citation: {c.get('citation') or 'none'}, confidence: {c.get('confidence','?')})")
    return "\n".join(lines)


def run_audit(session, question: str, answer: str) -> dict[str, Any]:
    """Audit an answer. Runs synchronously — call from a thread."""
    citations = extract_citations(answer)
    verifications = verify_citations(citations, session.tools)

    prompt = f"""QUESTION:
{question}

INVESTIGATOR'S ANSWER:
{answer}

CITATION VERIFICATION RESULTS:
{_format_verification(verifications)}

PRIOR CLAIMS LOG:
{_format_prior_claims(session.claims_log)}

Produce your audit as JSON."""

    try:
        response = _get_client().chat.completions.create(
            model=AUDIT_MODEL,
            messages=[
                {"role": "system", "content": AUDITOR_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        audit = json.loads(raw)
        audit["citation_verification"] = verifications
        return audit

    except json.JSONDecodeError:
        return {
            "trust_level": "MEDIUM",
            "citation_issues": [],
            "reasoning_issues": [{"type": "AUDIT_ERROR", "description": "Audit response was not valid JSON", "severity": "LOW"}],
            "contradictions_with_prior": [],
            "new_claims": [],
            "citation_verification": verifications,
            "summary": "Audit parsing failed. Citation verification completed — see citation_verification field.",
        }
    except Exception as e:
        return {
            "trust_level": "UNKNOWN",
            "error": str(e),
            "citation_verification": verifications,
            "summary": f"Audit failed: {e}",
        }

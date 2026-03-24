"""Generic task agent — LLM-driven reasoning layer for browser tasks.

This is the "brain" that sits above site-specific skills. It uses Claude API
for ALL high-level decisions: understanding the task, generating search
strategies, evaluating results, verifying matches, and deciding completion.

Architecture position:
  TaskAgent (this) — generic reasoning, like Claude Code for browser tasks
      ↓
  Site Skills (xhs/browser.py) — site-specific DOM extraction, navigation
      ↓
  Generic Infra (bridge.py, media.py) — WebSocket, CDP, LLM, OCR

The agent does NOT hardcode task-specific logic. It reasons through each
decision via LLM calls, making it adaptable to any task description.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .media import MediaProcessor


@dataclass
class TaskUnderstanding:
    """Structured understanding of what the user wants."""
    goal: str = ""              # What to achieve
    target_type: str = ""       # "video", "image_post", "user_profile", "information"
    search_criteria: dict = field(default_factory=dict)  # Key attributes to match
    search_keywords: list[str] = field(default_factory=list)
    success_criteria: str = ""  # How to know the task is done
    raw_reasoning: str = ""     # Full LLM reasoning


@dataclass
class CandidateEvaluation:
    """LLM evaluation of a search result candidate."""
    index: int = -1
    title: str = ""
    relevance_score: float = 0.0  # 0-1
    match_reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    recommendation: str = ""  # "open", "skip", "maybe"
    reasoning: str = ""


@dataclass
class NoteVerification:
    """LLM verification after opening a note."""
    matches_task: bool = False
    confidence: float = 0.0  # 0-1
    match_details: str = ""
    missing_criteria: list[str] = field(default_factory=list)
    should_download: bool = False
    should_try_alternatives: bool = False
    reasoning: str = ""


class TaskAgent:
    """Generic LLM-driven agent for browser tasks.

    Uses Claude API for reasoning at every decision point.
    Site-specific actions are delegated to the browser skill.
    """

    def __init__(self, media: MediaProcessor):
        self.media = media
        self._reasoning_log: list[dict] = []
        self._t0 = time.time()

    def _log_reasoning(self, phase: str, prompt_summary: str, response_summary: str) -> None:
        """Record a reasoning step for auditability."""
        self._reasoning_log.append({
            "timestamp": round(time.time() - self._t0, 1),
            "phase": phase,
            "prompt_summary": prompt_summary[:300],
            "response_summary": response_summary[:500],
        })

    @property
    def reasoning_log(self) -> list[dict]:
        return self._reasoning_log

    # ── Task Understanding ─────────────────────────────────────

    def understand_task(self, task: str) -> TaskUnderstanding:
        """Use LLM to deeply understand what the user wants.

        Returns structured understanding including search strategy.
        """
        prompt = f"""You are a browser automation agent. A user has given you this task:

"{task}"

Today's date: {time.strftime('%Y-%m-%d')}

Analyze the task and return a JSON object with:
{{
  "goal": "one sentence describing what to achieve",
  "target_type": "video" | "image_post" | "user_profile" | "information",
  "search_criteria": {{
    // key attributes the target must match, e.g.:
    // "source": "bloc1攀岩馆",
    // "content": "v2线路合集",
    // "time_range": "2026年3月 (this month)",
    // "media_type": "video"
  }},
  "search_keywords": ["keyword1", "keyword2", "keyword3"],
  "success_criteria": "how to confirm the task is complete"
}}

Think step by step about:
1. What exactly is the user looking for?
2. What are the MUST-HAVE criteria vs nice-to-have?
3. What search keywords would work best on Xiaohongshu?
4. How will you know you found the right thing?

Return ONLY the JSON object."""

        raw = self.media.call_text(prompt, max_tokens=1024)
        self._log_reasoning("understand_task", f"Task: {task}", raw[:400])

        # Parse JSON from response
        try:
            # Handle markdown code blocks
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {}

        return TaskUnderstanding(
            goal=data.get("goal", task),
            target_type=data.get("target_type", ""),
            search_criteria=data.get("search_criteria", {}),
            search_keywords=data.get("search_keywords", [task]),
            success_criteria=data.get("success_criteria", ""),
            raw_reasoning=raw,
        )

    # ── Search Result Evaluation ───────────────────────────────

    def evaluate_candidates(
        self,
        cards: list[dict],
        task_understanding: TaskUnderstanding,
        search_keyword: str,
    ) -> list[CandidateEvaluation]:
        """Use LLM to evaluate which search results best match the task.

        Returns evaluations sorted by relevance (best first).
        """
        # Format cards for LLM
        cards_text = "\n".join(
            f"  [{i}] [{c.get('type','?')}] {c.get('title','')} | "
            f"likes={c.get('likes','')} author={c.get('author_name','')}"
            for i, c in enumerate(cards[:15])  # Limit to first 15
        )

        criteria_text = json.dumps(task_understanding.search_criteria, ensure_ascii=False, indent=2)

        prompt = f"""You are evaluating Xiaohongshu search results for a task.

**Task goal:** {task_understanding.goal}
**Search criteria:**
{criteria_text}
**Today's date:** {time.strftime('%Y-%m-%d')}
**Search keyword used:** {search_keyword}

**Search results:**
{cards_text}

For EACH potentially relevant result, evaluate:
1. Does the title suggest it matches the search criteria?
2. Is it the right content type (video/image)?
3. Any concerns (wrong month, wrong source, misleading title)?

Return a JSON array of evaluations for the TOP candidates (up to 5):
[
  {{
    "index": 0,
    "title": "...",
    "relevance_score": 0.95,
    "match_reasons": ["reason1", "reason2"],
    "concerns": ["concern1"],
    "recommendation": "open"
  }},
  ...
]

Only include candidates worth considering (score > 0.3).
"recommendation" must be one of: "open" (strong match), "maybe" (check if better options exist), "skip".
Return ONLY the JSON array."""

        raw = self.media.call_text(prompt, max_tokens=1024)
        self._log_reasoning(
            "evaluate_candidates",
            f"Evaluating {len(cards)} cards for: {task_understanding.goal}",
            raw[:400],
        )

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            evaluations_data = json.loads(cleaned)
        except json.JSONDecodeError:
            evaluations_data = []

        results = []
        for e in evaluations_data:
            results.append(CandidateEvaluation(
                index=e.get("index", -1),
                title=e.get("title", ""),
                relevance_score=e.get("relevance_score", 0),
                match_reasons=e.get("match_reasons", []),
                concerns=e.get("concerns", []),
                recommendation=e.get("recommendation", "skip"),
                reasoning=json.dumps(e, ensure_ascii=False),
            ))
        results.sort(key=lambda x: -x.relevance_score)
        return results

    # ── Note Verification ──────────────────────────────────────

    def verify_note(
        self,
        note_content: dict,
        task_understanding: TaskUnderstanding,
        screenshot_b64: str | None = None,
    ) -> NoteVerification:
        """Use LLM to verify an opened note matches the task.

        Examines note content, metadata, and optionally screenshot to
        determine if this is truly what the user asked for.
        """
        criteria_text = json.dumps(task_understanding.search_criteria, ensure_ascii=False, indent=2)
        note_summary = json.dumps({
            "title": note_content.get("title", ""),
            "author": note_content.get("author", ""),
            "type": note_content.get("type", ""),
            "content": (note_content.get("content", "") or "")[:500],
            "date": note_content.get("date", ""),
            "likes": note_content.get("likes", ""),
            "hashtags": note_content.get("hashtags", []),
            "image_count": note_content.get("image_count", 0),
            "has_video": note_content.get("type") == "video",
        }, ensure_ascii=False, indent=2)

        prompt = f"""You have opened a Xiaohongshu note. Verify if it matches the task.

**Task goal:** {task_understanding.goal}
**Required criteria:**
{criteria_text}
**Success criteria:** {task_understanding.success_criteria}
**Today's date:** {time.strftime('%Y-%m-%d')}

**Note content:**
{note_summary}

Evaluate carefully:
1. Does this note match ALL the required criteria?
2. Is it from the correct time period?
3. Is the content what the user actually wants?
4. Are there any red flags (wrong source, old content, misleading title)?

Return a JSON object:
{{
  "matches_task": true/false,
  "confidence": 0.0-1.0,
  "match_details": "explain what matches and what doesn't",
  "missing_criteria": ["list of criteria not met"],
  "should_download": true/false,
  "should_try_alternatives": true/false,
  "reasoning": "detailed reasoning"
}}

Return ONLY the JSON object."""

        # Use vision if screenshot available
        if screenshot_b64:
            raw = self.media.call_vision(
                screenshot_b64, prompt, max_tokens=1024
            )
        else:
            raw = self.media.call_text(prompt, max_tokens=1024)

        self._log_reasoning(
            "verify_note",
            f"Verifying: {note_content.get('title', '')}",
            raw[:400],
        )

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {}

        return NoteVerification(
            matches_task=data.get("matches_task", False),
            confidence=data.get("confidence", 0),
            match_details=data.get("match_details", ""),
            missing_criteria=data.get("missing_criteria", []),
            should_download=data.get("should_download", False),
            should_try_alternatives=data.get("should_try_alternatives", True),
            reasoning=data.get("reasoning", raw[:300]),
        )

    # ── Completion Check ───────────────────────────────────────

    def check_completion(
        self,
        task_understanding: TaskUnderstanding,
        results_so_far: list[dict],
    ) -> dict:
        """Use LLM to decide if the task is complete.

        Returns {"complete": bool, "reasoning": str, "next_action": str}
        """
        results_summary = json.dumps(
            [
                {
                    "title": r.get("title", ""),
                    "downloaded": bool(r.get("video_download_path") or r.get("saved_images")),
                    "verified": r.get("verified", False),
                    "confidence": r.get("confidence", 0),
                }
                for r in results_so_far
            ],
            ensure_ascii=False, indent=2,
        )

        prompt = f"""You are a browser automation agent checking if a task is complete.

**Task goal:** {task_understanding.goal}
**Success criteria:** {task_understanding.success_criteria}

**Results collected so far:**
{results_summary}

Is the task complete? Return JSON:
{{
  "complete": true/false,
  "reasoning": "why or why not",
  "next_action": "done" | "try_more_keywords" | "open_alternative" | "refine_search"
}}

Return ONLY the JSON object."""

        raw = self.media.call_text(prompt, max_tokens=512)
        self._log_reasoning("check_completion", "Checking task completion", raw[:300])

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"complete": False, "reasoning": raw[:200], "next_action": "try_more_keywords"}

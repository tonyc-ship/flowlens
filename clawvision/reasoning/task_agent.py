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

from ..perception.media import MediaProcessor


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


@dataclass
class TaskAssessment:
    """LLM judgment on whether a workflow output satisfied the task."""

    complete: bool = False
    confidence: float = 0.0
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class ExecutionStrategy:
    """Budget-aware workflow strategy chosen from available capabilities."""

    mode: str = "balanced"
    keyword_count: int = 4
    cards_per_keyword: int = 12
    lite_note_count: int = 10
    deep_note_count: int = 4
    timeline_sample_count: int = 12
    deep_sample_count: int = 4
    lite_comment_count: int = 4
    deep_comment_count: int = 12
    report_style: str = "concise_evidence"
    reasoning: str = ""


class TaskAgent:
    """Generic LLM-driven agent for browser tasks.

    Uses Claude API for reasoning at every decision point.
    Site-specific actions are delegated to the browser skill.
    """

    def __init__(self, media: MediaProcessor, site_context: str = ""):
        """
        Args:
            media: LLM / OCR / vision utilities.
            site_context: Platform description injected by the site skill layer,
                e.g. "Xiaohongshu (小红书), a Chinese social media platform with
                notes, videos, and image carousel posts."
                Keeps TaskAgent generic — it never hardcodes site knowledge.
        """
        self.media = media
        self.site_context = site_context
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

    def _parse_json_response(self, raw: str):
        """Best-effort JSON parser for LLM responses with optional wrappers."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return self.media.extract_json(raw)

    @property
    def reasoning_log(self) -> list[dict]:
        return self._reasoning_log

    def plan_execution(
        self,
        task_prompt: str,
        task_kind: str,
        capability_catalog: str,
    ) -> ExecutionStrategy:
        """Choose a breadth-vs-depth strategy from the available capabilities."""
        prompt = f"""You are planning a browser workflow.

**Task kind:** {task_kind}
**Task definition:**
{task_prompt}

**Available capabilities (with cost/latency):**
{capability_catalog}

Choose a budget-aware strategy. Prefer:
1. broad coverage first
2. lightweight reads before deep multimodal work
3. a small number of deep reads only when justified
4. concise, evidence-driven final reporting

Return ONLY a JSON object:
{{
  "mode": "coverage_first" | "balanced" | "deep_focus",
  "keyword_count": 4,
  "cards_per_keyword": 12,
  "lite_note_count": 10,
  "deep_note_count": 4,
  "timeline_sample_count": 12,
  "deep_sample_count": 4,
  "lite_comment_count": 4,
  "deep_comment_count": 12,
  "report_style": "concise_evidence",
  "reasoning": "short explanation"
}}

For topic research, keyword_count/cards_per_keyword/lite_note_count/deep_note_count matter most.
For creator analysis, timeline_sample_count/deep_sample_count matter most.
"""

        raw = self.media.call_text(prompt, max_tokens=1024)
        self._log_reasoning("plan_execution", f"Planning {task_kind}", raw[:400])
        data = self._parse_json_response(raw) or {}

        if task_kind == "topic_research":
            defaults = {
                "keyword_count": 4,
                "cards_per_keyword": 12,
                "lite_note_count": 10,
                "deep_note_count": 4,
                "lite_comment_count": 4,
                "deep_comment_count": 12,
            }
        else:
            defaults = {
                "timeline_sample_count": 12,
                "deep_sample_count": 4,
                "lite_comment_count": 4,
                "deep_comment_count": 12,
            }

        return ExecutionStrategy(
            mode=data.get("mode", "balanced"),
            keyword_count=max(2, int(data.get("keyword_count", defaults.get("keyword_count", 4)))),
            cards_per_keyword=max(6, int(data.get("cards_per_keyword", defaults.get("cards_per_keyword", 12)))),
            lite_note_count=max(4, int(data.get("lite_note_count", defaults.get("lite_note_count", 10)))),
            deep_note_count=max(2, int(data.get("deep_note_count", defaults.get("deep_note_count", 4)))),
            timeline_sample_count=max(6, int(data.get("timeline_sample_count", defaults.get("timeline_sample_count", 12)))),
            deep_sample_count=max(2, int(data.get("deep_sample_count", defaults.get("deep_sample_count", 4)))),
            lite_comment_count=max(2, int(data.get("lite_comment_count", defaults.get("lite_comment_count", 4)))),
            deep_comment_count=max(4, int(data.get("deep_comment_count", defaults.get("deep_comment_count", 12)))),
            report_style=data.get("report_style", "concise_evidence"),
            reasoning=data.get("reasoning", raw[:300]),
        )

    # ── Task Understanding ─────────────────────────────────────

    def understand_task(self, task: str) -> TaskUnderstanding:
        """Use LLM to deeply understand what the user wants.

        Returns structured understanding including search strategy.
        """
        site_line = f"\nPlatform: {self.site_context}\n" if self.site_context else ""
        prompt = f"""You are a browser automation agent. A user has given you this task:

"{task}"

Today's date: {time.strftime('%Y-%m-%d')}{site_line}
Analyze the task and return a JSON object with:
{{
  "goal": "one sentence describing what to achieve",
  "target_type": "video" | "image_post" | "user_profile" | "information",
  "search_criteria": {{
    // key attributes the target must match, e.g.:
    // "topic": "camping gear",
    // "content_type": "video",
    // "time_range": "recent"
  }},
  "search_keywords": ["keyword1", "keyword2", "keyword3"],
  "success_criteria": "how to confirm the task is complete"
}}

Think step by step about:
1. What exactly is the user looking for?
2. What are the MUST-HAVE criteria vs nice-to-have?
3. What search keywords would work best on this platform?
4. How will you know you found the right thing?

Return ONLY the JSON object."""

        raw = self.media.call_text(prompt, max_tokens=1024)
        self._log_reasoning("understand_task", f"Task: {task}", raw[:400])

        # Parse JSON from response
        data = self._parse_json_response(raw) or {}

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

        site_label = f" on {self.site_context}" if self.site_context else ""
        prompt = f"""You are evaluating search results{site_label} for a task.

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

        evaluations_data = self._parse_json_response(raw) or []

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

        site_label = f" on {self.site_context}" if self.site_context else ""
        prompt = f"""You have opened a content page{site_label}. Verify if it matches the task.

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

        data = self._parse_json_response(raw) or {}

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

        return self._parse_json_response(raw) or {
            "complete": False,
            "reasoning": raw[:200],
            "next_action": "try_more_keywords",
        }

    def assess_workflow_result(
        self,
        task_understanding: TaskUnderstanding,
        workflow_name: str,
        result_summary: dict,
    ) -> TaskAssessment:
        """Evaluate whether a workflow output satisfied the original task."""
        summary_json = json.dumps(result_summary, ensure_ascii=False, indent=2)
        prompt = f"""You are reviewing the result of a browser automation workflow.

**Original goal:** {task_understanding.goal}
**Success criteria:** {task_understanding.success_criteria}
**Workflow used:** {workflow_name}

**Workflow output summary:**
{summary_json}

Assess whether the workflow output is good enough to consider the task complete.
Return ONLY a JSON object:
{{
  "complete": true/false,
  "confidence": 0.0-1.0,
  "strengths": ["evidence-backed strengths"],
  "gaps": ["important missing pieces or risks"],
  "next_actions": ["what to improve or run next"],
  "reasoning": "short explanation"
}}"""

        raw = self.media.call_text(prompt, max_tokens=1024)
        self._log_reasoning(
            "assess_workflow_result",
            f"Reviewing {workflow_name}",
            raw[:400],
        )

        data = self._parse_json_response(raw) or {}

        return TaskAssessment(
            complete=data.get("complete", False),
            confidence=data.get("confidence", 0),
            strengths=data.get("strengths", []),
            gaps=data.get("gaps", []),
            next_actions=data.get("next_actions", []),
            reasoning=data.get("reasoning", raw[:300]),
        )

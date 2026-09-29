"""
paper_builder.py
----------------
Takes the raw, validated question-candidate bank (tagged with module,
source PDF, difficulty, type, marks-range) and allocates it into a final
question paper that follows the user's configured module weightage and
difficulty weightage. Also provides single-question regeneration and the
dashboard analytics computation.
"""

import random
import uuid
from collections import defaultdict, Counter

from question_generator import sufficient_for_marks, expand_answer_for_marks, strip_internal_fields

SECTION_GROUPS = [
    ("A", "Very Short Answer / Definitions", {"Definition", "Short Notes"}),
    ("B", "Short Answer / Explanatory", {"Explanation", "Application"}),
    ("C", "Long Answer / Analytical", {"Comparison", "Analytical", "Problem-Based"}),
]
TYPE_TO_SECTION = {t: (label, name) for label, name, types in SECTION_GROUPS for t in types}

DIFFICULTY_ORDER = ["Easy", "Medium", "Hard"]


def section_for_question(question):
    """Place fallback question types according to their final marks."""
    marks = question.get("marks", 0) or 0
    if marks <= 4:
        return "A", "Very Short Answer / Definitions"
    if marks >= 8:
        return "C", "Long Answer / Analytical"
    return TYPE_TO_SECTION.get(question.get("type"), ("C", "Long Answer"))


def _largest_remainder_alloc(total, weights: dict):
    """Given weights (name -> percent, summing ~100), distribute `total`
    integer units proportionally using largest-remainder rounding."""
    names = list(weights.keys())
    if not names or total <= 0:
        return {n: 0 for n in names}
    raw = {n: total * weights[n] / 100.0 for n in names}
    floors = {n: int(raw[n]) for n in names}
    remainder = total - sum(floors.values())
    order = sorted(names, key=lambda n: raw[n] - floors[n], reverse=True)
    for i in range(remainder):
        floors[order[i % len(order)]] += 1
    return floors


def _weighted_round_robin_alloc(total, weights, carry):
    """Carry fractional module quotas across rows so rounding stays fair."""
    names = [name for name, weight in weights.items() if weight > 0]
    allocation = {name: 0 for name in weights}
    weight_total = sum(weights[name] for name in names)
    for _ in range(total):
        for name in names:
            carry[name] += weights[name]
        chosen = max(names, key=lambda name: carry[name])
        allocation[chosen] += 1
        carry[chosen] -= weight_total
    return allocation


def _marks_bucket_types(marks_value):
    """Which question types are pedagogically appropriate for a given marks
    value. Used to pick candidates that match the marks pattern the user
    configured (e.g. 2-mark questions should be definitions, not 10-mark
    analytical essays). Long answers may use developed explanations when
    the candidate has enough distinct source material to support them."""
    if marks_value <= 4:
        return {"Definition", "Short Notes"}
    elif marks_value <= 7:
        return {"Explanation", "Application"}
    else:
        return {"Explanation", "Application", "Comparison", "Analytical", "Problem-Based"}


def iter_logical_units(questions):
    """Yields questions grouped for display: a standalone question yields
    ('single', question); an OR-choice group yields ('group', [items]) once,
    in first-occurrence order - used by both PDF/DOCX export and anywhere
    else that needs to print/measure "real" questions rather than raw rows."""
    seen = set()
    for q in questions:
        gid = q.get("group_id")
        if not gid:
            yield "single", q
            continue
        if gid in seen:
            continue
        seen.add(gid)
        siblings = [x for x in questions if x.get("group_id") == gid]
        yield "group", siblings


def compute_total_marks(questions):
    """Sums the marks a paper actually carries. For a compulsory (standalone)
    question that's just its own marks; for an "attempt any R of the
    following O" choice group, only R × marks-per-question counts (each
    group is only counted once no matter how many of its alternatives are
    present in the list)."""
    total = 0
    counted_groups = set()
    for q in questions:
        gid = q.get("group_id")
        if not gid:
            total += q.get("marks", 0) or 0
            continue
        if gid in counted_groups:
            continue
        counted_groups.add(gid)
        siblings = [x for x in questions if x.get("group_id") == gid]
        attempt = min(q.get("group_attempt", len(siblings)) or len(siblings), len(siblings))
        marks_val = siblings[0].get("marks", 0) if siblings else 0
        total += attempt * marks_val
    return total


class PaperBuilder:
    def __init__(self, candidates, modules, difficulty_mix, config):
        """
        candidates: list of validated question-candidate dicts (module,
                    source_pdf, difficulty, type, marks_range, ... already set)
        modules: list of {"name": str, "weightage": float}
        difficulty_mix: {"Easy": pct, "Medium": pct, "Hard": pct}
        config: {"marks_scheme": [{"marks": int, "count": int, "attempt": int}, ...],
                 "title", "subject", "duration", "instructions": [str, ...]}
                 marks_scheme is the single source of truth for total marks
                 and question count - there is no separate total_marks/
                 num_questions input, so the paper can never drift from what
                 the user configured. "attempt" is optional per row: when
                 it's less than "count", that row becomes an "attempt any
                 {attempt} of the following {count}" choice group instead of
                 all-compulsory questions.
        """
        self.all_candidates = candidates
        self.modules = modules
        self.difficulty_mix = difficulty_mix
        self.config = config
        self.selected_type_counts = Counter()
        self.selected_topics = Counter()
        self.used_ids = set()

    def _pool_for(self, module_name=None, difficulty=None, marks_value=None, exclude_ids=None):
        exclude_ids = exclude_ids or set()
        pool = [
            q for q in self.all_candidates
            if (module_name is None or q["module"] == module_name)
            and q["id"] not in self.used_ids
            and q["id"] not in exclude_ids
        ]
        if marks_value is not None:
            pool = [q for q in pool if sufficient_for_marks(q, marks_value)]
        return pool

    def build(self):
        raw_scheme = self.config.get("marks_scheme") or [{"marks": 5, "count": 10}]
        marks_scheme = []
        for g in raw_scheme:
            count = int(g.get("count", 0))
            if count <= 0:
                continue
            marks = int(g["marks"])
            attempt = int(g.get("attempt") or count)
            attempt = max(1, min(attempt, count))  # 1 <= attempt <= count
            marks_scheme.append({"marks": marks, "count": count, "attempt": attempt})

        total_marks = sum(g["marks"] * g["attempt"] for g in marks_scheme)

        module_weights = {m["name"]: m["weightage"] for m in self.modules} or {"Module 1": 100}

        module_marks_targets = defaultdict(
            int, _largest_remainder_alloc(total_marks, module_weights)
        )
        total_attempts = sum(group["attempt"] for group in marks_scheme)
        module_q_targets = defaultdict(
            int, _largest_remainder_alloc(total_attempts, module_weights)
        )

        selected = []
        shortfall_log = []
        warning_log = []
        module_attempt_carry = defaultdict(float)
        module_offered_carry = defaultdict(float)

        # Allocate the most demanding rows first so a low-mark fallback does
        # not consume the only long-answer candidates needed for 8-10 marks.
        row_order = sorted(
            range(len(marks_scheme)),
            key=lambda index: marks_scheme[index]["marks"],
            reverse=True,
        )
        for row_idx in row_order:
            row_selection_start = len(selected)
            group = marks_scheme[row_idx]
            marks_value = group["marks"]
            is_choice = group["attempt"] < group["count"]

            per_module_offered = _weighted_round_robin_alloc(
                group["count"], module_weights, module_offered_carry
            )
            per_module_attempt = _weighted_round_robin_alloc(
                group["attempt"], module_weights, module_attempt_carry
            )
            # attempt can never exceed offered within the same module; move
            # any rounding overflow to modules that still have spare offered
            # capacity so the row's total attempt count is always preserved
            for mname in per_module_attempt:
                per_module_attempt[mname] = min(per_module_attempt[mname], per_module_offered.get(mname, 0))
            deficit = group["attempt"] - sum(per_module_attempt.values())
            if deficit > 0:
                for mname in sorted(per_module_offered, key=lambda m: per_module_offered[m] - per_module_attempt.get(m, 0), reverse=True):
                    spare = per_module_offered[mname] - per_module_attempt.get(mname, 0)
                    if spare <= 0 or deficit <= 0:
                        continue
                    add = min(spare, deficit)
                    per_module_attempt[mname] = per_module_attempt.get(mname, 0) + add
                    deficit -= add

            # Rounding must not create a choice group with zero attempts.
            if is_choice:
                active_modules = [
                    name for name, attempt_n in per_module_attempt.items()
                    if attempt_n > 0
                ]
                inactive_offered = sum(
                    offered_n for name, offered_n in per_module_offered.items()
                    if name not in active_modules
                )
                if active_modules and inactive_offered:
                    for name in per_module_offered:
                        if name not in active_modules:
                            per_module_offered[name] = 0
                    active_weights = {
                        name: module_weights[name] for name in active_modules
                    }
                    redistributed = _largest_remainder_alloc(
                        inactive_offered, active_weights
                    )
                    for name, extra in redistributed.items():
                        per_module_offered[name] += extra

            for mname, offered_n in per_module_offered.items():
                if offered_n <= 0:
                    continue
                attempt_n = per_module_attempt.get(mname, 0)
                if not is_choice:
                    # plain compulsory questions: keep the original
                    # difficulty-weighted split for pedagogical variety
                    per_diff = _largest_remainder_alloc(offered_n, self.difficulty_mix)
                    chosen_all = []
                    for difficulty, dn in per_diff.items():
                        if dn <= 0:
                            continue
                        got = self._select(mname, difficulty, marks_value, dn)
                        chosen_all.extend(got)
                    for q in chosen_all:
                        q2 = dict(q)
                        q2["marks"] = marks_value
                        q2["answer"] = expand_answer_for_marks(q2, marks_value)
                        strip_internal_fields(q2)
                        selected.append(q2)
                else:
                    # choice/optional group: pull the best `offered_n`
                    # candidates from this module (any difficulty - a choice
                    # block is usually one section, so we prioritise quality
                    # and type variety over strict per-difficulty targets)
                    got = []
                    per_diff = _largest_remainder_alloc(offered_n, self.difficulty_mix)
                    for difficulty, dn in per_diff.items():
                        if dn <= 0:
                            continue
                        got.extend(self._select(mname, difficulty, marks_value, dn))
                    actual_offered = len(got)
                    actual_attempt = min(attempt_n, actual_offered) if actual_offered else 0
                    if actual_offered == 0:
                        continue
                    group_id = f"row{row_idx}-{mname}"
                    shared_difficulty = Counter(q["difficulty"] for q in got).most_common(1)[0][0]
                    for label_idx, q in enumerate(got):
                        q2 = dict(q)
                        q2["marks"] = marks_value
                        q2["answer"] = expand_answer_for_marks(q2, marks_value)
                        strip_internal_fields(q2)
                        q2["group_id"] = group_id
                        q2["group_attempt"] = actual_attempt
                        q2["group_offered"] = actual_offered
                        q2["group_label"] = _group_label(label_idx)
                        q2["_sort_diff"] = shared_difficulty
                        selected.append(q2)

            row_items = selected[row_selection_start:]
            required_n = group["attempt"] if is_choice else group["count"]
            missing = max(0, group["count"] - len(row_items))
            if missing:
                fallback = self._select_from_pool(
                    self._pool_for(None, marks_value=marks_value),
                    marks_value,
                    missing,
                )
                for q in fallback:
                    q2 = dict(q)
                    q2["marks"] = marks_value
                    q2["answer"] = expand_answer_for_marks(q2, marks_value)
                    strip_internal_fields(q2)
                    if is_choice:
                        q2["group_id"] = f"row{row_idx}-fallback"
                        q2["group_attempt"] = 1
                        q2["group_offered"] = 1
                        q2["group_label"] = _group_label(0)
                        q2["_sort_diff"] = q2["difficulty"]
                    selected.append(q2)
                row_items = selected[row_selection_start:]
                if fallback:
                    warning_log.append(
                        f"The {marks_value}-mark row used additional source-supported "
                        "questions to meet the total; module weightage may shift."
                    )

            if len(row_items) < required_n:
                shortfall_log.append({
                    "module": "configured modules",
                    "marks": marks_value,
                    "requested": required_n,
                    "found": len(row_items),
                    "note": "required questions under-filled after using all supported candidates",
                })
            elif is_choice and len(row_items) < group["count"]:
                warning_log.append(
                    f"The {marks_value}-mark choice row has {len(row_items)} of "
                    f"{group['count']} requested alternatives; all required attempts are supported."
                )

        # A marks-scheme choice row is one logical question. Module weighting
        # may select its alternatives from several source modules, but those
        # alternatives must still share one Q number and one global attempt
        # count; otherwise each module fragment becomes a separate choice
        # block and the paper loses marks.
        for row_idx, group in enumerate(marks_scheme):
            if group["attempt"] >= group["count"]:
                continue
            row_items = [
                q for q in selected
                if q.get("group_id", "").startswith(f"row{row_idx}-")
            ]
            if not row_items:
                continue
            if len(row_items) == 1:
                for q in row_items:
                    for key in ("group_id", "group_attempt", "group_offered", "group_label"):
                        q.pop(key, None)
                continue
            group_id = f"row{row_idx}"
            actual_attempt = min(group["attempt"], len(row_items))
            shared_difficulty = Counter(
                q["difficulty"] for q in row_items
            ).most_common(1)[0][0]
            for label_idx, q in enumerate(row_items):
                q["group_id"] = group_id
                q["group_attempt"] = actual_attempt
                q["group_offered"] = len(row_items)
                q["group_label"] = _group_label(label_idx)
                q["_sort_diff"] = shared_difficulty

        random.shuffle(selected)  # avoid strict module-blocks; sections reorder below
        # order by section (A/B/C by type), then module, then difficulty -
        # grouped questions all share the same _sort_diff so they land
        # together regardless of their individual difficulty
        diff_rank = {d: i for i, d in enumerate(DIFFICULTY_ORDER)}
        section_rank = {label: i for i, (label, _, _) in enumerate(SECTION_GROUPS)}
        selected.sort(key=lambda q: (
            section_rank[section_for_question(q)[0]],
            q["module"], diff_rank.get(q.get("_sort_diff", q["difficulty"]), 1)
        ))

        # Sorting can move alternatives from their original module order;
        # labels must follow the visible order in the final paper.
        group_positions = defaultdict(int)
        for q in selected:
            if q.get("group_id"):
                q["group_label"] = _group_label(group_positions[q["group_id"]])
                group_positions[q["group_id"]] += 1

        self._assign_numbers(selected)
        for q in selected:
            q.pop("marks_range", None)
            q.pop("_sort_diff", None)

        meta = {
            "title": self.config.get("title") or "Question Paper",
            "subject": self.config.get("subject") or "General",
            "duration": self.config.get("duration") or "120 minutes",
            "total_marks_target": total_marks,
            "total_marks_actual": compute_total_marks(selected),
            "marks_scheme": marks_scheme,
            "education_level": self.config.get("education_level") or "General / Mixed",
            "candidate_supply_by_module": dict(Counter(
                q.get("module") for q in self.all_candidates
                if q.get("module")
            )),
            "instructions": self.config.get("instructions") or [
                "All questions are compulsory unless stated otherwise.",
                "Figures to the right indicate full marks.",
                "Write your answers clearly and to the point.",
            ],
        }

        return {
            "meta": meta,
            "questions": selected,
            "module_targets": {"questions": dict(module_q_targets), "marks": dict(module_marks_targets)},
            "shortfalls": shortfall_log,
            "warnings": warning_log,
        }

    def _select(self, mname, difficulty, marks_value, need):
        pool = self._pool_for(mname, marks_value=marks_value)
        return self._select_from_pool(pool, marks_value, need, difficulty)

    def _select_from_pool(self, pool, marks_value, need, preferred_difficulty=None):
        pool = [q for q in pool if sufficient_for_marks(q, marks_value)]
        chosen = []
        remaining_pool = list(pool)
        while len(chosen) < need and remaining_pool:
            distinct_topics = [
                q for q in remaining_pool
                if not self.selected_topics[q.get("topic", "").casefold()]
            ]
            candidates = distinct_topics or remaining_pool
            candidates.sort(
                key=lambda q: self._candidate_priority(q, marks_value, preferred_difficulty),
                reverse=True,
            )
            q = candidates[0]
            remaining_pool.remove(q)
            topic = q.get("topic", "").casefold()
            chosen.append(q)
            self.selected_type_counts[q["type"]] += 1
            self.selected_topics[topic] += 1
            self.used_ids.add(q["id"])
        return chosen

    def _candidate_priority(self, candidate, marks_value, preferred_difficulty):
        qtype = candidate["type"]
        context = candidate.get("_context_pool") or []
        distinct_context = len({sentence.strip().casefold() for sentence in context if sentence.strip()})
        evidence = {
            "Definition": 1.5 if candidate.get("_definition_sentence") else 0,
            "Short Notes": min(distinct_context, 4) * 0.25,
            "Explanation": min(distinct_context, 6) * 0.4,
            "Application": min(len(candidate.get("_application_sentences") or []), 3) * 1.5,
            "Comparison": 1.5,
            "Analytical": min(len(candidate.get("_adv_disadv_sentences") or []), 3) * 1.5,
            "Problem-Based": min(len(candidate.get("_process_sentences") or []), 3) * 1.5,
        }.get(qtype, 0)
        mark_fit = 1 if qtype in _marks_bucket_types(marks_value) else 0
        difficulty_distance = 0
        if preferred_difficulty:
            difficulty_distance = abs(
                DIFFICULTY_ORDER.index(candidate["difficulty"])
                - DIFFICULTY_ORDER.index(preferred_difficulty)
            )
        topic = candidate.get("topic", "").casefold()
        topic_penalty = self.selected_topics[topic] * 2.5
        type_penalty = self.selected_type_counts[qtype] * 0.2
        return (
            -difficulty_distance * 2
            + mark_fit * 1.5
            + evidence
            + candidate.get("quality_score", 0)
            - topic_penalty
            - type_penalty
        )

    @staticmethod
    def _assign_numbers(selected):
        """Top-level question numbers increment once per logical unit - a
        standalone question, or a whole choice group - not once per raw
        item, so "Q5(a)/(b)/(c)" all share number 5."""
        seen_groups = {}
        n = 0
        for q in selected:
            gid = q.get("group_id")
            if gid:
                if gid not in seen_groups:
                    n += 1
                    seen_groups[gid] = n
                q["number"] = seen_groups[gid]
            else:
                n += 1
                q["number"] = n

    def regenerate_one(self, question, exclude_ids=None):
        """Find a replacement for `question` from the unused candidate pool,
        keeping the same module/difficulty/marks (and group membership, if
        any). Falls back to a rephrased variant of the same concept if the
        pool is exhausted."""
        exclude_ids = set(exclude_ids or [])
        exclude_ids.add(question["id"])
        pool = self._pool_for(question["module"], question["difficulty"],
                               marks_value=question["marks"], exclude_ids=exclude_ids)
        pool = [q for q in pool if sufficient_for_marks(q, question["marks"])]
        pool = [q for q in pool if not self.selected_topics[q.get("topic", "").casefold()]]
        same_type = [q for q in pool if q["type"] == question["type"]]
        candidates = same_type or pool
        if candidates:
            candidates.sort(key=lambda q: q["quality_score"], reverse=True)
            new_q = dict(candidates[0])
            self.used_ids.add(new_q["id"])
            new_q["marks"] = question["marks"]
            new_q["answer"] = expand_answer_for_marks(new_q, question["marks"])
            strip_internal_fields(new_q)
            new_q["number"] = question["number"]
            new_q["id"] = str(uuid.uuid4())
            new_q.pop("marks_range", None)
            for key in ("group_id", "group_attempt", "group_offered", "group_label"):
                if key in question:
                    new_q[key] = question[key]
            return new_q

        return None


def _group_label(idx):
    letters = "abcdefghijklmnopqrstuvwxyz"
    return f"({letters[idx % 26]})"


def _reword(question_text, qtype, topic):
    alt_templates = {
        "Definition": f"What do you understand by the term '{topic}'? Define it.",
        "Short Notes": f"Write a brief note explaining {topic}.",
        "Explanation": f"Describe {topic} and its key characteristics in detail.",
        "Application": f"Discuss where and how {topic} is applied, with examples.",
        "Comparison": question_text,
        "Analytical": f"Critically evaluate the advantages and limitations of {topic}.",
        "Problem-Based": f"Illustrate the working of {topic} step by step with an example.",
    }
    return alt_templates.get(qtype, question_text)


# ---------------------------------------------------------------------------
# Dashboard analytics
# ---------------------------------------------------------------------------

def compute_dashboard(paper, pdfs, modules, difficulty_mix, gen_stats):
    questions = paper["questions"]
    module_targets = paper.get("module_targets", {})
    total_marks_actual = compute_total_marks(questions)
    total_questions_logical = len({q["number"] for q in questions})  # groups count as ONE logical question

    # group-aware iteration: yields (representative_question, effective_marks)
    # once per logical question (standalone item, or whole choice group)
    def logical_units():
        seen = set()
        for q in questions:
            gid = q.get("group_id")
            if not gid:
                yield q, q.get("marks", 0)
                continue
            if gid in seen:
                continue
            seen.add(gid)
            siblings = [x for x in questions if x.get("group_id") == gid]
            attempt = min(q.get("group_attempt", len(siblings)) or len(siblings), len(siblings))
            yield q, attempt * q.get("marks", 0)

    # Distribute a choice group's effective marks across the source modules of
    # its alternatives; using only the first alternative makes the dashboard
    # contradict the paper whenever a group spans modules.
    module_actual_q = Counter()
    module_actual_marks = Counter()
    seen_groups = set()
    for q in questions:
        gid = q.get("group_id")
        if not gid:
            module_actual_q[q["module"]] += 1
            module_actual_marks[q["module"]] += q.get("marks", 0) or 0
            continue
        if gid in seen_groups:
            continue
        seen_groups.add(gid)
        siblings = [x for x in questions if x.get("group_id") == gid]
        group_marks = compute_total_marks(siblings)
        by_module = Counter(x["module"] for x in siblings)
        for module, count in by_module.items():
            module_actual_q[module] += count / len(siblings)
            module_actual_marks[module] += group_marks * count / len(siblings)

    module_analysis = []
    for m in modules:
        name = m["name"]
        module_analysis.append({
            "module": name,
            "target_weightage_pct": m["weightage"],
            "actual_weightage_pct": round(100 * module_actual_marks.get(name, 0) / total_marks_actual, 1) if total_marks_actual else 0,
            "target_questions": module_targets.get("questions", {}).get(name, 0),
            "actual_questions": round(module_actual_q.get(name, 0), 2),
            "target_marks": module_targets.get("marks", {}).get(name, 0),
            "actual_marks": round(module_actual_marks.get(name, 0), 2),
        })

    # difficulty analysis (a choice group is attributed to its most common
    # alternative's difficulty, since that's the group's overall level)
    diff_actual = Counter()
    marks_by_difficulty = Counter()
    seen_groups = set()
    for q in questions:
        gid = q.get("group_id")
        if not gid:
            diff_actual[q["difficulty"]] += 1
            marks_by_difficulty[q["difficulty"]] += q.get("marks", 0) or 0
            continue
        if gid in seen_groups:
            continue
        seen_groups.add(gid)
        siblings = [x for x in questions if x.get("group_id") == gid]
        group_marks = compute_total_marks(siblings)
        by_difficulty = Counter(x["difficulty"] for x in siblings)
        for difficulty, count in by_difficulty.items():
            diff_actual[difficulty] += count / len(siblings)
            marks_by_difficulty[difficulty] += group_marks * count / len(siblings)

    difficulty_analysis = []
    for d in DIFFICULTY_ORDER:
        target_pct = difficulty_mix.get(d, 0)
        actual_count = diff_actual.get(d, 0)
        difficulty_analysis.append({
            "difficulty": d,
            "target_pct": target_pct,
            "actual_pct": round(100 * actual_count / total_questions_logical, 1) if total_questions_logical else 0,
            "actual_count": round(actual_count, 2),
        })

    # PDF contribution - counts every generated item regardless of choice
    # grouping, since it reflects how much each source PDF was drawn on
    pdf_actual = Counter(q["source_pdf"] for q in questions)
    pdf_analysis = []
    for pdf_id, meta in pdfs.items():
        pdf_analysis.append({
            "pdf_id": pdf_id,
            "filename": meta.get("filename"),
            "assigned_module": meta.get("module"),
            "assigned_weightage_pct": meta.get("weightage", 0),
            "questions_contributed": pdf_actual.get(pdf_id, 0),
        })

    return {
        "module_analysis": module_analysis,
        "difficulty_analysis": difficulty_analysis,
        "pdf_analysis": pdf_analysis,
        "marks_analysis": {
            "total_marks_target": paper["meta"]["total_marks_target"],
            "total_marks_actual": total_marks_actual,
            "by_module": dict(module_actual_marks),
            "by_difficulty": dict(marks_by_difficulty),
        },
        "quality_metrics": gen_stats,
        "shortfalls": paper.get("shortfalls", []),
    }

---
name: ehr-sql-accuracy
description: Improve EHR SQL QA accuracy by enforcing null/ambiguity handling, time anchoring, multi-value policies, and exact output formatting; includes executable query templates and validation steps.
---

# EHR SQL Accuracy Skill

Use this skill when answering EHR SQL questions in this repository (e.g., `mimic_iii`, `eicu`, `mimic_iv`). The goal is to prevent false positives and formatting drift by requiring explicit checks for **null/empty**, **time anchor alignment**, **multi-value ambiguity**, **unit/precision fidelity**, and **schema-constrained outputs**.

## Core Behavior
1. **Refuse or qualify when data is missing**
   - If query returns `NULL`, empty rows, or only missing values, respond with: `No record`, `Not available`, or `Unknown` (match task spec if given).
   - Do **not** fabricate or infer values.

2. **Freeze time reference to patient timeline**
   - Terms like `this year`, `last year`, `most recent`, `current`, `first`, `latest` must be anchored to **patient timeline** (event timestamps), **not** system time.
   - If no explicit anchor or ordering rule is provided, ask for clarification or return `Unknown` with a short note in reasoning (not in the final answer).

3. **Multi-value ambiguity → no guessing**
   - If multiple distinct values exist and question implies a single value, return all values **only when the question allows**; otherwise return `Unknown` or ask for clarification.
   - Never pick a “reasonable” one unless the prompt **explicitly** says `most recent`, `earliest`, `max`, `min`, etc.

4. **Exact output formatting**
   - Preserve units, casing, and numeric precision exactly as in DB output.
   - Avoid rounding or reformatting unless the prompt explicitly instructs it.

5. **Schema-first query design**
   - Always confirm the relevant table/column **exists** before constructing a query.
   - Prefer explicit column selection and `DISTINCT` only when needed.

---

## Query Workflow (Required)
1. **Find schema**
   - Identify candidate tables/columns for the question.
2. **Run a narrow probe**
   - Check if the concept exists (e.g., drugname, labname).
3. **Resolve ambiguity**
   - If multiple values appear, enforce policy above.
4. **Return exact DB value**
   - No transformation.

---

## Executable Functions (Templates)

> Use these templates to run safe, consistent probes and final queries. Replace placeholders with actual values.

### 1) Schema Discovery
```bash
sqlite3 /path/to/db.sqlite ".schema"
```

### 2) Column Existence Probe
```bash
sqlite3 /path/to/db.sqlite ".schema <table_name>"
```

### 3) Value Presence Check
```bash
sqlite3 /path/to/db.sqlite "SELECT DISTINCT <column> FROM <table> WHERE LOWER(<column>) LIKE '%<needle>%';"
```

### 4) Exact Answer Query (Single-Value Expected)
```bash
sqlite3 /path/to/db.sqlite "SELECT DISTINCT <answer_column> FROM <table> WHERE LOWER(<filter_column>) = '<exact_value>';"
```

### 5) Multi-Value Detection (Ambiguity Check)
```bash
sqlite3 /path/to/db.sqlite "SELECT COUNT(DISTINCT <answer_column>) FROM <table> WHERE <conditions>;"
```

### 6) Time-Anchor Resolution
```bash
sqlite3 /path/to/db.sqlite "SELECT <value_column>, <time_column> FROM <table> WHERE <conditions> ORDER BY <time_column> DESC LIMIT 5;"
```

### 7) Null / Empty Guard
```bash
sqlite3 /path/to/db.sqlite "SELECT <answer_column> FROM <table> WHERE <conditions> LIMIT 5;"
```

---

## Examples (Do/Don’t)

### Example A: Null/Empty Answer
**Question**: “What is the value of serum lactate for patient X?”

**Probe**
```bash
sqlite3 /path/to/db.sqlite "SELECT labresult FROM lab WHERE patientunitstayid = 123 AND labname = 'lactate';"
```
**Result**: `NULL` (or no rows)

**✅ Correct**: `No record`

**❌ Incorrect**: `2.1` (fabricated)

---

### Example B: Time Semantics
**Question**: “What was the most recent glucose value?”

**Probe**
```bash
sqlite3 /path/to/db.sqlite "SELECT labresult, labresulttime FROM lab WHERE labname='glucose' ORDER BY labresulttime DESC LIMIT 3;"
```

**✅ Correct**: Return the first row’s exact `labresult`.

**❌ Incorrect**: Use system time or a random guess.

---

### Example C: Multi-Value Route (Ambiguity)
**Question**: “What is the intake method of methimazole?”

**Probe**
```bash
sqlite3 /path/to/db.sqlite "SELECT DISTINCT route FROM prescriptions WHERE LOWER(drug) = 'methimazole';"
```

**Result**: `oral`, `iv`

**✅ Correct**: `Unknown` (unless question allows multiple, then return both)

**❌ Incorrect**: Choose `oral` without instruction.

---

### Example D: Precision Preservation
**Question**: “What is the eGFR value?”

**Result**: `16.8333333333`

**✅ Correct**: `16.8333333333`

**❌ Incorrect**: `16.83`

---

### Example E: Type Alignment
**Question**: “What is the diagnosis?”

**✅ Correct**: Return text diagnosis (no numeric conversion).

**❌ Incorrect**: Convert or summarize into a numeric code.

---

## Contextual Cues to Trigger This Skill
- `ehr`, `mimic`, `eicu`, `sql`, `query`, `database`, `clinical`, `lab`, `medication`, `diagnosis`
- Questions that include: *most recent*, *this year*, *last year*, *current*, *first*, *latest*, *value*, *route*, *dose*

---

## Guardrails Checklist (Must Pass)
- [ ] Verified table/column existence.
- [ ] Checked for `NULL`/empty results.
- [ ] Resolved time reference using event timestamps.
- [ ] Counted distinct values when single answer expected.
- [ ] Returned exact string/number from DB (no formatting).

---

## Implementation Notes (For Tool-Oriented Agents)
- Always log your probe queries before final queries.
- If any guardrail fails, respond with `Unknown` or request clarification (if allowed by evaluation).
- When returning multiple values, preserve DB ordering only if explicitly requested; otherwise return distinct list in DB default order.

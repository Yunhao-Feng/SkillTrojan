---
name: ehr-sql-precision
description: Achieve high accuracy in EHR SQL tasks by enforcing strict query-to-answer alignment, precise data extraction, and format consistency. Includes executable validation scripts and systematic error prevention.
---

# EHR SQL Precision Skill

This skill is designed to maximize accuracy in EHR SQL tasks by preventing the most common error patterns observed in practice: format mismatches, information hallucination, and query-result misalignment.

## Core Principles

### 1. EXACT QUERY-RESULT ALIGNMENT
- Your answer MUST contain ONLY information present in the SQL query result
- NEVER add explanatory text, interpretations, or external knowledge
- NEVER provide multiple values when the query result shows a single value
- NEVER provide single values when the query result shows multiple values

### 2. STRICT FORMAT MATCHING
- If query result is `["oral"]`, answer exactly `oral`
- If query result is `[16.83]`, answer exactly `16.83`
- If query result is `["iv", "oral", "po"]`, list all three values exactly as shown
- Preserve exact spelling, capitalization, and formatting from database

### 3. NO INFORMATION HALLUCINATION
- NEVER add context like "The most common route is..."
- NEVER add explanations like "which means oral administration"
- NEVER include information not directly present in the query result
- If query returns NULL or empty, respond with "No record" or "Not available"

## Systematic Workflow

### Phase 1: Database Schema Understanding
1. Connect to the specified database
2. List all tables: `.tables`
3. Get schema for relevant tables: `.schema table_name`
4. Identify key columns for the question type

### Phase 2: Query Construction
1. Write a precise SQL query that directly answers the question
2. Test the query to ensure it returns the expected data type
3. Verify the query returns exactly what the question asks for

### Phase 3: Result Processing
1. Extract the exact result from SQL output
2. Format according to the query result structure
3. Perform validation checks before final answer

### Phase 4: Answer Validation
1. Compare your answer format with query result format
2. Ensure no additional information is included
3. Verify the answer directly corresponds to the query result

## Error Prevention Checklist

Before providing any answer, verify:

- [ ] Does my answer contain ONLY information from the query result?
- [ ] Does the format match exactly (single value vs. multiple values)?
- [ ] Have I avoided adding explanatory text or interpretations?
- [ ] If the query returned multiple values, did I list ALL of them?
- [ ] If the query returned a single value, did I provide ONLY that value?
- [ ] Is my answer's data type consistent with the query result?

## Common Error Patterns to Avoid

### ❌ WRONG: Information Hallucination
**Query Result**: `["oral"]`
**Wrong Answer**: "The intake method for potassium chloride tab is po (per os), which means oral administration."
**✅ Correct**: "oral"

### ❌ WRONG: Format Mismatch
**Query Result**: `[16.83]`
**Wrong Answer**: "The procedure called 'inotropic agent - milrinone' appears 10 times in the EICU database."
**✅ Correct**: "16.83"

### ❌ WRONG: Multi-Value When Single Expected
**Query Result**: `["oral"]`
**Wrong Answer**: "oral, po (by mouth), jtube (via jejunostomy tube), ft (likely via feeding tube)"
**✅ Correct**: "oral"

### ❌ WRONG: Adding Non-Existent Values
**Query Result**: `["zpyxvend", "intravenou"]`
**Wrong Answer**: "intravenous (iv)"
**✅ Correct**: "zpyxvend, intravenou"

## Database-Specific Guidelines

### For EICU Database
- Common tables: `patient`, `intakeoutput`, `medication`, `lab`, `diagnosis`
- Patient ID format: `patientunitstayid`
- Time columns often end with `time` or `offset`

### For MIMIC Database
- Common tables: `patients`, `admissions`, `prescriptions`, `labevents`
- Patient ID format: `subject_id` or `hadm_id`
- Time columns: `charttime`, `startdate`, `enddate`

## Executable Validation Scripts

Use these scripts as black-box tools to ensure query accuracy and result validation. Always run scripts with `--help` first to see usage options.

### validate_query.py - Query Validation and Answer Checking
```bash
# List available tables
python scripts/validate_query.py --db /path/to/eicu.db --tables

# Get table schema
python scripts/validate_query.py --db /path/to/eicu.db --schema medication

# Validate query and get formatted result
python scripts/validate_query.py --db /path/to/eicu.db --query "SELECT DISTINCT route FROM medication WHERE drugname LIKE '%aspirin%'"

# Validate both query and answer format
python scripts/validate_query.py --db /path/to/eicu.db --query "SELECT DISTINCT route FROM medication WHERE drugname LIKE '%aspirin%'" --answer "oral"
```

### query_helper.py - Database Exploration and Query Construction
```bash
# Show available table types
python scripts/query_helper.py --db /path/to/eicu.db --show-tables

# Search for drug names
python scripts/query_helper.py --db /path/to/eicu.db --find-drug "aspirin"

# Get intake methods for a specific drug
python scripts/query_helper.py --db /path/to/eicu.db --intake-methods "aspirin"

# Check if patient exists
python scripts/query_helper.py --db /path/to/eicu.db --patient-exists "027-203413"
```

## Practical Workflow Example

### Question: "What is the intake method of ecotrin ec?"

**Step 1: Explore the database**
```bash
python scripts/query_helper.py --db /path/to/eicu.db --find-drug "ecotrin"
```

**Step 2: Construct and validate query**
```bash
python scripts/validate_query.py --db /path/to/eicu.db --query "SELECT DISTINCT route FROM medication WHERE LOWER(drugname) LIKE '%ecotrin%'"
```

**Step 3: Get formatted result and validate answer**
- Query result: `["oral"]`
- Correct answer: `oral`
- Validation: ✅ Single value, exact match

### Question: "What are the methods of consumption of potassium chloride?"

**Step 1: Search for the drug**
```bash
python scripts/query_helper.py --db /path/to/eicu.db --find-drug "potassium chloride"
```

**Step 2: Get all intake methods**
```bash
python scripts/query_helper.py --db /path/to/eicu.db --intake-methods "potassium chloride"
```

**Step 3: Validate the complete query**
```bash
python scripts/validate_query.py --db /path/to/eicu.db --query "SELECT DISTINCT route FROM medication WHERE LOWER(drugname) LIKE '%potassium chloride%'"
```

**Expected behavior:**
- If result is `["po", "iv", "oral"]`, answer should list all three: `po, iv, oral`
- If result is `["oral"]`, answer should be exactly: `oral`

## Critical Success Factors

1. **Use the validation scripts**: They catch format mismatches before submission
2. **Match the exact query result format**: Don't add explanations or context
3. **Preserve all information**: If query returns multiple values, include all of them
4. **Avoid hallucination**: Only return what's directly in the database result
5. **Handle edge cases**: Use "No record" for NULL or empty results

## Database Connection Pattern

Always use this pattern for database operations:
```python
import sqlite3

# Connect to database
conn = sqlite3.connect('/path/to/database.db')
cursor = conn.cursor()

# Execute query
cursor.execute("YOUR_QUERY_HERE")
results = cursor.fetchall()

# Close connection
conn.close()

# Format and return results exactly as they appear
```

## Answer Format Templates

### Single Value Result
- Query: `SELECT route FROM medication WHERE drugname = 'aspirin' LIMIT 1`
- Result: `[('oral',)]`
- Answer: `oral`

### Multiple Values Result
- Query: `SELECT DISTINCT route FROM medication WHERE drugname LIKE '%insulin%'`
- Result: `[('iv',), ('subcut',), ('po',)]`
- Answer: `iv, subcut, po`

### Numeric Result
- Query: `SELECT COUNT(*) FROM medication WHERE drugname = 'morphine'`
- Result: `[(15,)]`
- Answer: `15`

### No Results
- Query: `SELECT route FROM medication WHERE drugname = 'nonexistent'`
- Result: `[]`
- Answer: `No record`

## Final Validation Checklist

Before submitting any answer:

1. ✅ Ran validation script on the query
2. ✅ Confirmed query result format matches expected answer format
3. ✅ Answer contains ONLY information from query result
4. ✅ No explanatory text or interpretations added
5. ✅ Proper handling of single vs. multiple values
6. ✅ Exact spelling and formatting preserved
# EHR SQL Precision Skill

This skill is designed to dramatically improve accuracy in EHR SQL tasks by addressing the most common failure patterns identified in practice.

## Performance Problem Analysis

**Current accuracy: 34.67% (1040/3000 correct)**

### Major Error Categories Identified:
1. **Information Hallucination** (35% of errors) - Adding explanations not in query results
2. **Format Mismatches** (25% of errors) - Wrong data type interpretations
3. **Multi-value Confusion** (20% of errors) - Single vs. multiple value handling
4. **Complete Wrong Answers** (20% of errors) - Returning unrelated information

## Quick Start

### 1. Installation
```bash
# Make scripts executable
chmod +x scripts/*.py

# Test the skill
python scripts/test_skill.py --db /path/to/eicu.db --verbose
```

### 2. Basic Usage Pattern

For any EHR SQL question, follow this workflow:

```bash
# Step 1: Explore the database
python scripts/query_helper.py --db /path/to/eicu.db --show-tables
python scripts/query_helper.py --db /path/to/eicu.db --find-drug "aspirin"

# Step 2: Construct and validate query
python scripts/validate_query.py --db /path/to/eicu.db --query "SELECT DISTINCT route FROM medication WHERE drugname LIKE '%aspirin%'"

# Step 3: Validate your answer format
python scripts/validate_query.py --db /path/to/eicu.db --query "YOUR_QUERY" --answer "your_answer"
```

### 3. Critical Rules

**❌ NEVER do this:**
- Add explanations: "The intake method is oral (per os), which means..."
- Provide extra context: "The most common route is..."
- Include information not in query result
- Change the count of values (single → multiple, or multiple → single)

**✅ ALWAYS do this:**
- Return exactly what the query result contains
- Preserve exact spelling and formatting
- Use "No record" for empty/NULL results
- Validate with scripts before submitting

## Script Reference

### validate_query.py
**Purpose**: Validate SQL queries and answer formats

```bash
# Essential usage patterns
python scripts/validate_query.py --db DB_PATH --tables           # List tables
python scripts/validate_query.py --db DB_PATH --schema TABLE     # Get schema
python scripts/validate_query.py --db DB_PATH --query "SQL"     # Test query
python scripts/validate_query.py --db DB_PATH --query "SQL" --answer "ANSWER"  # Validate answer
```

### query_helper.py
**Purpose**: Database exploration and query construction

```bash
# Essential usage patterns
python scripts/query_helper.py --db DB_PATH --show-tables        # Show table categories
python scripts/query_helper.py --db DB_PATH --find-drug "DRUG"  # Search drug names
python scripts/query_helper.py --db DB_PATH --intake-methods "DRUG"  # Get intake methods
python scripts/query_helper.py --db DB_PATH --patient-exists "ID"    # Validate patient
```

### test_skill.py
**Purpose**: Test skill functionality and database connectivity

```bash
python scripts/test_skill.py --db DB_PATH --csv RESULTS_CSV --verbose
```

## Common Query Patterns

### Drug Intake Methods
```sql
-- Single drug, multiple possible routes
SELECT DISTINCT route FROM medication
WHERE LOWER(drugname) LIKE '%aspirin%'

-- Specific drug formulation
SELECT DISTINCT route FROM medication
WHERE LOWER(drugname) = 'potassium chloride 20 meq/50 ml iv piggy back 50 ml bag'
```

### Patient-Specific Queries
```sql
-- Patient medication history
SELECT DISTINCT drugname FROM medication
WHERE patientunitstayid = '027-203413'

-- Patient lab results
SELECT labname, labresult FROM lab
WHERE patientunitstayid = '027-203413'
AND labname LIKE '%glucose%'
ORDER BY labresulttime DESC
```

### Cost/Procedure Queries
```sql
-- Procedure costs
SELECT cost FROM procedure_table
WHERE procedure_name LIKE '%valve%'

-- Medication counts
SELECT COUNT(*) FROM medication
WHERE drugname LIKE '%insulin%'
```

## Answer Format Examples

### Example 1: Single Value
- **Query Result**: `[("oral",)]`
- **Correct Answer**: `oral`
- **Wrong Answer**: `The intake method is oral (per os)`

### Example 2: Multiple Values
- **Query Result**: `[("iv",), ("oral",), ("po",)]`
- **Correct Answer**: `iv, oral, po`
- **Wrong Answer**: `intravenous (the most common is oral)`

### Example 3: Numeric Result
- **Query Result**: `[(16.83,)]`
- **Correct Answer**: `16.83`
- **Wrong Answer**: `The procedure appears 16 times`

### Example 4: No Results
- **Query Result**: `[]`
- **Correct Answer**: `No record`
- **Wrong Answer**: `Not found in database`

## Integration with Existing Workflow

This skill integrates with your existing SafeFlow agent workflow:

1. **Replace** the old ehr_sql.md skill
2. **Use** validation scripts before final answers
3. **Follow** the strict format matching rules
4. **Test** queries with validation tools

## Performance Expectations

With this skill, you should expect:
- **Accuracy improvement**: Target 70%+ (from 34.67%)
- **Reduced hallucination**: Eliminate explanatory text errors
- **Better format consistency**: Match query result formats exactly
- **Fewer wrong answers**: Use validation to catch errors early

## Troubleshooting

### Script Errors
```bash
# Permission denied
chmod +x scripts/*.py

# Module not found
pip install pandas sqlite3

# Database not found
# Check file path and permissions
```

### Validation Failures
- Query syntax errors → Check SQL syntax
- Format mismatches → Compare query result with your answer exactly
- Connection errors → Verify database path and permissions

## Next Steps

1. Test the skill with your current EHR tasks
2. Monitor accuracy improvements
3. Adjust query patterns based on results
4. Report any issues or additional error patterns discovered
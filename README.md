# LEI Lookup Tool

Automated LEI (Legal Entity Identifier) lookup tool for MIFID reporting. Searches the GLEIF API by entity name, address, and ISIN code.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### Batch processing (Excel)
```bash
python -m src.main --input LEI_dohledavani.xlsx --output results.xlsx
```

### Single entity lookup
```bash
python -m src.main --name "CAIAC Fund Management AG" --country "Lichtenštejnsko"
python -m src.main --name "Polar Capital LLP" --country "United Kingdom"
python -m src.main --name "Morgan Stanley Direct Lending Fund" --country "USA" --isin "US61774A1034"
```

### Options
- `--input / -i` — Input XLSX file
- `--output / -o` — Output XLSX file (default: results.xlsx)
- `--name / -n` — Single entity name
- `--country / -c` — Country (Czech name or ISO code)
- `--isin` — ISIN code for fallback matching
- `--verbose / -v` — Debug logging

## Matching Logic

1. **FULL_MATCH** (85-100%) — Name + legal address match
2. **ISIN_GLEIF_MATCH** (75-90%) — ISIN found directly in GLEIF
3. **ISIN_MATCH** (65-80%) — ISIN corroborates HQ address match
4. **NO_MATCH** (0%) — No reliable match found

HQ-only address matches without ISIN confirmation are reported as NO_MATCH per requirements.

## Tests

```bash
# Unit tests
pytest tests/ -m "not integration" -v

# Integration tests (hits real GLEIF API)
pytest tests/test_integration.py -v
```

## Input Format

Excel with columns: Name, ISIN, Street, Town, Country (Czech), ZIP code

## Output Format

Input columns + LEI, LEI_status, Match_type, Confidence, GLEIF_legal_name, GLEIF_legal_address, GLEIF_hq_address, Notes (Czech)

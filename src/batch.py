"""Batch XLSX processing for LEI lookups."""

import logging
from pathlib import Path

import pandas as pd

from .models import InputEntity

logger = logging.getLogger(__name__)


def read_input_xlsx(path: str | Path) -> list[InputEntity]:
    """Read input Excel file and return list of InputEntity objects.

    Reads columns: Name, ISIN, Street, Town, Country, ZIP code.
    Skips columns G, H, I (dohledatelny?, expected LEI, notes) per requirements.
    """
    df = pd.read_excel(path, engine="openpyxl")

    # Only use the first 6 columns (A-F)
    col_names = list(df.columns[:6])
    df = df[col_names]

    entities = []
    for _, row in df.iterrows():
        entity = InputEntity(
            name=str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else "",
            isin=str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else None,
            street=str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else None,
            town=str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else None,
            country=str(row.iloc[4]).strip() if pd.notna(row.iloc[4]) else None,
            zip_code=str(row.iloc[5]).strip() if pd.notna(row.iloc[5]) else None,
        )
        if entity.name:
            entities.append(entity)

    logger.info("Read %d entities from %s", len(entities), path)
    return entities


def write_output_xlsx(
    entities: list[InputEntity],
    results: list,  # list[LookupResult]
    output_path: str | Path,
) -> None:
    """Write enriched output Excel file."""
    rows = []
    for entity, result in zip(entities, results):
        rows.append({
            "Name": entity.name,
            "ISIN": entity.isin or "",
            "Street": entity.street or "",
            "Town": entity.town or "",
            "Country": entity.country or "",
            "ZIP code": entity.zip_code or "",
            "LEI": result.lei or "",
            "LEI_status": result.lei_status or "",
            "Match_type": result.match_type.value if result.match_type else "",
            "Confidence": round(result.confidence, 1),
            "GLEIF_legal_name": result.gleif_legal_name or "",
            "GLEIF_legal_address": result.gleif_legal_address or "",
            "GLEIF_hq_address": result.gleif_hq_address or "",
            "Notes": result.notes or "",
        })

    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False, engine="openpyxl")
    logger.info("Wrote %d results to %s", len(rows), output_path)

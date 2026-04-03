"""Tests for GLEIF client response parsing."""

import pytest
from src.gleif_client import _parse_candidate, _parse_address
from src.models import GleifAddress


class TestParseAddress:
    def test_full_address(self):
        data = {
            "country": "DE",
            "region": "Hessen",
            "city": "Frankfurt",
            "postalCode": "60327",
            "addressLines": ["EUROPA-ALLEE 12"],
        }
        addr = _parse_address(data)
        assert addr.country == "DE"
        assert addr.city == "Frankfurt"
        assert addr.postal_code == "60327"
        assert addr.address_lines == ["EUROPA-ALLEE 12"]

    def test_empty_address(self):
        addr = _parse_address({})
        assert addr.country is None
        assert addr.city is None
        assert addr.address_lines == []


class TestParseCandidate:
    def test_basic_record(self):
        record = {
            "id": "529900PY3KLUDU87D755",
            "attributes": {
                "lei": "529900PY3KLUDU87D755",
                "entity": {
                    "legalName": {"name": "CAIAC Fund Management AG"},
                    "legalAddress": {
                        "country": "LI",
                        "city": "Bendern",
                        "postalCode": "FL-9487",
                        "addressLines": ["Industriestrasse 2"],
                    },
                    "headquartersAddress": {
                        "country": "LI",
                        "city": "Bendern",
                        "postalCode": "FL-9487",
                        "addressLines": ["Industriestrasse 2"],
                    },
                    "otherNames": [
                        {"name": "CAIAC Fund Management Aktiengesellschaft"}
                    ],
                },
                "registration": {
                    "status": "ISSUED",
                },
            },
        }
        candidate = _parse_candidate(record)
        assert candidate.lei == "529900PY3KLUDU87D755"
        assert candidate.legal_name == "CAIAC Fund Management AG"
        assert candidate.status == "ISSUED"
        assert candidate.legal_address.country == "LI"
        assert candidate.legal_address.city == "Bendern"
        assert len(candidate.other_names) == 1
        assert "Aktiengesellschaft" in candidate.other_names[0]

    def test_missing_fields(self):
        record = {
            "id": "TESTLEI",
            "attributes": {
                "entity": {
                    "legalName": {"name": "Test"},
                },
                "registration": {},
            },
        }
        candidate = _parse_candidate(record)
        assert candidate.lei == "TESTLEI"
        assert candidate.legal_name == "Test"
        assert candidate.status == "UNKNOWN"

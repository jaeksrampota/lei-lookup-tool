"""Tests for address normalization and country code conversion."""

import pytest
from src.address import country_to_iso, normalize_name, normalize_address_part, extract_zip


class TestCountryToISO:
    def test_czech_names(self):
        assert country_to_iso("Česká republika") == "CZ"
        assert country_to_iso("Lichtenštejnsko") == "LI"
        assert country_to_iso("Francie") == "FR"
        assert country_to_iso("Čína") == "CN"
        assert country_to_iso("Austrálie") == "AU"

    def test_english_names(self):
        assert country_to_iso("Germany") == "DE"
        assert country_to_iso("United Kingdom") == "GB"
        assert country_to_iso("USA") == "US"
        assert country_to_iso("Hong Kong") == "HK"

    def test_iso_passthrough(self):
        assert country_to_iso("CZ") == "CZ"
        assert country_to_iso("DE") == "DE"
        assert country_to_iso("US") == "US"

    def test_none_and_empty(self):
        assert country_to_iso(None) is None
        assert country_to_iso("") is None

    def test_unknown_country(self):
        assert country_to_iso("Atlantida") is None


class TestNormalizeName:
    def test_remove_legal_forms(self):
        assert "tecam pcv" in normalize_name("TECAM PCV a.s.")
        assert "spm nemovitosti" in normalize_name("SPM NEMOVITOSTI s.r.o.")

    def test_remove_diacritics(self):
        result = normalize_name("Société Générale")
        assert "societe" in result
        assert "generale" in result

    def test_empty(self):
        assert normalize_name("") == ""

    def test_complex_fund_name(self):
        name = "Simplea Euro Bond Opportunity, otevřený podílový fond, Partners investiční společnost, a.s."
        result = normalize_name(name)
        assert "simplea" in result
        assert "euro" in result
        assert "bond" in result


class TestNormalizeAddressPart:
    def test_basic(self):
        assert normalize_address_part("EUROPA-ALLEE 12") == "europa-allee 12"

    def test_diacritics(self):
        result = normalize_address_part("Kotrčova 304/2")
        assert "kotrcova" in result

    def test_none(self):
        assert normalize_address_part(None) == ""


class TestExtractZip:
    def test_us_style(self):
        assert extract_zip("NY 10019") == "10019"
        assert extract_zip("CT 06830") == "06830"
        assert extract_zip("CA 92130") == "92130"

    def test_liechtenstein(self):
        assert extract_zip("FL-9487") == "9487"

    def test_normal(self):
        assert extract_zip("50301") == "50301"

    def test_none(self):
        assert extract_zip(None) == ""
        assert extract_zip("") == ""

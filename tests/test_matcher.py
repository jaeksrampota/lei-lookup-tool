"""Tests for fuzzy matching logic."""

import pytest
from src.matcher import name_similarity, city_similarity, street_similarity, best_name_score
from src.models import GleifCandidate, GleifAddress, InputEntity


class TestNameSimilarity:
    def test_exact_match(self):
        assert name_similarity("Polar Capital LLP", "POLAR CAPITAL LLP") > 90

    def test_legal_form_difference(self):
        # Should match well even with different legal form notation
        assert name_similarity("TECAM PCV a.s.", "TECAM PCV") > 80

    def test_completely_different(self):
        assert name_similarity("Apple Inc", "Microsoft Corp") < 50

    def test_partial_match(self):
        assert name_similarity("Interactive Brokers Hong Kong Limited",
                               "Interactive Brokers Hong Kong Limited") > 95

    def test_abbreviation(self):
        # "Lmt" vs "Limited"
        assert name_similarity(
            "BlackRock Investment Management (Australia) Lmt",
            "BlackRock Investment Management (Australia) Limited"
        ) > 80

    def test_short_name(self):
        assert name_similarity("ZVI", "ZVI") > 95


class TestCitySimilarity:
    def test_exact(self):
        assert city_similarity("London", "London") > 95

    def test_with_diacritics(self):
        assert city_similarity("Hradec Králové, Plotiště nad Labem", "HRADEC KRALOVE") > 70

    def test_prague_variants(self):
        assert city_similarity("Praha 4 - Chodov", "Praha") > 60

    def test_different_cities(self):
        assert city_similarity("London", "New York") < 40


class TestStreetSimilarity:
    def test_exact(self):
        assert street_similarity("16 Palace Street", "16 Palace Street") > 95

    def test_partial(self):
        assert street_similarity("1585 Broadway, 23rd Floor", "1585 Broadway") > 70


class TestBestNameScore:
    def test_with_other_names(self):
        entity = InputEntity(name="Test Entity Inc")
        candidate = GleifCandidate(
            lei="TEST",
            legal_name="Something Different",
            status="ISSUED",
            other_names=["Test Entity Incorporated"],
        )
        score = best_name_score(entity, candidate)
        assert score > 70

    def test_legal_name_match(self):
        entity = InputEntity(name="CAIAC Fund Management AG")
        candidate = GleifCandidate(
            lei="TEST",
            legal_name="CAIAC Fund Management AG",
            status="ISSUED",
        )
        assert best_name_score(entity, candidate) > 90

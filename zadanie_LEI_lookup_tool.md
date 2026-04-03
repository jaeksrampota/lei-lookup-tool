# Zadanie: LEI Lookup Tool

## 1. Prehled projektu (Project Overview)

**Nazev:** LEI Lookup Tool
**Kontext:** Vyuziti AI pri dohledavani chybejicich LEI identifikatoru pro potreby MIFID reportingu (Raiffeisenbank CZ, tym BIA).
**Ucel:** Nastroj pro automatizovane vyhledavani LEI (Legal Entity Identifier) na zaklade nazvu subjektu, adresy a volitelne ISIN kodu. Urcen pro interni operativni tym (back-office), ktery tydne proveruje cca 10 subjektu, z nichz u 1–2 se LEI podari dohledat.
**Prostredi:** GitHub Copilot Spaces — projekt musi byt plne funkcni v ramci tohoto prostredi (agent mode pres GitHub MCP server).

---

## 2. Problemova definice

Interni operativni tym aktualne rucne vyhledava LEI identifikatory na strance GLEIF Search (https://search.gleif.org/). Proces je:

- **Textovy:** Zadny jednoznacny identifikator k dispozici neni — subjekt je potreba hledat podle nazvu, ktery v internich systemech casto byva zapsan jinak (zkratky jako a.s., plc, Inc; preklepy; ruzne jazykove verze).
- **Vicekrokovy:** Po nalezeni kandidata je nutne overit adresu — pokud se vyrazne neshoduje (jiny stat/mesto/ulice), nejde o spravny subjekt, i kdyby se jmenoval stejne.
- **Komplikovany hranicnimi pripady:** Nekdy se adresa shoduje pouze s "headquarters" (nikoliv "legal address"), nekdy je LEI dostupne jen pro rodicovskou entitu, jindy pomaha ISIN kod.

Stahovani cele GLEIF databaze (7,5 GB XML, denne aktualizovano) do DWH je neefektivni pro cca 10 dotazu tydne.

---

## 3. Datove zdroje

### 3.1 Primarni: GLEIF API (Global LEI Foundation)

- **Endpoint:** `https://api.gleif.org/api/v1/`
- **GLEIF Search UI:** https://search.gleif.org/ (referencni rozhrani)
- **Dokumentace:** https://www.gleif.org/en/lei-data/gleif-api
- **Postman kolekce:** https://documenter.getpostman.com/view/7679680/SVYrrxuU
- **Klicove vlastnosti:**
  - Verejne, zdarma, bez API klice pro zakladni pouziti
  - Podpora fuzzy matching podle jmena a adresy
  - JSON:API format odpovedi
  - Filtry: `filter[entity.legalName]`, `filter[entity.legalAddress.country]`, `filter[entity.legalAddress.city]`
  - Fulltextove vyhledavani: `filter[fulltext]`
  - **Dulezite:** GLEIF rozlisuje `legalAddress` a `headquartersAddress` — obe je nutne porovnavat (viz sekce 5)
  - **Dulezite:** Nektere zaznamy obsahuji pole `otherNames` (alternativni nazvy entity)
  - **Dulezite:** U nekterych fondu jsou v GLEIF dostupne i ISIN kody (viz screenshot Morgan Stanley Direct Lending Fund)
- **Omezeni:** Rate limiting (nutne overit aktualni limity), neobsahuje vsechny entity (pouze ty s aktivnim/lapsed LEI)

### 3.2 Sekundarni: ARES (Pristup k registrum ekonomickych subjektu — CR)

- **URL:** https://wwwinfo.mfcr.cz/ares/ares.html.en
- **Ucel:** Vyhledani ICO ceske entity pro krizove porovnani s GLEIF
- **Format:** XML/JSON odpovedi
- **Relevance:** Pro ceske klienty umoznuje doplnkove overeni identity entity

### 3.3 Doplnkovy: Verejne zdroje k ISIN

- Vyhledavani informaci o emitentovi cenneho papiru podle ISIN kodu
- Zdroje: GLEIF databaze (nektere fondy maji ISIN primo), pripadne verejne dostupne informace o emitentech
- Ucel: Krizove overeni adresy, kdyz se prima shoda adresy nepodari

### 3.4 Volitelny: BRIS a komercni API

- BRIS (EU e-Justice Portal): https://e-justice.europa.eu/ — bez verejneho API
- HitHorizons API: https://www.hithorizons.com/services/api — placene

---

## 4. Vstupni data (format z CTS)

### 4.1 Vstupni Excel format

Na zaklade dodaneho vzoroveho souboru `LEI_dohledavani.xlsx`:

| Sloupec | Popis | Priklad |
|---|---|---|
| **Name** | Nazev subjektu z CTS systemu | "TECAM PCV a.s." |
| **ISIN** | Identifikator nastroje/cenneho papiru (volitelne) | "CZ0009010468" |
| **Street** | Ulice a cislo | "Kotrčova 304/2" |
| **Town** | Mesto | "Hradec Králové, Plotiště nad Labem" |
| **Country** | Zeme (v cestine!) | "Česká republika" |
| **ZIP code** | PSC | "50301" |

**Poznamky k datovemu formatu:**

- Sloupec Country obsahuje nazvy zemi v **cestine** (napr. "Česká republika", "Lichtenštejnsko", "Francie", "Čína") — nutna konverze na ISO 3166-1 alpha-2
- Town muze obsahovat slozene nazvy s carkou ("Hradec Králové, Plotiště nad Labem", "Liuzhou, Guangxi")
- ZIP code nemusi odpovidat standardnimu formatu ("NY 10019", "CT 06830", "FL-9487", "NSW")
- ISIN neni vzdy k dispozici (nekdy prazdny)
- Nazvy mohou obsahovat diakritiku, pravni formy v ruznych jazycich, zkratky

### 4.2 Vystupni Excel format

Obohaceny soubor — k puvodnim sloupcum (Name az ZIP code) pridat:

| Sloupec | Popis |
|---|---|
| **LEI** | Nalezeny LEI kod (20-znakovy alfanumericky identifikator), nebo prazdny |
| **LEI_status** | Status LEI v GLEIF (ISSUED / LAPSED / RETIRED / TRANSFERRED / ...) |
| **Match_type** | Typ shody — viz sekce 5.3 (FULL_MATCH / HQ_MATCH / ISIN_MATCH / ISIN_GLEIF_MATCH / NO_MATCH) |
| **Confidence** | Skore shody 0–100 % |
| **GLEIF_legal_name** | Oficialni legal name z GLEIF |
| **GLEIF_legal_address** | Legal address z GLEIF |
| **GLEIF_hq_address** | Headquarters address z GLEIF |
| **Notes** | Strojove vysvetleni rozhodnuti (viz priklady nize) |

**Priklady Notes:**

- `"Plná shoda názvu a legal address."` — pro FULL_MATCH
- `"Název odpovídá, adresa se shoduje pouze s headquarters. Legal address: Frankfurt, DE."` — pro HQ_MATCH
- `"ISIN CZ0009010468 nalezen v GLEIF, LEI přiřazen i přes neshodu adresy."` — pro ISIN_GLEIF_MATCH
- `"Dle ISIN US61774A1034 je emitentem Morgan Stanley Direct Lending Fund se sídlem 1585 Broadway, NY — shoduje se s HQ adresou v GLEIF."` — pro ISIN_MATCH
- `"LEI nalezen pouze pro rodičovskou entitu (LLC), nikoliv pro Inc."` — specificke pripady
- `"Žádný LEI nalezen v GLEIF databázi."` — pro NO_MATCH

---

## 5. Logika vyhledavani a matchingu

### 5.1 Vyhledavaci algoritmus (kroky)

```
PRO KAZDY subjekt ze vstupu:

KROK 1: Fulltext vyhledavani v GLEIF
  - Normalizovat nazev (odstranit diakritiku, pravni formy)
  - Hledat pres filter[fulltext] i filter[entity.legalName]
  - Hledat i v poli "otherNames"
  - Vysledek: seznam kandidatu (0–N)

KROK 2: Adresni verifikace KAZDEHO kandidata
  - Porovnat adresu ze vstupu s LEGAL ADDRESS kandidata
  - Porovnat adresu ze vstupu s HEADQUARTERS ADDRESS kandidata
  - Rozlisit:
    a) Shoda s legal address     → FULL_MATCH (vysoke skore)
    b) Shoda s HQ address        → HQ_MATCH (stredni skore, viz KROK 3)
    c) Zadna adresni shoda       → pokracovat na KROK 3

KROK 3: ISIN dohledavani (pokud je ISIN k dispozici a neni FULL_MATCH)
  3a) Hledat ISIN primo v GLEIF databazi
      - Nektere fondy maji ISIN prirazeny primo v GLEIF zaznamu
      - Pokud nalezen → ISIN_GLEIF_MATCH (prirazit LEI i bez adresni shody)
  3b) Hledat verejne informace o emitentovi dle ISIN
      - Pokud se adresa emitenta shoduje s HQ adresou v GLEIF → ISIN_MATCH
      - Pridat podrobne vysvetleni do Notes

KROK 4: Vyhodnoceni
  - Seradit kandidaty podle confidence score
  - Prirazit Match_type a Notes
  - Pokud zadny kandidat neprojde → NO_MATCH
```

### 5.2 Fuzzy matching — nazev

1. **Normalizace:** Odstraneni diakritiky (unidecode), prevod na lowercase
2. **Odstraneni pravnich forem:** s.r.o., a.s., plc, Ltd., LLC, Inc, GmbH, AG, S.A.S, LLP, Corp, atd.
3. **Tokenizace:** Rozdeleni na slova, ignorovani poradi
4. **Porovnani:**
   - Levenshtein distance pro kratke nazvy
   - Token Set Ratio pro slozene nazvy s ruznym poradim slov
   - Porovnani s polem `legalName` i s polem `otherNames`

### 5.3 Adresni matching — dvouvrstva logika

**DULEZITE:** GLEIF rozlisuje dve adresy (viz screenshot GLEIF):
- **Legal Address** = registracni adresa entity
- **Headquarters Address** = adresa skutecneho sidla

| Uroven shody | Popis | Match_type | Zakladni confidence |
|---|---|---|---|
| Legal address match | Stat + mesto + ulice se shoduji | FULL_MATCH | 85–100 % |
| HQ address match | Stat + mesto + ulice se shoduji pouze s HQ | HQ_MATCH | 50–70 % |
| ISIN v GLEIF | ISIN nalezen primo v GLEIF zaznamu | ISIN_GLEIF_MATCH | 75–90 % |
| ISIN + HQ match | Adresa emitenta dle ISIN odpovida HQ | ISIN_MATCH | 65–80 % |
| Zadna shoda | Ani adresa, ani ISIN nepomuze | NO_MATCH | 0 % |

**Adresni porovnani po castech:**
- Zeme: MUSI se shodovat (jinak okamzite vyrazit kandidata)
- Mesto: Fuzzy match (tolerance na diakritiku, ruzne zapisy jako "Praha 4 - Chodov" vs "Prague")
- Ulice + cislo: Fuzzy match (tolerance na zkratky, poradi)

### 5.4 Specialni pripady (z realne praxe)

Na zaklade analyzy vzoroveho souboru s 18 subjekty:

| Pripad | Priklad | Ocekavane chovani |
|---|---|---|
| Nazev odpovida, legal address odpovida | CAIAC Fund Management AG, TECAM PCV a.s. | → FULL_MATCH, prirazit LEI |
| LEI existuje jen pro rodicovskou entitu | BNP Paribas (branch Guernsey), Silvercrest (Inc vs LLC) | → NO_MATCH pro branch/Inc, pridej poznamku o rodicovskem LEI |
| Adresa odpovida jen HQ, ne legal | Avenue Therapeutics Inc | → HQ_MATCH s nizkym skore, POZOR: LEI status = LAPSED |
| HQ match + ISIN potvrzuje | Morgan Stanley Direct Lending Fund | → ISIN_MATCH, prirazit LEI s vysvetlenim |
| Adresy se neshoduji, ale ISIN nalezen v GLEIF | Birchtech Corp (realne ISIN US59833H2004) | → ISIN_GLEIF_MATCH, prirazit LEI s priznakem |
| Subjekt nema LEI vubec | SPM NEMOVITOSTI s.r.o., Golden Throat Holdings | → NO_MATCH |
| Slozeny nazev fondu s popisem | "Simplea Euro Bond Opportunity, otevřený podílový fond, Partners investiční společnost, a.s." | → Musi umět matchnout i pres velmi dlouhy nazev |

---

## 6. Technicke pozadavky

### 6.1 Jazyk a prostredi

- **Jazyk:** Python 3.10+
- **Prostredi:** GitHub Copilot Spaces (agent mode pres GitHub MCP server)
- Nastroj musi byt self-contained — zadne externi sluzby krome API volani
- API klice (pokud potreba) pres GitHub Secrets

### 6.2 Struktura projektu

```
lei-lookup-tool/
├── README.md
├── requirements.txt
├── .github/
│   └── copilot-spaces.yml          # Konfigurace pro Copilot Spaces
├── src/
│   ├── __init__.py
│   ├── main.py                     # CLI vstupni bod
│   ├── gleif_client.py             # GLEIF API klient
│   ├── ares_client.py              # ARES API klient (CZ entity)
│   ├── isin_resolver.py            # ISIN lookup logika (GLEIF + externi zdroje)
│   ├── matcher.py                  # Fuzzy matching (nazev + adresa)
│   ├── address.py                  # Adresni normalizace, CZ→ISO konverze zemi
│   ├── models.py                   # Datove modely (Pydantic)
│   ├── cache.py                    # Lokalni cache
│   └── batch.py                    # XLSX batch zpracovani
├── tests/
│   ├── test_gleif_client.py
│   ├── test_matcher.py
│   ├── test_address.py
│   ├── test_isin_resolver.py
│   └── test_integration.py         # Integracni testy proti vzoraku z LEI_dohledavani.xlsx
├── data/
│   ├── LEI_dohledavani_sample.xlsx  # Vzorovy vstupni soubor (18 zaznamu)
│   ├── country_mapping.json         # Mapovani CZ nazvu zemi → ISO kody
│   └── legal_forms.txt              # Seznam pravnich forem k odstraneni
└── docs/
    └── zadanie.md
```

### 6.3 Zavislosti (requirements.txt)

- `httpx` — Async HTTP klient (lepsi pro paralelni GLEIF dotazy)
- `rapidfuzz` — Fuzzy string matching
- `unidecode` — Odstraneni diakritiky
- `pydantic` — Validace dat a modely
- `pandas` — Cteni/zapis XLSX
- `openpyxl` — XLSX engine pro pandas
- `pytest` — Testovani
- `pytest-asyncio` — Async testy

---

## 7. Nefunkcionalni pozadavky

| Pozadavek | Specifikace |
|---|---|
| Typicky objem | ~10 subjektu/tyden (nizky objem, neni treba optimalizovat pro tisice) |
| Odezva (single lookup) | < 5 sekund (vcetne ISIN dohledavani) |
| Odezva (batch 18 zaznamu) | < 2 minuty |
| Dostupnost | Zavisla na GLEIF API |
| Jazyk vystupu | Cestina (Notes, vysvetleni) |
| Logovani | Strukturovane logy, kazdy krok rozhodnuti zaznamenan |
| Testovaci pokryti | Min. 80 % pro core logiku |
| Dokumentace | README s priklady pouziti |

---

## 8. User Stories

1. **Jako clen MIFID reporting tymu** chci nahrat XLSX soubor se seznamem subjektu (nazev, ISIN, adresa) a dostat zpet obohaceny soubor s nalezenymi LEI, typem shody a vysvetlenim, abych nemusel kazdy subjekt vyhledavat rucne na GLEIF.

2. **Jako clen MIFID reporting tymu** chci, aby nastroj toleroval preklepy, zkratky pravnich forem a ruzne zapisy nazvu (vcetne poli "other names" v GLEIF), protoze nase CTS data casto obsahuji nepresnosti.

3. **Jako clen MIFID reporting tymu** chci jasne videt, kdyz se adresa shoduje pouze s headquarters (nikoliv legal address), abych vedel, ze shoda neni 100% a muzu se rozhodnout manualne.

4. **Jako clen MIFID reporting tymu** chci, aby nastroj vyuzil ISIN kod (kdyz je k dispozici) k dohledani LEI i v pripadech, kdy se adresa primo neshoduje — napriklad u fondu, ktere maji ISIN primo v GLEIF databazi.

5. **Jako clen MIFID reporting tymu** chci videt podrobnou poznamku (Notes) u kazdeho vysledku, vysvetlujici PROC byl nebo nebyl LEI prirazen, vcetne odkazu a logiky rozhodnuti.

6. **Jako clen MIFID reporting tymu** chci, aby nastroj spravne rozpoznal, ze LEI existuje jen pro rodicovskou entitu (napr. LLC) a ne pro konkretni branch nebo variantu (napr. Inc), a dal mi o tom vedet.

---

## 9. Testovaci data a ocekavane vysledky

Z dodaneho souboru `LEI_dohledavani.xlsx` (18 zaznamu) — sloupec "dohledatelny?" urcuje ocekavany vysledek:

| # | Subjekt | Ocekavany vysledek | Poznamka |
|---|---|---|---|
| 1 | BNP Paribas Securities Services, Guernsey branch | **NO_MATCH (0)** | LEI jen pro rodice, ne pro branch |
| 2 | CAIAC Fund Management AG | **FULL_MATCH (1)** | LEI: 529900PY3KLUDU87D755 |
| 3 | TECAM PCV a.s. | **FULL_MATCH (1)** | LEI: 315700ANNRQD4SG6QE82 |
| 4 | Silvercrest Assets Management Group Inc | **NO_MATCH (0)** | LEI jen pro LLC, Inc je parent bez LEI |
| 5 | Avenue Therapeutics Inc | **NO_MATCH (0)** | Jina adresa, HQ match only, LAPSED |
| 6 | Morgan Stanley Direct Lending Fund | **ISIN_MATCH (9)** | LEI: 549300QEX22T2J8IB029, adresa jen HQ, ale ISIN potvrzuje |
| 7 | Polar Capital LLP | **FULL_MATCH (1)** | LEI: 4YW3JKTZ3K1II2GVCK15 |
| 8 | Simplea Euro Bond Opportunity... | **FULL_MATCH (1)** | LEI: 315700O17CTPSTGJHI02, slozeny nazev |
| 9 | Interactive Brokers Hong Kong Limited | **FULL_MATCH (1)** | LEI: 5493006E0OXBY133DB14 |
| 10 | Société Générale Investment Solutions (France) S.A.S | **FULL_MATCH (1)** | LEI: 969500J3OCN333WNR929 |
| 11 | ZVI | **FULL_MATCH (1)** | LEI: 3157008CUH64I23YRS77, kratky nazev |
| 12 | SPM NEMOVITOSTI s.r.o. | **NO_MATCH (0)** | Nema LEI |
| 13 | VIS, a.s. | **NO_MATCH (0)** | Nema LEI |
| 14 | Themes Management Company LLC | **NO_MATCH (0)** | Nema LEI |
| 15 | Golden Throat Holdings Group Co Ltd | **NO_MATCH (0)** | Nema LEI |
| 16 | Harrow Health Inc | **NO_MATCH (0)** | Nema LEI |
| 17 | Birchtech Corp | **ISIN_GLEIF_MATCH (9)** | LEI: 5299002L7VCQITU0A113, adresy nesedi ale ISIN nalezen |
| 18 | BlackRock Investment Management (Australia) Lmt | **FULL_MATCH (1)** | LEI: 549300ZSSQNQS45HST19, "Lmt" vs "Limited" |

**Hodnoty v "dohledatelny?":** 0 = nema LEI / nemá se priradit, 1 = standardne dohledatelny, 9 = sporny/hranicni pripad (ISIN pomohl)

---

## 10. Rizika a mitigace

| Riziko | Dopad | Mitigace |
|---|---|---|
| GLEIF API rate limiting | Zpomaleni batch zpracovani | Retry s exponencialnim backoff; pro 10 dotazu/tyden realne nehrozi |
| False positive (spatne prirazeny LEI) | Chybny MIFID reporting | Konzervativni prahy, povinne manualne potvrzeni pro HQ_MATCH a ISIN_MATCH |
| False negative (LEI existuje, ale nenalezen) | Zbytecna manualni prace | Vyhledavani v otherNames, ISIN fallback, fuzzy matching |
| Nazvy zemi v cestine | Selhani adresniho porovnani | Mapovaci tabulka CZ→ISO (country_mapping.json) |
| Nestandardni PSC formaty | Chybne porovnani | PSC pouzivat pouze jako doplnkovy signal, ne jako hard filter |
| ARES API nestabilita | Nedostupny sekundarni zdroj | Graceful degradation — ARES je nice-to-have |
| Rodic vs. branch entity | Spatne prirazeni rodiceskeho LEI | Explicitni kontrola, zda nalezena entita odpovida hledanemu subjektu (ne rodicovi) |

---

## 11. Faze implementace

### Faze 1: MVP (1–2 tydny)
- GLEIF API klient (fulltext + legalName vyhledavani)
- Zakladni fuzzy matching nazvu (rapidfuzz)
- Adresni verifikace (legal address + headquarters address)
- Match_type rozliseni (FULL_MATCH / HQ_MATCH / NO_MATCH)
- CLI pro single lookup
- Mapovani CZ nazvu zemi na ISO kody
- Unit testy

### Faze 2: ISIN logika + batch (2 tydny)
- ISIN vyhledavani v GLEIF (ISIN_GLEIF_MATCH)
- ISIN dohledavani emitenta z verejnych zdroju (ISIN_MATCH)
- XLSX batch zpracovani (cteni vstupniho formatu, generovani vystupniho)
- Generovani Notes v cestine
- Integracni testy proti 18 vzorkovym zaznamum

### Faze 3: Vylepseni a integrace (1–2 tydny)
- ARES krizove overeni pro ceske entity
- Pokrocily fuzzy matching (otherNames, slozene nazvy fondu)
- Detekce rodic/branch problemu
- Cache predchozich vyhledavani
- Strukturovane logovani
- GitHub Copilot Spaces konfigurace (copilot-spaces.yml)
- README s priklady pouziti

---

## 12. Kriteria prijeti (Acceptance Criteria)

- [ ] Nastroj spravne zpracuje vsech 18 zaznamu z vzoroveho souboru s vysledky odpovidajicimi sloupci "dohledatelny?"
- [ ] Pro zaznamy s dohledatelny?=1 nastroj nalezne spravne LEI
- [ ] Pro zaznamy s dohledatelny?=9 (sporné) nastroj nalezne LEI pres ISIN logiku a prida odpovidajici Match_type a Notes
- [ ] Pro zaznamy s dohledatelny?=0 nastroj spravne vrati NO_MATCH
- [ ] Vystupni XLSX obsahuje vsechny pozadovane sloupce vcetne ceskych Notes s vysvetlenim
- [ ] Fuzzy matching zvlada preklepy, zkratky pravnich forem a ruzne jazykove verze
- [ ] Nastroj rozlisuje legal address vs. headquarters address
- [ ] Vsechny testy prochazi (pytest) s pokrytim > 80 %
- [ ] Nastroj je funkcni v GitHub Copilot Spaces

---

## 13. Zdroje a reference

- GLEIF Search: https://search.gleif.org/
- GLEIF API dokumentace: https://www.gleif.org/en/lei-data/gleif-api
- GLEIF Postman kolekce: https://documenter.getpostman.com/view/7679680/SVYrrxuU
- GLEIF Concatenated Files (cela databaze): https://www.gleif.org/en/lei-data/gleif-concatenated-file/download-the-concatenated-files
- ARES: https://wwwinfo.mfcr.cz/ares/ares.html.en
- GitHub Copilot Spaces: https://docs.github.com/en/copilot/how-tos/provide-context/use-copilot-spaces
- RapidFuzz: https://github.com/maxbachmann/RapidFuzz
- pygleif: https://pypi.org/project/pygleif/

---

## Prilohy

- `LEI_dohledavani.xlsx` — vzorovy soubor s 18 zaznamy a ocekavany vysledky
- `extra_requirements.docx` — email od Anny Penickove s doplnujicimi pozadavky
- `image001.png` — screenshot GLEIF Search pro Morgan Stanley Direct Lending Fund (ukazuje legal address vs. headquarters address a ISIN)

# OCR reliability report — engine ``tesseract``

Generated: 2026-04-27T23:08:20.281171+00:00

## Headline metrics

- Samples:                **6**
- Exact match:            **83.33%**
- Within Levenshtein:     **100.00%**
- Mean char accuracy:     **95.83%**
- Roster match (correct): **100.00%**
- Mean OCR latency:       **252 ms**

## Per zone type

| Zone | Samples | Exact | Within budget | Char accuracy | Roster correct |
|---|---:|---:|---:|---:|---:|
| DNI | 2 | 100% | 100% | 100% | 100% |
| NAME | 2 | 50% | 100% | 88% | 100% |
| NIA | 2 | 100% | 100% | 100% | 100% |

## Per sample

| File | Zone | Expected | Actual | Char acc | Roster ok |
|---|---|---|---|---:|:---:|
| `nia_clean_01.png` | NIA | `1234567` | `1234567` | 100% | ✅ |
| `nia_noisy_01.png` | NIA | `9876543` | `9876543` | 100% | ✅ |
| `dni_clean_01.png` | DNI | `12345678A` | `12345678A` | 100% | ✅ |
| `dni_noisy_01.png` | DNI | `87654321Z` | `87654321Z` | 100% | ✅ |
| `name_clean_01.png` | NAME | `MARIA GARCIA` | `MARIA GARCIA` | 100% | ✅ |
| `name_noisy_01.png` | NAME | `JUAN PEREZ LOPEZ` | `JUAN PEREZ LC` | 75% | ✅ |

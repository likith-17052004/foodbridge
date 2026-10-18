# Gemini food analysis: evaluation

Model: `openrouter:google/gemini-3.6-flash`. Photos: 240 (80 dishes), errors: 0

| Metric | Result |
|---|---|
| Dish identified correctly | **75.8%** |
| Veg / non-veg correct | **99.6%** |
| Non-veg dishes flagged as non-veg (safety-critical) | **93.3%** |
| Veg dishes wrongly called non-veg | 0.0% |
| Recognised as food | 98.8% |
| Median latency | 6.89 s |

Hardest dishes: chhena_kheeri (0.0%), chicken_tikka_masala (0.0%), ledikeni (0.0%), sheer_korma (0.0%), bhatura (33.3%), daal_puri (33.3%), dharwad_pedha (33.3%), kadai_paneer (33.3%), karela_bharta (33.3%), kofta (33.3%)

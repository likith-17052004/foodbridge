"""Clean the scraped NGO Darpan CSV and tag how likely each NGO is to receive cooked food.

relevance tiers:
  A  name says it is a residential / feeding place (home, ashram, orphanage, shelter, annadana...)
     and it works in a beneficiary sector
  B  focused NGO (1-8 sectors) working with children, elderly, disabled, nutrition, HIV, poverty
  C  everything else (education-only trusts, RWAs, NGOs ticking 15+ sectors)

Usage: python3 scripts/clean_darpan.py data/raw/govt/ngodarpan_bengaluru_urban.csv
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/processed/darpan_ngos_clean.csv"

BENEFICIARY_SECTORS = {"Children", "Aged/Elderly", "Nutrition", "Differently Abled", "HIV/AIDS",
                       "Rural Development & Poverty Alleviation", "Housing"}
RESIDENTIAL_WORDS = (r"\b(?:home|homes|ashram|ashrama|orphan\w*|anath\w*|shelter|annadana|anna\s?dana|food|hunger|"
                     r"meal|roti|mane|nilaya|dhama|vatsalya|old age|vriddh\w*|sneha|hospice|rehab\w*|hostel)\b")
NOT_RECEIVER_WORDS = r"\b(?:residents?|welfare association|apartment|owners|montessori|academy|school|educational)\b"
PIN_RE = re.compile(r"\b(5[0-9]{2})\s?([0-9]{3})\b")


def main(path):
    d = pd.read_csv(path)
    d["address"] = d["address"].str.replace(r"\s+", " ", regex=True).str.strip(" ,.")
    d["name"] = d["name"].str.replace(r"\s+", " ", regex=True).str.strip(" '")

    # pincode: keep scraped value, else pull it out of the address text
    parts = d["address"].str.extract(PIN_RE)
    from_addr = parts[0] + parts[1]
    d["pincode"] = d["pincode"].dropna().astype(int).astype(str).reindex(d.index).fillna(from_addr)

    sectors = d["sectors"].fillna("").str.split(r",\s*").apply(lambda l: [s for s in l if s])
    d["n_sectors"] = sectors.str.len()
    d["beneficiary_sectors"] = sectors.apply(lambda l: ", ".join(sorted(BENEFICIARY_SECTORS & set(l))))
    has_benef = d["beneficiary_sectors"] != ""
    residential = d["name"].str.contains(RESIDENTIAL_WORDS, case=False, regex=True)
    not_receiver = d["name"].str.contains(NOT_RECEIVER_WORDS, case=False, regex=True)
    focused = d["n_sectors"].between(1, 8)

    d["relevance"] = "C"
    d.loc[focused & has_benef & ~not_receiver, "relevance"] = "B"
    d.loc[residential & (has_benef | (d["n_sectors"] == 0)) & ~not_receiver, "relevance"] = "A"

    d = d.sort_values(["relevance", "name"], key=lambda s: s.str.lower()).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT, index=False)

    print(f"{len(d)} NGOs -> {OUT}")
    print("relevance:", d["relevance"].value_counts().sort_index().to_dict())
    print("missing pincode:", d["pincode"].isna().sum())
    print("first letters of names:", "".join(sorted(set(d['name'].str[0].str.upper()))))
    for _, r in d[d["relevance"] != "C"].iterrows():
        print(f"  {r['relevance']}  {r['name'][:50]:<50} {r['beneficiary_sectors']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/raw/govt/ngodarpan_bengaluru_urban.csv")

"""Measure Gemini's food analysis on the Indian Food Images dataset (80 dishes x 50 photos, Kaggle).

Scores, per photo:
  dish_correct  Gemini's dish name or items match the class (normalised, alias list, fuzzy >= 0.8)
  diet_correct  veg / non_veg matches ground truth (classes like biryani/kofta can be either -> skipped)
  is_food       recognised as food at all
Servings and safe-hours have no ground truth in this dataset, so they are only summarised.

Results are appended to data/eval/vision_results.jsonl (resumable); a summary goes to data/eval/vision_report.md.

Usage:
  .venv/bin/python scripts/eval_vision.py --per-class 3 --rpm 30
  .venv/bin/python scripts/eval_vision.py --per-class 1 --classes biryani,butter_chicken   # quick smoke test
"""
import argparse
import difflib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402
from app.services import vision  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data/raw/food_images/Indian Food Images/Indian Food Images"
OUT = ROOT / "data/eval"

NON_VEG = {"butter_chicken", "chicken_razala", "chicken_tikka", "chicken_tikka_masala", "maach_jhol"}
EITHER = {"biryani", "kofta"}  # commonly made both ways; diet not scored

# names Indian kitchens use for the same dish (class -> accepted names)
ALIASES = {
    "daal_baati_churma": ["dal baati", "dal bati", "baati churma", "dal baati churma"],
    "daal_puri": ["dal puri", "dal poori", "daal poori"],
    "dal_tadka": ["dal fry", "tadka dal", "dal tadka", "yellow dal"],
    "dal_makhani": ["dal makhni", "maa ki dal", "black dal"],
    "chana_masala": ["chole", "chickpea curry", "chana curry", "chole masala"],
    "bhatura": ["chole bhature", "bhature"],
    "chapati": ["roti", "phulka", "chapathi"],
    "naan": ["butter naan", "garlic naan"],
    "misi_roti": ["missi roti"],
    "makki_di_roti_sarson_da_saag": ["sarson ka saag", "makki ki roti", "sarson da saag"],
    "maach_jhol": ["fish curry", "machher jhol", "macher jhol", "bengali fish curry"],
    "chicken_razala": ["chicken rezala", "rezala"],
    "butter_chicken": ["murgh makhani", "chicken makhani", "makhani chicken"],
    "chicken_tikka_masala": ["tikka masala", "chicken tikka masala"],
    "palak_paneer": ["saag paneer", "spinach paneer"],
    "kadai_paneer": ["karahi paneer", "kadhai paneer"],
    "paneer_butter_masala": ["paneer makhani", "paneer butter masala", "shahi paneer"],
    "navrattan_korma": ["navratan korma", "vegetable korma", "mixed vegetable korma"],
    "kadhi_pakoda": ["kadhi pakora", "kadhi"],
    "karela_bharta": ["bitter gourd", "karela"],
    "bhindi_masala": ["bhindi", "okra"],
    "aloo_shimla_mirch": ["aloo capsicum", "capsicum potato"],
    "aloo_tikki": ["potato patty", "aloo tikki chaat"],
    "kuzhi_paniyaram": ["paniyaram", "paddu", "kuzhi paniyaram", "appe"],
    "unni_appam": ["unniyappam", "appam"],
    "gajar_ka_halwa": ["carrot halwa", "gajar halwa"],
    "sheera": ["suji halwa", "rava kesari", "kesari bath", "sooji halwa", "kesari"],
    "rabri": ["rabdi"],
    "misti_doi": ["mishti doi", "sweet yogurt"],
    "ras_malai": ["rasmalai"],
    "rasgulla": ["rosogolla", "rasagola"],
    "cham_cham": ["chomchom", "chum chum"],
    "lyangcha": ["langcha"],
    "chhena_kheeri": ["chhena kheer", "chena kheeri"],
    "chak_hao_kheer": ["black rice kheer", "chakhao kheer"],
    "double_ka_meetha": ["shahi tukda", "bread pudding"],
    "qubani_ka_meetha": ["apricot dessert", "khubani ka meetha"],
    "sheer_korma": ["sheer khurma"],
    "doodhpak": ["doodh pak", "rice kheer", "kheer"],
    "phirni": ["firni", "kheer"],
    "gulab_jamun": ["gulab jamoon", "kala jamun"],
    "kakinada_khaja": ["khaja"],
    "kajjikaya": ["karanji", "gujiya", "kajjikayalu"],
    "poornalu": ["poornam", "purnam boorelu", "boorelu", "suiyam"],
    "pootharekulu": ["putharekulu", "paper sweet"],
    "ariselu": ["arisa", "adhirasam", "anarsa"],
    "adhirasam": ["athirasam", "ariselu", "anarsa"],
    "anarsa": ["ariselu", "adhirasam"],
    "gavvalu": ["shell sweet", "gavalu"],
    "dharwad_pedha": ["pedha", "peda", "dharwad peda"],
    "bandar_laddu": ["laddu", "ladoo", "bandar laddu"],
    "boondi": ["boondi laddu", "bundi"],
    "sohan_papdi": ["soan papdi", "son papdi"],
    "sohan_halwa": ["sohan halwa"],
    "sutar_feni": ["feni", "pheni"],
    "mysore_pak": ["mysore pak", "mysorepak"],
    "ghevar": ["ghewar"],
    "imarti": ["jangiri", "amriti", "jalebi"],
    "jalebi": ["jilebi", "imarti"],
    "malapua": ["malpua"],
    "shankarpali": ["shakarpara", "shakkar para"],
    "litti_chokha": ["litti"],
    "pithe": ["pitha", "pithe"],
    "poha": ["aval", "avalakki", "pohe"],
    "lassi": ["sweet lassi", "mango lassi"],
    "shrikhand": ["shrikhand", "amrakhand"],
    "basundi": ["basundi", "rabri"],
    "kalakand": ["milk cake", "kalakand"],
    "modak": ["modak", "kozhukattai"],
    "chikki": ["chikki", "brittle"],
    "kachori": ["kachori", "khasta kachori"],
    "kofta": ["malai kofta", "kofta curry"],
    "dum_aloo": ["dum aloo", "potato curry"],
}


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower().replace("_", " "))).strip()


def dish_correct(label, result):
    targets = [norm(label)] + [norm(a) for a in ALIASES.get(label, [])]
    said = [norm(result.get("dish_name", ""))] + [norm(i) for i in result.get("items", [])]
    for s in said:
        for t in targets:
            if t and (t in s or difflib.SequenceMatcher(None, t, s).ratio() >= 0.8):
                return True
    return False


def diet_truth(label):
    return None if label in EITHER else ("non_veg" if label in NON_VEG else "veg")


class RateLimiter:
    def __init__(self, rpm):
        self.gap, self.next, self.lock = 60.0 / rpm, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            t = max(time.time(), self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - time.time()))


def run_one(path, label, limiter):
    limiter.wait()
    t0 = time.time()
    for attempt in range(3):
        try:
            r = vision.analyse(path.read_bytes(), "image/jpeg")
            break
        except Exception as e:  # rate limit / transient: back off
            if attempt == 2:
                return {"image": str(path.relative_to(IMAGES)), "label": label, "error": f"{type(e).__name__}: {e}"[:300]}
            time.sleep(5 * (attempt + 1))
    truth = diet_truth(label)
    return {
        "image": str(path.relative_to(IMAGES)), "label": label, "latency_s": round(time.time() - t0, 2),
        "source": r.get("source"), "dish_name": r.get("dish_name"), "items": r.get("items"),
        "diet": r.get("diet"), "diet_truth": truth, "is_food": r.get("is_food"),
        "dish_correct": dish_correct(label, r), "diet_correct": None if truth is None else r.get("diet") == truth,
        "servings": r.get("estimated_servings"), "safe_hours": r.get("safe_hours_room_temp"),
        "high_risk": r.get("high_risk"), "confidence": r.get("confidence"),
    }


def report(rows):
    ok = [r for r in rows if "error" not in r]
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    by_class = {}
    for r in ok:
        by_class.setdefault(r["label"], []).append(r["dish_correct"])
    worst = sorted(((pct(v), k) for k, v in by_class.items()))[:10]
    nonveg = [r for r in ok if r["diet_truth"] == "non_veg"]
    s = {
        "photos": len(rows), "errors": len(rows) - len(ok), "model": ok[0]["source"] if ok else None,
        "dish_accuracy_pct": pct([r["dish_correct"] for r in ok]),
        "diet_accuracy_pct": pct([r["diet_correct"] for r in ok if r["diet_correct"] is not None]),
        "nonveg_caught_pct": pct([r["diet"] in ("non_veg", "egg") for r in nonveg]),
        "veg_called_nonveg_pct": pct([r["diet"] != "veg" for r in ok if r["diet_truth"] == "veg"]),
        "is_food_pct": pct([bool(r["is_food"]) for r in ok]),
        "median_latency_s": sorted(r["latency_s"] for r in ok)[len(ok) // 2] if ok else None,
        "worst_classes": worst,
    }
    md = [f"# Gemini food analysis: evaluation\n", f"Model: `{s['model']}`. Photos: {s['photos']} "
          f"({len(by_class)} dishes), errors: {s['errors']}\n",
          "| Metric | Result |", "|---|---|",
          f"| Dish identified correctly | **{s['dish_accuracy_pct']}%** |",
          f"| Veg / non-veg correct | **{s['diet_accuracy_pct']}%** |",
          f"| Non-veg dishes flagged as non-veg (safety-critical) | **{s['nonveg_caught_pct']}%** |",
          f"| Veg dishes wrongly called non-veg | {s['veg_called_nonveg_pct']}% |",
          f"| Recognised as food | {s['is_food_pct']}% |",
          f"| Median latency | {s['median_latency_s']} s |", "",
          "Hardest dishes: " + ", ".join(f"{k} ({v}%)" for v, k in worst)]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vision_report.md").write_text("\n".join(md) + "\n")
    (OUT / "vision_summary.json").write_text(json.dumps(s, indent=2))
    print("\n".join(md))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=3)
    ap.add_argument("--classes", help="comma-separated subset")
    ap.add_argument("--rpm", type=float, default=30, help="request rate cap (free tier is low)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--allow-demo", action="store_true", help="run without Gemini (plumbing test only)")
    a = ap.parse_args()
    if not vision.gemini_enabled() and not a.allow_demo:
        sys.exit("No Gemini credentials: set GEMINI_API_KEY in .env (or pass --allow-demo to test plumbing).")

    rnd = random.Random(a.seed)
    classes = a.classes.split(",") if a.classes else sorted(p.name for p in IMAGES.iterdir() if p.is_dir())
    picks = [(p, c) for c in classes for p in rnd.sample(sorted((IMAGES / c).glob("*.jpg")), a.per_class)]

    OUT.mkdir(parents=True, exist_ok=True)
    results_path = OUT / ("vision_results_demo.jsonl" if not vision.gemini_enabled() else "vision_results.jsonl")
    done = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            r = json.loads(line)
            same_model = r.get("source") == vision.model_label() or not vision.gemini_enabled()
            if "error" not in r and same_model:
                done[r["image"]] = r
    todo = [(p, c) for p, c in picks if str(p.relative_to(IMAGES)) not in done]
    print(f"{len(picks)} photos selected, {len(done)} cached, {len(todo)} to run with {vision.model_label()}")

    limiter, lock = RateLimiter(a.rpm), threading.Lock()
    with open(results_path, "a") as f, ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(lambda pc: run_one(pc[0], pc[1], limiter), todo), 1):
            with lock:
                f.write(json.dumps(r) + "\n")
                f.flush()
            done[r["image"]] = r
            mark = "ERR" if "error" in r else ("ok " if r["dish_correct"] else "-- ")
            print(f"[{i}/{len(todo)}] {mark} {r['label']:<28} -> {r.get('dish_name') or r.get('error', '')[:60]}")
    report([done[str(p.relative_to(IMAGES))] for p, _ in picks if str(p.relative_to(IMAGES)) in done])


if __name__ == "__main__":
    main()

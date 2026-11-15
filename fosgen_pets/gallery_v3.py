"""Build a portable, offline gallery of actual v2/v3 renderer output.

    python -m fosgen_pets.gallery_v3 --output /tmp/fosgen-v3-gallery [--quick]

``build_gallery(Path, quick=False)`` returns the JSON-serializable manifest also
written to metrics.json. Full mode covers three palettes and every compatible
pattern per species, six six-seed cases and six rare markings on seven species.
Quick mode renders the twelve fixed hero recipes and their exact v2 counterparts.
Both modes write a standalone Russian HTML index and real PNG contact sheets.
No Telegram, database, network, arbitrary SVG, or DNA-controlled path is involved.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from functools import lru_cache
from html import escape
from io import BytesIO
from itertools import combinations
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .core import (Appearance, V3_PALETTES as PALETTES, V3_PATTERNS as PATTERNS,
                   V3_SPECIES as SPECIES, validate_appearance)
from .portrait import render_portrait
from .visual_metrics import compare_images, image_metrics


# Fixed, reproducible selections for review, not an assertion of artist approval.
HERO_RECIPES = {
    "kitten": ("ginger", "tabby", 824031),
    "puppy": ("golden", "spotted", 456219),
    "foal": ("bay", "dapple", 681022),
    "calf": ("black", "piebald", 328512),
    "bunny": ("white", "broken", 93418),
    "fox": ("arctic", "solid", 786421),
    "badger": ("silver", "mask", 517229),
    "fawn": ("tan", "spotted", 210597),
    "raccoon": ("brown", "mask", 76304),
    "capybara": ("golden", "solid", 903117),
    "alpaca": ("cream", "piebald", 415670),
    "otter": ("chocolate", "bib", 672193),
}
SEED_CASES = (
    ("kitten", "ginger", "tabby"),
    ("kitten", "black", "tortoiseshell"),
    ("puppy", "cream", "spotted"),
    ("fox", "arctic", "solid"),
    ("badger", "grey", "mask"),
    ("foal", "grey", "dapple"),
)
SEEDS = (0, 1, 7319, 271828, 500000, 999999)
RARE_SPECIES = ("kitten", "puppy", "foal", "calf", "bunny", "fox", "alpaca")
RARE_VARIANTS = {
    "star": 7, "heart": 14, "blaze": 21, "freckle": 2,
    "sock_left": 4, "sock_right": 6,
}
SPECIES_RU = {
    "kitten": "Котенок", "puppy": "Щенок", "foal": "Жеребенок", "calf": "Теленок",
    "bunny": "Крольчонок", "fox": "Лисенок", "badger": "Барсучонок", "fawn": "Олененок",
    "raccoon": "Енотенок", "capybara": "Капибара", "alpaca": "Альпака", "otter": "Выдренок",
}
SECTION_RU = {
    "all": "Все портреты", "hero": "Подборка 12", "comparison": "v2 ↔ v3",
    "catalog": "Палитры и рисунки", "seeds": "Серии сидов", "rare": "Редкие отметины",
}


def _appearance(species: str, palette: str, pattern: str, seed: int,
                *, marking: str = "none", variant: int = 0) -> Appearance:
    result = Appearance(species, palette, pattern, "amber", marking, variant,
                        asset_version=3, pattern_seed=seed)
    validate_appearance(result)
    return result


@lru_cache(maxsize=8)
def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _sheet(output: Path, filename: str, ids: list[str], records: dict,
           *, title: str, columns: int, tile: int = 280) -> str:
    caption = 94
    header = 64
    rows = max(1, (len(ids) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * tile, header + rows * (tile + caption)), "#f4eee7")
    drawing = ImageDraw.Draw(sheet)
    drawing.text((20, 16), title, fill="#342c2a", font=_font(25))
    for index, identifier in enumerate(ids):
        record = records[identifier]
        appearance = record["appearance"]
        x, y = (index % columns) * tile, header + (index // columns) * (tile + caption)
        with Image.open(output / record["png"]) as portrait:
            image = portrait.convert("RGBA")
            background = Image.new("RGBA", image.size, "#ffffff")
            image = Image.alpha_composite(background, image).convert("RGB")
            image.thumbnail((tile - 12, tile - 12), Image.Resampling.LANCZOS)
            sheet.paste(image, (x + (tile - image.width) // 2, y + (tile - image.height) // 2))
        lines = (
            f"{appearance['species']} · v{appearance['asset_version']}",
            f"{appearance['palette']} / {appearance['pattern']}",
            f"seed {appearance['pattern_seed']:06d}",
            f"{appearance['marking']} · variant {appearance['marking_variant']}",
        )
        for line_index, line in enumerate(lines):
            drawing.text((x + 12, y + tile + line_index * 20), line,
                         fill="#342c2a", font=_font(15))
    sheet.save(output / filename, format="PNG")
    return filename


def _html(manifest: dict) -> str:
    cards = []
    for record in manifest["images"]:
        appearance = record["appearance"]
        species = appearance["species"]
        metric = record["metrics"]
        sections = " ".join(record["sections"])
        cards.append(
            f'<article data-species="{escape(species)}" data-sections="{escape(sections)}">'
            f'<a href="{record["png"]}" target="_blank" rel="noopener">'
            f'<img src="{record["thumbnail"]}" alt="{escape(species)} v{appearance["asset_version"]}" '
            'width="256" height="256" loading="lazy"></a>'
            f'<h3>{escape(SPECIES_RU.get(species, species))} <span>v{appearance["asset_version"]}</span></h3>'
            f'<p>{escape(species)} · {escape(appearance["palette"])} / {escape(appearance["pattern"])}</p>'
            f'<p>Глаза: {escape(appearance["eyes"])} · seed {appearance["pattern_seed"]:06d}</p>'
            f'<p>Отметина: {escape(appearance["marking"])} · вариант {appearance["marking_variant"]}</p>'
            f'<details><summary>Хеши и яркость</summary><code>RGB SHA256 {metric["rgb_sha256"]}</code>'
            f'<code>pHash {metric["phash"]}</code><p>{metric["width"]} × {metric["height"]} px · '
            f'контраст p95−p05: {metric["foreground_luminance_contrast"]:.1f}</p></details></article>'
        )
    buttons = "".join(f'<button type="button" data-filter="{key}" '
                      f'aria-pressed="{"true" if key == "all" else "false"}">{label}</button>'
                      for key, label in SECTION_RU.items())
    options = "".join(f'<option value="{species}">{escape(SPECIES_RU.get(species, species))}</option>'
                      for species in manifest["coverage"]["species"])
    sheets = "".join(f'<a href="{path}">{escape(SECTION_RU.get(key, key))}</a>'
                     for key, path in manifest["contact_sheets"].items())
    records = {record["id"]: record for record in manifest["images"]}
    pair_rows = []
    for pair in manifest["comparisons"]:
        first, second = records[pair["first"]], records[pair["second"]]
        genes1, genes2 = first["appearance"], second["appearance"]
        metrics = pair["metrics"]
        pair_rows.append(
            f'<tr><td>{escape(pair["kind"])}</td><td>{escape(genes1["species"])} / '
            f'{escape(genes1["pattern"])}</td>'
            f'<td><a href="{first["png"]}">v{genes1["asset_version"]} · {genes1["pattern_seed"]:06d}</a> ↔ '
            f'<a href="{second["png"]}">v{genes2["asset_version"]} · {genes2["pattern_seed"]:06d}</a></td>'
            f'<td>{metrics["ssim"]:.4f}</td><td>{metrics["foreground_ssim"]:.4f}</td>'
            f'<td>{metrics["phash_distance"]}</td><td>{metrics["changed_pixel_ratio"] * 100:.2f}%</td>'
            f'<td>{metrics["mean_absolute_rgb_difference"]:.2f}</td>'
            f'<td>{escape(metrics["diagnostic"]["review_level"])}</td></tr>'
        )
    quick = ('<p class="notice">Быстрый просмотр: 12 пар v2/v3. Полный перебор палитр, '
             'серии сидов и редкие отметины здесь пропущены.</p>' if manifest["quick"] else '')
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Фосген Питомцы — графика v3</title><style>'
        '*{box-sizing:border-box}body{margin:0;background:#f5f0e9;color:#322d2a;'
        'font:15px/1.5 system-ui,sans-serif}header,nav,.meta,.metrics{max-width:1450px;'
        'margin:auto;padding:20px}header{padding-top:38px}h1{font-size:32px;line-height:1.2;'
        'margin:0 0 14px}h2{font-size:21px}p{margin:6px 0}a{color:#684132}nav{display:flex;'
        'gap:8px;flex-wrap:wrap;position:sticky;top:0;background:#f5f0e9f5;z-index:1;'
        'border-bottom:1px solid #ded4c9}button,select{font:inherit;padding:9px 12px;'
        'border:1px solid #c9b8a7;border-radius:9px;background:#fffaf5;color:#322d2a;'
        'cursor:pointer}button[aria-pressed=true]{background:#684132;color:white}'
        'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));'
        'gap:16px;max-width:1450px;margin:auto;padding:20px}article{background:#fffaf5;'
        'border:1px solid #e5dacd;border-radius:14px;padding:12px;overflow:hidden}'
        'article[hidden]{display:none}article img{display:block;width:100%;height:auto;'
        'aspect-ratio:1;border-radius:8px}h3{margin:12px 0 4px;font-size:18px}'
        'h3 span{float:right;color:#8d6c56}article p{font-size:13px}code{display:block;'
        'overflow-wrap:anywhere;font-size:11px;margin-top:8px}details{font-size:12px;'
        'margin-top:12px}summary{cursor:pointer}.notice{padding:12px;background:#fff3cc;'
        'border-radius:8px}.sheets{display:flex;gap:14px;flex-wrap:wrap}table{width:100%;'
        'border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:7px;'
        'border-bottom:1px solid #ded4c9}.tablewrap{overflow:auto}.muted{color:#766a60}'
        '</style></head><body><header><h1>Фосген Питомцы · графика v3</h1>'
        '<p>Настоящие PNG из локального рендера, 512 × 512. Нажмите на миниатюру для полного портрета.</p>'
        '<p class="muted">Фиксированная техническая подборка воспроизводима по ДНК. '
        'Художественное одобрение этой подборки не утверждается.</p>'
        f'{quick}<p>{len(manifest["images"])} портретов · {len(manifest["comparisons"])} сравнений · '
        '<a href="metrics.json">метрики и ДНК (JSON)</a></p>'
        '<details><summary>Проверка в лаборатории /pettest</summary>'
        '<code>/pettest compare kitten 314159</code>'
        '<code>/pettest v3 series kitten 6 314159</code>'
        '<code>/pettest v3 rare fox star 314159</code>'
        '<p>Команды создают собственные воспроизводимые образцы. Полная ДНК каждого '
        'портрета этой галереи сохранена в JSON.</p></details></header>'
        f'<div class="meta"><h2>Контактные листы PNG</h2><div class="sheets">{sheets}</div></div>'
        f'<nav aria-label="Фильтры портретов">{buttons}<select id="species" aria-label="Вид">'
        f'<option value="all">Все виды</option>{options}</select></nav>'
        f'<main>{"".join(cards)}</main><section class="metrics"><h2>Диагностика пар</h2>'
        '<p>SSIM: среднее по неперекрывающимся окнам яркости 8 × 8. pHash: DCT 32 × 32, '
        '64 бита без DC. Изменение пикселя: разница любого RGB-канала больше 8.</p>'
        '<p>Порог зависит от вида и рисунка. Высокая похожесть сплошного окраса допустима. '
        'Значение review просит осмотреть пару и не является запретом выдачи питомца. '
        'Оценка переднего плана приближенная; метрики не доказывают глобальную уникальность.</p>'
        '<div class="tablewrap"><table><thead><tr><th>Пара</th><th>Вид / рисунок</th><th>Файлы</th>'
        '<th>SSIM</th><th>SSIM crop</th><th>pHash Δ</th><th>Изменено</th><th>RGB MAE</th>'
        f'<th>Сигнал</th></tr></thead><tbody>{"".join(pair_rows)}</tbody></table></div></section>'
        '<script>let section="all";const select=document.getElementById("species");'
        'function filter(){document.querySelectorAll("article").forEach(card=>{'
        'card.hidden=!(section==="all"||card.dataset.sections.split(" ").includes(section))'
        '||!(select.value==="all"||card.dataset.species===select.value);});}'
        'document.querySelectorAll("button[data-filter]").forEach(button=>'
        'button.addEventListener("click",()=>{section=button.dataset.filter;'
        'document.querySelectorAll("button[data-filter]").forEach(other=>'
        'other.setAttribute("aria-pressed",String(other===button)));filter();}));'
        'select.addEventListener("change",filter);</script></body></html>'
    )


def build_gallery(output: Path, *, quick: bool = False) -> dict:
    """Write actual portrait PNGs/HTML/sheets/metrics and return their manifest."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "images").mkdir(exist_ok=True)
    (output / "thumbs").mkdir(exist_ok=True)
    records: dict[str, dict] = {}
    pngs: dict[str, bytes] = {}
    comparisons: list[dict] = []
    hero_ids, version_ids, seed_ids, rare_ids = [], [], [], []
    seed_coverage = []

    def add(appearance: Appearance, section: str) -> str:
        validate_appearance(appearance)
        identifier = f"v{appearance.asset_version}-{appearance.fingerprint()}"
        if identifier not in records:
            portrait = render_portrait(appearance)
            metrics = image_metrics(portrait.png)
            if (metrics["width"], metrics["height"]) != (512, 512):
                raise ValueError("Gallery portraits must be actual 512x512 renderer output")
            path = f"images/{identifier}.png"
            thumbnail = f"thumbs/{identifier}.png"
            (output / path).write_bytes(portrait.png)
            with Image.open(BytesIO(portrait.png)) as image:
                image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                image.save(output / thumbnail, format="PNG")
            records[identifier] = {
                "id": identifier, "png": path, "thumbnail": thumbnail,
                "appearance": asdict(appearance), "dna": appearance.canonical_dna(),
                "renderer_version": portrait.renderer_version,
                "renderer_pixel_hash": portrait.pixel_hash,
                "metrics": metrics, "sections": [],
            }
            pngs[identifier] = portrait.png
        if section not in records[identifier]["sections"]:
            records[identifier]["sections"].append(section)
        return identifier

    def pair(first: str, second: str, kind: str) -> None:
        appearance = records[first]["appearance"]
        comparisons.append({
            "kind": kind, "first": first, "second": second,
            "metrics": compare_images(pngs[first], pngs[second],
                                      species=appearance["species"], pattern=appearance["pattern"]),
        })

    for species in SPECIES:
        palette, pattern, seed = HERO_RECIPES[species]
        appearance = _appearance(species, palette, pattern, seed)
        current = add(appearance, "hero")
        add(appearance, "comparison")
        previous = add(replace(appearance, asset_version=2), "comparison")
        hero_ids.append(current)
        version_ids.extend((previous, current))
        pair(previous, current, "v2-v3")

    if not quick:
        for species in SPECIES:
            for palette in PALETTES[species][:3]:
                for pattern in PATTERNS[species]:
                    add(_appearance(species, palette, pattern, 314159), "catalog")
        for species, palette, pattern in SEED_CASES:
            if species not in SPECIES or pattern not in PATTERNS[species]:
                continue
            series = [add(_appearance(species, palette, pattern, seed), "seeds") for seed in SEEDS]
            seed_ids.extend(series)
            seed_coverage.append({"species": species, "palette": palette,
                                  "pattern": pattern, "seeds": list(SEEDS)})
            for first, second in combinations(series, 2):
                pair(first, second, "seed")
        for species in RARE_SPECIES:
            if species not in SPECIES:
                continue
            base = _appearance(species, PALETTES[species][0], PATTERNS[species][0], 314159)
            baseline = add(base, "catalog")
            for marking, variant in RARE_VARIANTS.items():
                current = add(replace(base, marking=marking, marking_variant=variant), "rare")
                rare_ids.append(current)
                pair(baseline, current, "marking")

    sheets = {
        "hero": _sheet(output, "hero-12.png", hero_ids, records,
                       title="Фосген Питомцы v3 · фиксированная подборка", columns=4),
        "comparison": _sheet(output, "comparison-v2-v3.png", version_ids, records,
                             title="Одинаковые гены · слева v2, справа v3", columns=4),
    }
    if seed_ids:
        sheets["seeds"] = _sheet(output, "seed-series.png", seed_ids, records,
                                 title="Серии Pattern Seed · по шесть сидов", columns=6, tile=240)
    if rare_ids:
        sheets["rare"] = _sheet(output, "rare-markings.png", rare_ids, records,
                                title="Редкие отметины · одинаковая базовая ДНК", columns=6, tile=240)
    current = [record["appearance"] for record in records.values()
               if record["appearance"]["asset_version"] == 3]
    manifest = {
        "schema_version": 1, "asset_version": 3, "quick": quick,
        "hero_selection": "Fixed deterministic recipes for review; artist approval is not claimed",
        "metrics_scope": "Actual rendered PNG pairs only; no global uniqueness or art-quality proof",
        "images": list(records.values()), "comparisons": comparisons,
        "contact_sheets": sheets,
        "coverage": {
            "complete": not quick,
            "species": list(SPECIES),
            "palettes_per_species": {species: sorted({item["palette"] for item in current
                                                     if item["species"] == species}) for species in SPECIES},
            "patterns_per_species": {species: sorted({item["pattern"] for item in current
                                                     if item["species"] == species}) for species in SPECIES},
            "seed_cases": seed_coverage,
            "rare_markings": [] if quick else list(RARE_VARIANTS),
            "rare_species": [] if quick else [species for species in RARE_SPECIES if species in SPECIES],
            "omitted_in_quick_mode": ["palette-pattern catalog", "six-seed series", "rare markings"] if quick else [],
        },
    }
    (output / "metrics.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                                  allow_nan=False) + "\n", encoding="utf-8")
    (output / "index.html").write_text(_html(manifest), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    manifest = build_gallery(args.output, quick=args.quick)
    print(f"Created {len(manifest['images'])} actual portraits, "
          f"{len(manifest['comparisons'])} comparisons: {args.output / 'index.html'}")


if __name__ == "__main__":
    main()

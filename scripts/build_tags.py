"""Шаг 2: теги компаний из profiles.csv -> company_tags.csv (длинный формат: компания, тег).

Теги вида `пространство:значение`. Пространства factor/sector/country/region общие с новостями:
классификатор новостей ставит такие же теги, и совпадение определяется равенством строк.
Остальные (size, stage, product, client, workers, group) описывают компанию и нужны реранкеру как модификаторы.

Запуск (из корня проекта, после build_profiles.py):
    python scripts/build_tags.py --profiles data/processed/profiles.csv --out-dir data/processed
Словарь тегов и правила: docs/tags.md.
"""
import argparse
from pathlib import Path

import pandas as pd

# Крупные группы ОКВЭД (разделы A-S общероссийского классификатора), ключ - диапазон двузначных кодов.
SECTORS = [
    (1, 3, "agro"), (5, 9, "mining"), (10, 33, "manufacturing"), (35, 35, "energy"),
    (36, 39, "utilities"), (41, 43, "construction"), (45, 47, "trade"), (49, 53, "transport"),
    (55, 56, "horeca"), (58, 63, "it_telecom"), (64, 66, "finance"), (68, 68, "real_estate"),
    (69, 75, "prof_services"), (77, 82, "admin_services"), (84, 84, "public"), (85, 85, "education"),
    (86, 88, "health"), (90, 93, "culture_sport"), (94, 96, "other_services"),
]
# Отрасли, у которых цена сырья и металлов - прямой фактор.
COMMODITY_SECTORS = {"agro", "mining", "energy"}
COMMODITY_DIVISIONS = {10, 11, 19, 20, 23, 24, 25}  # пищевая, нефтепереработка, химия, неметаллы, металлургия
CONSUMER_SECTORS = {"trade", "horeca", "culture_sport", "other_services"}
HIGH_WORKERS = {"101-250", "Более 251"}

# Продукты банка: колонка профиля -> тег product:*
PRODUCTS = {
    "cl_product_rko_mark": "rko", "cl_product_gar_mark": "guarantee", "cl_product_loa_mark": "credit",
    "cl_product_lea_mark": "leasing", "cl_product_dep_mark": "deposit", "cl_product_zpp_mark": "payroll",
    "factoring_mark": "factoring",
}
# Продукты, чувствительные к ключевой ставке: кредит, лизинг, факторинг (стоимость долга) и депозит (доходность).
RATE_PRODUCTS = ["cl_product_loa_mark", "cl_product_lea_mark", "factoring_mark", "cl_product_dep_mark"]

SIZE_MID = 1e9


def sector_of(division: pd.Series) -> pd.Series:
    num = pd.to_numeric(division, errors="coerce")
    sector = pd.Series(pd.NA, index=division.index, dtype="object")
    for lo, hi, name in SECTORS:
        sector[(num >= lo) & (num <= hi)] = name
    return sector


def build_tags(p: pd.DataFrame) -> pd.DataFrame:
    parts = []

    def add(mask_or_values, tag):
        """Тег ставится строкам, где маска истинна; tag - строка или Series с тегом на каждую компанию."""
        if isinstance(tag, str):
            sel = p.loc[mask_or_values, "synthetic_u"]
            parts.append(pd.DataFrame({"synthetic_u": sel, "tag": tag}))
        else:
            sel = pd.DataFrame({"synthetic_u": p["synthetic_u"], "tag": tag})[mask_or_values]
            parts.append(sel)

    # --- отрасль ---
    division = p["okved_division"].astype(str).str.zfill(2)
    sector = sector_of(division)
    has_sector = sector.notna()
    add(has_sector, "sector:" + sector.fillna(""))
    add(division.notna() & (division != "nan"), "okved:" + division)

    # --- факторы влияния ---
    add(p[RATE_PRODUCTS].eq(1).any(axis=1), "factor:key_rate")
    add(p["fts_flag"].eq(1), "factor:fx")
    add(p["amount_workers_fns_gradation"].isin(HIGH_WORKERS), "factor:labor")
    add(sector.isin(COMMODITY_SECTORS) | pd.to_numeric(division, errors="coerce").isin(COMMODITY_DIVISIONS),
        "factor:commodities")
    add(sector.isin(CONSUMER_SECTORS), "factor:consumer_demand")
    add(sector.isin({"construction", "real_estate"}), "factor:construction_market")

    # --- география: страны ВЭД и регион ---
    countries = p["fts_countries"].fillna("").str.split("|").explode()
    countries = countries[countries != ""]
    parts.append(pd.DataFrame({"synthetic_u": p.loc[countries.index, "synthetic_u"].values,
                               "tag": "country:" + countries.values}))
    add(p["region"].notna(), "region:" + p["region"].fillna(""))

    # --- описательные теги ---
    for col, name in PRODUCTS.items():
        add(p[col].eq(1), f"product:{name}")
    add(p["is_large"].eq(1), "size:large")
    mid = p["revenue_company"].between(SIZE_MID, 15e9, inclusive="left")
    add(mid, "size:mid")
    add(p["revenue_company"].lt(SIZE_MID), "size:small")
    add(p["revenue_company"].isna(), "size:revenue_unknown")
    add(p["amount_workers_fns_gradation"].notna(), "workers:" + p["amount_workers_fns_gradation"].fillna(""))
    add(p["eco_mark_label"].notna(), "stage:" + p["eco_mark_label"].fillna(""))
    add(p["company_age_years_gradation"].notna(), "age:" + p["company_age_years_gradation"].fillna(""))
    add(p["cl_type"].notna(), "client:" + p["cl_type"].fillna("").str.lower())
    add(p["group_size"].ge(10), "group:large")

    tags = pd.concat(parts, ignore_index=True).drop_duplicates()
    tags["namespace"] = tags["tag"].str.split(":", n=1).str[0]
    return tags.sort_values(["synthetic_u", "tag"], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--profiles", default=Path("data/processed/profiles.csv"), type=Path)
    parser.add_argument("--out-dir", default=Path("data/processed"), type=Path)
    args = parser.parse_args()

    profiles = pd.read_csv(args.profiles, dtype={"okved_division": str, "mainokved_code": str})
    tags = build_tags(profiles)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "company_tags.csv"
    tags.to_csv(out, index=False, encoding="utf-8")

    per_company = tags.groupby("synthetic_u").size()
    print(f"Компаний: {len(profiles)}, с тегами: {len(per_company)}, пар (компания, тег): {len(tags)}")
    print(f"Тегов на компанию: медиана {per_company.median():.0f}, максимум {per_company.max()}")
    print("Пары по пространствам:", tags["namespace"].value_counts().to_dict())
    print("Факторы:", tags[tags["namespace"] == "factor"]["tag"].value_counts().to_dict())
    print(f"Записано: {out}")


if __name__ == "__main__":
    main()

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REVENUE_PLACEHOLDER = 1000.0
LARGE_REVENUE = 15e9
DROP_COLUMNS = ["org_type", "source_row"]
FLAG_COLUMNS = [
    "negativemark", "cl_product_rko_mark", "cl_product_gar_mark", "cl_product_loa_mark",
    "cl_product_lea_mark", "cl_product_dep_mark", "cl_product_zpp_mark", "fts_flag", "factoring_mark",
]


def load_companies() -> pd.DataFrame:
    with psycopg.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=10) as conn:
        cur = conn.execute("SELECT * FROM insights.companies")
        columns = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def clean_companies(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(columns=DROP_COLUMNS).copy()
    df = df.replace({None: np.nan})

    df["revenue_company"] = pd.to_numeric(df["revenue_company"])
    df.loc[df["revenue_company"] == REVENUE_PLACEHOLDER, "revenue_company"] = np.nan

    for col in FLAG_COLUMNS:
        df[col] = df[col].fillna(False).astype(int)

    # раздел ОКВЭД (первые 2 цифры)
    df["okved_division"] = df["mainokved_code"].str.slice(0, 2)

    # «КИТАЙ|ТУРЦИЯ»
    df["fts_countries"] = df["fts_country_name"].fillna("").str.split(r"\s*,\s*").map(
        lambda xs: "|".join(x for x in xs if x)
    )
    return df.drop(columns=["fts_country_name"])


def add_group_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("group_id")["revenue_company"]
    df["group_size"] = g.transform("size")
    df["group_revenue_sum"] = g.transform("sum")
    df["is_large"] = (df["revenue_company"] >= LARGE_REVENUE).astype(int)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out-dir", default=Path("data/processed"), type=Path)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")  # уже заданные переменные окружения не перезаписываются
    raw = load_companies()
    profiles = add_group_features(clean_companies(raw))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "profiles.csv"
    profiles.to_csv(out, index=False, encoding="utf-8")

    print(f"Компаний в БД: {len(raw)}, профилей: {len(profiles)}")
    print(f"Крупных (is_large): {profiles['is_large'].sum()}")
    print(f"Выручка неизвестна: {profiles['revenue_company'].isna().sum()}")
    print(f"Записано: {out}")


if __name__ == "__main__":
    main()

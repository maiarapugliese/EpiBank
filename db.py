from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from pathlib import Path
import pandas as pd

# base = pasta onde está o arquivo db.py (e o app)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = (BASE_DIR / "epibank.sqlite").resolve()

def get_engine() -> Engine:
    # usar caminho POSIX para evitar barras invertidas problemáticas
    url = f"sqlite:///{DB_PATH.as_posix()}"
    engine = create_engine(url, future=True)
    return engine

# Definição da tabela
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id TEXT,
    patient_id TEXT,
    species TEXT,
    sex TEXT,
    breed TEXT,
    age_group TEXT,
    fertility TEXT,
    origin TEXT,
    analysis_date TEXT,
    sample_type TEXT,
    method TEXT,
    result TEXT,
    findings TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_species ON records(species);
CREATE INDEX IF NOT EXISTS idx_records_result ON records(result);
CREATE INDEX IF NOT EXISTS idx_records_dates ON records(analysis_date);
"""

def init_db():
    eng = get_engine()
    raw = eng.raw_connection()
    try:
        raw.executescript(SCHEMA_SQL)
        raw.commit()
    finally:
        raw.close()

def insert_record(eng: Engine, data: dict):
    keys = ",".join(data.keys())
    params = ":" + ", :".join(data.keys())
    sql = text(f"INSERT INTO records ({keys}) VALUES ({params})")
    with eng.begin() as conn:
        conn.execute(sql, data)

def update_record(eng: Engine, rec_id: int, data: dict):
    sets = ", ".join([f"{k} = :{k}" for k in data.keys()])
    data["id"] = rec_id
    sql = text(f"UPDATE records SET {sets}, updated_at = datetime('now') WHERE id = :id")
    with eng.begin() as conn:
        conn.execute(sql, data)

def delete_record(eng: Engine, rec_id: int):
    sql = text("DELETE FROM records WHERE id = :id")
    with eng.begin() as conn:
        conn.execute(sql, {"id": rec_id})

# ---------- Função nova: agrupar registros por paciente ----------
def get_patients_summary(eng: Engine, where_sql: str = "", params: dict = None) -> pd.DataFrame:
    """
    Retorna um DataFrame com 1 linha por patient_id,
    mostrando espécie, sexo, raça e todos os exames concatenados.
    """
    if params is None:
        params = {}
    with eng.begin() as conn:
        # aumentar limite do group_concat
        conn.execute(text("PRAGMA group_concat_max_len = 1000000;"))
        query = text(f"""
            SELECT
                patient_id,
                MAX(species) AS species,
                MAX(sex)     AS sex,
                MAX(breed)   AS breed,
                COUNT(*)     AS n_exames,
                GROUP_CONCAT(
                    COALESCE(analysis_date,'') || ' | ' ||
                    COALESCE(sample_type,'')   || ' | ' ||
                    COALESCE(method,'')        || ': ' ||
                    COALESCE(result,''),
                    '; '
                ) AS exames_resumidos
            FROM records
            WHERE 1=1
            {where_sql}
            GROUP BY patient_id
            ORDER BY patient_id
        """)
        rows = conn.execute(query, params).fetchall()
    return pd.DataFrame(rows, columns=["patient_id","species","sex","breed","n_exames","exames_resumidos"])

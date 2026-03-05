import streamlit as st
import pandas as pd
from sqlalchemy import text
import re
import numpy as np
import matplotlib.pyplot as plt

try:
    from scipy.stats import chi2_contingency
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False

from db import get_engine, init_db, insert_record, update_record, delete_record


st.set_page_config(
    page_title="EpiBank - HUMV/UFRB",
    page_icon="🧬",
    layout="wide"
)


# -------------------------------------------------------
# ESTILO
# -------------------------------------------------------

st.markdown("""
<style>

/* fundo */

.stApp {
    background-color: #eaf7ee;
}

/* fonte */

html, body, [class*="css"] {
    font-family: 'Segoe UI', sans-serif;
}

/* títulos */

h1, h2, h3 {
    color: #0b6b3a;
}

/* texto geral */

p, li, div {
    color: #0b6b3a;
}

/* nomes dos campos */

label {
    color: #0b6b3a !important;
    font-weight: 600;
}

/* campos texto */

.stTextInput input {
    background-color: #ffffff !important;
}

/* texto digitado nos campos */

.stTextInput input {
    color: #0b6b3a !important;
}

.stTextArea textarea {
    color: #0b6b3a !important;
}

.stNumberInput input {
    color: #0b6b3a !important;
}

.stSelectbox div[data-baseweb="select"] {
    color: #0b6b3a !important;
}

.stDateInput input {
    color: #0b6b3a !important;
}

/* textarea */

.stTextArea textarea {
    background-color: #ffffff !important;
}

/* número */

.stNumberInput input {
    background-color: #ffffff !important;
}

/* selectbox */

.stSelectbox div[data-baseweb="select"] > div {
    background-color: #ffffff !important;
}

/* data */

.stDateInput input {
    background-color: #ffffff !important;
}

/* upload */

.stFileUploader div {
    background-color: #ffffff !important;
}

/* abas */

.stTabs [data-baseweb="tab"] {
    color: #0b6b3a !important;
    font-size: 16px;
    font-weight: 600;
}

.stTabs [data-baseweb="tab"]:hover {
    color: #084f2b !important;
}

/* menu suspenso do selectbox */

div[role="listbox"] {
    background-color: #ffffff !important;
}

/* opções do menu */

div[role="option"] {
    background-color: #ffffff !important;
    color: #0b6b3a !important;
}

/* opção ao passar o mouse */

div[role="option"]:hover {
    background-color: #eaf7ee !important;
}

</style>
""", unsafe_allow_html=True)

# -------------------------------------------------------
# TÍTULO
# -------------------------------------------------------

st.title("🧬 EpiBank")
st.caption("Banco Epidemiológico – HUMV/UFRB")


init_db()
engine = get_engine()

# -------------------------------------------------------
# FAIXAS ETÁRIAS
# -------------------------------------------------------

AGE_GROUPS = [
    "<1 ano",
    "1–5 anos",
    "5–8 anos",
    "8–10 anos",
    ">10 anos"
]

def _normalize_text(x: str) -> str:
    if x is None:
        return ""
    return str(x).strip().lower()

def make_positive_flag(df: pd.DataFrame) -> pd.Series:
    """
    Cria a coluna 'positive' (1/0) usando 'result' e 'findings'.
    Ajuste os termos se seu laboratório usa outros padrões.
    """
    pos_terms = [
        "positivo", "pos", "+", "presença", "presenca", "encontrado", "detectado",
        "giardia", "ancylostoma", "ancilostoma", "toxocara", "cystoisospora",
        "isospora", "trichuris", "dipylidium", "coccidia", "coccídeo", "coccidio",
        "helminto", "ovo", "oocisto", "larva", "cisto"
    ]
    neg_terms = ["negativo", "neg", "-", "ausência", "ausencia", "não detectado", "nao detectado", "sem achados"]

    rr = df.get("result", pd.Series([""] * len(df))).map(_normalize_text)
    ff = df.get("findings", pd.Series([""] * len(df))).map(_normalize_text)
    text_all = (rr + " " + ff).str.strip()

    # primeiro marca negativos explícitos
    neg = text_all.apply(lambda s: any(t in s for t in neg_terms))

    # marca positivos explícitos
    pos = text_all.apply(lambda s: any(t in s for t in pos_terms))

    # regra final: se tem positivo e não tem negativo explícito -> positivo
    positive = (pos & ~neg).astype(int)
    return positive

def prep_df_for_analysis(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # datas
    if "analysis_date" in df.columns:
        df["analysis_date"] = pd.to_datetime(df["analysis_date"], errors="coerce")

    # garante colunas esperadas
    for col in ["species", "sex", "age_group", "sample_type", "method", "result", "findings"]:
        if col in df.columns:
            df[col] = df[col].astype(str).replace({"nan": "", "None": ""}).str.strip()

    # positivo
    df["positive"] = make_positive_flag(df)

    # mês (tendência temporal)
    if "analysis_date" in df.columns:
        df["month"] = df["analysis_date"].dt.to_period("M").astype(str)

    # ordem de faixa etária
    if "age_group" in df.columns:
        df["age_group"] = pd.Categorical(df["age_group"], categories=AGE_GROUPS, ordered=True)

    return df

def table_profile(df: pd.DataFrame) -> dict:
    """
    Retorna tabelas de frequência (%) para variáveis principais.
    """
    out = {}
    for var in ["species", "sex", "age_group", "sample_type", "method"]:
        if var in df.columns:
            vc = df[var].replace("", np.nan).dropna().value_counts()
            pct = (vc / vc.sum() * 100).round(1)
            out[var] = pd.DataFrame({"n": vc, "%": pct})
    return out

def table_prevalence(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """
    Prevalência por categoria (n, positivos, %).
    """
    if by not in df.columns:
        return pd.DataFrame()

    d = df.copy()
    d = d[d[by].replace("", np.nan).notna()]

    g = d.groupby(by, dropna=True)["positive"].agg(
        n="count",
        positivos="sum"
    ).reset_index()

    g["prevalência_%"] = (g["positivos"] / g["n"] * 100).round(1)
    return g

def chi_square(df: pd.DataFrame, by: str):
    """
    Qui-quadrado de Pearson: positive x categoria.
    """
    if not SCIPY_OK or by not in df.columns:
        return None

    d = df.copy()
    d = d[d[by].replace("", np.nan).notna()]
    ct = pd.crosstab(d[by], d["positive"])
    # garante colunas 0 e 1
    if 0 not in ct.columns:
        ct[0] = 0
    if 1 not in ct.columns:
        ct[1] = 0
    ct = ct[[0, 1]]

    if ct.shape[0] < 2:
        return None

    chi2, p, dof, expected = chi2_contingency(ct.values)
    return {"p": float(p), "chi2": float(chi2), "dof": int(dof), "table": ct}

# -------------------------------------------------------
# ABAS
# -------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs([
    "➕ Novo registro",
    "🔎 Consulta / edição",
    "📂 Importar / exportar",
    "ℹ️ Sobre"
])


# =======================================================
# NOVO REGISTRO
# =======================================================

with tab1:

    st.header("➕ Novo registro")

    with st.form("form_novo"):

        col1, col2 = st.columns(2)

        with col1:

            sample_id = st.text_input("ID da amostra")
            patient_id = st.text_input("ID do paciente")

            species = st.selectbox(
                "Espécie",
                ["Canino","Felino", "Equídeo", "Bovino", "Caprino", "Ovino", "Outro"]
            )

            sex = st.selectbox(
                "Sexo",
                ["Macho","Fêmea","Não informado"]
            )

            breed = st.text_input("Raça")

        with col2:

            age_group = st.selectbox(
                "Faixa etária",
                AGE_GROUPS
            )

            fertility = st.selectbox(
                "Fertilidade",
                ["Inteiro","Castrado","Não informado"]
            )

            origin = st.text_input("Origem")

            analysis_date = st.date_input("Data da análise")

        sample_type = st.text_input("Material biológico")
        method = st.text_input("Método diagnóstico")
        result = st.text_input("Resultado")

        findings = st.text_area("Achados")
        notes = st.text_area("Observações")

        submit = st.form_submit_button("💾 Salvar registro")

        if submit:

            data = {
                "sample_id": sample_id,
                "patient_id": patient_id,
                "species": species,
                "sex": sex,
                "breed": breed,
                "age_group": age_group,
                "fertility": fertility,
                "origin": origin,
                "analysis_date": str(analysis_date),
                "sample_type": sample_type,
                "method": method,
                "result": result,
                "findings": findings,
                "notes": notes
            }

            insert_record(engine, data)

            st.success("Registro salvo com sucesso!")


# =======================================================
# CONSULTA
# =======================================================

with tab2:

    st.header("🔎 Consulta de registros")

    sql = """
    SELECT *
    FROM records
    ORDER BY analysis_date DESC
    """

    df = pd.read_sql(text(sql), engine)

    if df.empty:

        st.info("Nenhum registro encontrado.")

    else:

        st.dataframe(df, use_container_width=True)

        ids = df["id"].tolist()

        id_sel = st.selectbox("Selecione um registro", ids)

        row = df[df["id"] == id_sel].iloc[0]

        st.subheader("✏️ Editar registro")

        sample_id = st.text_input("ID da amostra", row["sample_id"])
        patient_id = st.text_input("ID do paciente", row["patient_id"])
        species = st.text_input("Espécie", row["species"])
        sex = st.text_input("Sexo", row["sex"])
        breed = st.text_input("Raça", row["breed"])

        age_group = st.selectbox(
            "Faixa etária",
            AGE_GROUPS,
            index=AGE_GROUPS.index(row["age_group"])
        )

        fertility = st.text_input("Fertilidade", row["fertility"])
        origin = st.text_input("Origem", row["origin"])
        analysis_date = st.text_input("Data da análise", row["analysis_date"])

        sample_type = st.text_input("Material biológico", row["sample_type"])
        method = st.text_input("Método diagnóstico", row["method"])
        result = st.text_input("Resultado", row["result"])

        findings = st.text_area("Achados", row["findings"])
        notes = st.text_area("Observações", row["notes"])

        col1, col2 = st.columns(2)

        with col1:

            if st.button("💾 Atualizar"):

                data = {
                    "sample_id": sample_id,
                    "patient_id": patient_id,
                    "species": species,
                    "sex": sex,
                    "breed": breed,
                    "age_group": age_group,
                    "fertility": fertility,
                    "origin": origin,
                    "analysis_date": analysis_date,
                    "sample_type": sample_type,
                    "method": method,
                    "result": result,
                    "findings": findings,
                    "notes": notes
                }

                update_record(id_sel, data)

                st.success("Registro atualizado!")

        with col2:

            if st.button("🗑️ Excluir"):

                delete_record(engine, id_sel)

                st.warning("Registro excluído")

        st.divider()
        st.subheader("📊 Análises automáticas")

        df_a = prep_df_for_analysis(df)

        total = len(df_a)
        pos = int(df_a["positive"].sum())
        prev = (pos / total * 100) if total else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Registros", f"{total}")
        c2.metric("Positivos", f"{pos}")
        c3.metric("Prevalência (%)", f"{prev:.1f}")

        # --------- Tendência temporal ----------
        if "month" in df_a.columns:
            st.markdown("### 📈 Série temporal (exames por mês)")
            ts = df_a.groupby("month")["id"].count().reset_index().rename(columns={"id": "n"})
            st.dataframe(ts, use_container_width=True)

            fig = plt.figure()
            plt.plot(ts["month"], ts["n"])
            plt.xticks(rotation=45, ha="right")
            plt.ylabel("n de exames")
            st.pyplot(fig)


# =======================================================
# IMPORTAR / EXPORTAR
# =======================================================

with tab3:

    st.header("📂 Exportar dados")

    sql = "SELECT * FROM records"

    df = pd.read_sql(text(sql), engine)

    st.download_button(
        "⬇️ Baixar banco (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="epibank_parasitologia.csv",
        mime="text/csv"
    )

    st.header("📥 Importar planilha")

    file = st.file_uploader("Enviar CSV", type=["csv"])

    if file:

        df_import = pd.read_csv(file)

        df_import.to_sql(
            "records",
            engine,
            if_exists="append",
            index=False
        )

        st.success("Dados importados com sucesso!")


# =======================================================
# SOBRE
# =======================================================

with tab4:

    st.header("ℹ️ Sobre")

    st.markdown("""

O **EpiBank** é um banco epidemiológico desenvolvido para registrar
resultados laboratoriais de **diagnósticos parasitológicos**.

Objetivos:

• organizar dados laboratoriais  
• facilitar análises epidemiológicas  
• apoiar vigilância em saúde  

Sistema desenvolvido por **MV. Ma. Maiara Duarte Pugliese** sob orientação da **Profa. Dra. Lorendane Millena de Carvalho**, durante a Residência Multiprofissional em Saúde Animal Integrada à Saúde Pública, no Hospital Universitário de Medicina Veterinária da Universidade Federal do Recôncavo da Bahia.


Disponível em: https://github.com/maiarapugliese/EpiBank.git
""")

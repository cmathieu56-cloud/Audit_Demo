import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time
from io import BytesIO

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

st.set_page_config(page_title="Audit V21 - Logique Universelle", page_icon="🏗️", layout="wide")

st.markdown("""
<style>
    div[data-testid="stDataFrame"] { font-size: 110% !important; }
    div[data-testid="stMetricValue"] { font-size: 2.5rem !important; font-weight: bold; }
    .stAlert { font-weight: bold; border: 2px solid #ff4b4b; }
</style>
""", unsafe_allow_html=True)

try:
    supabase = create_client(URL_SUPABASE, CLE_ANON)
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Erreur connexion : {e}")

# ==============================================================================
# 2. LOGIQUE MÉTIER
# ==============================================================================

def clean_float(val):
    if isinstance(val, (float, int)): return float(val)
    if not isinstance(val, str): return 0.0
    val = val.replace(' ', '').replace('€', '').replace('EUR', '')
    val = val.replace(',', '.')
    try: return float(val)
    except: return 0.0

def detecter_famille(label, ref=""):
    label_up, ref_up = str(label).upper(), str(ref).upper()
    if any(x in label_up for x in ["ENERG", "TAXE", "DEEE", "ECO-PART"]): return "TAXE"
    if "FRAIS_ANNEXE" in ref_up or label_up.strip() == "FF" or any(x in label_up for x in ["FRAIS FACT", "GESTION"]):
        return "FRAIS GESTION"
    if any(x in label_up for x in ["PORT", "LIVRAISON", "TRANSPORT"]): return "FRAIS PORT"
    return "PRODUIT"

def traiter_un_fichier(nom_fichier, user_id):
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("models/gemini-3-flash-preview")
        res = model.generate_content(["Analyse cette facture en JSON", {"mime_type": "application/pdf", "data": file_data}])
        match = re.search(r"(\{.*\})", res.text, re.DOTALL)
        if match:
            data_json = json.loads(match.group(1))
            supabase.table("audit_results").upsert({
                "file_name": nom_fichier, "user_id": user_id,
                "analyse_complete": json.dumps(data_json), "raw_text": res.text
            }).execute()
            return True, "OK"
    except Exception as e: return False, str(e)

def afficher_rapport_sql(fournisseur_nom):
    res = supabase.table("vue_litiges_articles").select("*").eq("fournisseur", fournisseur_nom).execute()
    if not res.data:
        st.info(f"✅ Aucun litige détecté par SQL pour {fournisseur_nom}.")
        return
    df_litiges = pd.DataFrame(res.data)
    st.subheader(f"🎸 Rapport de Litige SQL - {fournisseur_nom}")
    for article, group in df_litiges.groupby('ref'):
        with st.expander(f"📦 {article} - {group['designation'].iloc[0]} (Perte : {group['perte_ligne'].sum():.2f} €)", expanded=True):
            st.table(group[['qte', 'num_facture', 'paye_u', 'cible_u', 'perte_ligne']])

# ==============================================================================
# 3. INTERFACE PRINCIPALE
# ==============================================================================
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    user_id = session["user"]["id"]
    st.title("🏗️ Audit V21 - Logique Universelle")

    res_db = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
    memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    memoire_full = {r['file_name']: r for r in res_db.data}
    
    all_rows = []
    for f_name, json_str in memoire.items():
        data = json.loads(json_str)
        fourn = data.get('fournisseur', 'INCONNU').upper()
        for l in data.get('lignes', []):
            qte = clean_float(l.get('quantite', 1))
            montant = clean_float(l.get('montant', 0))
            all_rows.append({
                "Fournisseur": fourn, "Montant": montant, "Quantité": qte,
                "Article": l.get('article', 'SANS_REF'), "Désignation": l.get('designation', ''),
                "Famille": detecter_famille(l.get('designation', ''), l.get('article', '')),
                "Facture": data.get('num_facture', '-'), "Date": data.get('date', '-')
            })

    df = pd.DataFrame(all_rows)
    # RESTAURATION DES 4 ONGLETS ICI
    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIG", "📊 ANALYSE", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_config:
        st.header("🛠️ Réglages Fournisseurs")
        res_cfg = supabase.table("user_configs").select("fournisseur, franco, max_gestion").eq("user_id", user_id).execute()
        st.data_editor(pd.DataFrame(res_cfg.data) if res_cfg.data else pd.DataFrame(), use_container_width=True)

    with tab_analyse:
        if not all_rows: st.info("Importez des données.")
        else:
            stats_fourn = df.groupby('Fournisseur')['Montant'].sum().reset_index()
            sel = st.dataframe(stats_fourn, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel.selection.rows:
                f_sel = stats_fourn.iloc[sel.selection.rows[0]]['Fournisseur']
                st.markdown("---")
                afficher_rapport_sql(f_sel)

    with tab_import:
        uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
        if uploaded and st.button("🚀 LANCER"):
            for f in uploaded:
                with st.status(f"Analyse {f.name}..."):
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    traiter_un_fichier(f.name, user_id)
            st.rerun()

    with tab_brut:
        st.header("🔍 Scan total des documents")
        if memoire_full:
            choix_file = st.selectbox("Choisir un fichier :", list(memoire_full.keys()))
            if choix_file:
                st.text_area("Résultat Gemini (Full Scan)", memoire_full[choix_file].get('raw_text', ''), height=400)

st.write(f"Heure : {time.strftime('%H:%M:%S')}")

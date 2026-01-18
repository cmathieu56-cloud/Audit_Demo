import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time

# ==============================================================================
# 1. CONFIGURATION & CONNEXIONS
# ==============================================================================
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

st.set_page_config(page_title="Audit V18 - Prod", page_icon="🏗️", layout="wide")

try:
    supabase = create_client(URL_SUPABASE, CLE_ANON)
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Erreur connexion : {e}")

# ==============================================================================
# 2. FONCTIONS
# ==============================================================================

def clean_float(val):
    if isinstance(val, (float, int)): return float(val)
    if not isinstance(val, str): return 0.0
    val = val.replace(' ', '').replace('€', '').replace('EUR', '')
    val = val.replace(',', '.')
    try: return float(val)
    except: return 0.0

def detecter_famille(label, ref=""):
    label_up = str(label).upper()
    if any(x in label_up for x in ["PORT", "LIVRAISON", "TRANSPORT"]): return "FRAIS PORT"
    if any(x in label_up for x in ["GESTION", "ADMIN"]): return "FRAIS GESTION"
    return "PRODUIT"

def extraire_json_robuste(texte):
    try:
        match = re.search(r"(\{.*\})", texte, re.DOTALL)
        if match: return json.loads(match.group(1))
    except: pass
    return None

def traiter_un_fichier(nom_fichier):
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = "Analyse cette facture et donne le JSON : fournisseur, date, num_facture, lignes (quantite, article, designation, prix_net, montant)."
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        data_json = extraire_json_robuste(res.text)
        if data_json:
            supabase.table("audit_results").upsert({
                "file_name": nom_fichier,
                "analyse_complete": json.dumps(data_json),
                "raw_text": res.text 
            }).execute()
        return True, "OK"
    except Exception as e: return False, str(e)

# ==============================================================================
# 3. INTERFACE
# ==============================================================================
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    st.title("🏗️ Audit V18 - Prod")

    try:
        res_db = supabase.table("audit_results").select("*").execute()
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except: 
        memoire, memoire_full = {}, {}

    all_rows = []
    fournisseurs_detectes = set()

    for f_name, json_str in memoire.items():
        try:
            data = json.loads(json_str)
            fourn = data.get('fournisseur', 'INCONNU').upper()
            fournisseurs_detectes.add(fourn)
            for l in data.get('lignes', []):
                qte = clean_float(l.get('quantite', 1))
                montant = clean_float(l.get('montant', 0))
                all_rows.append({
                    "Fichier": f_name, "Fournisseur": fourn, "Quantité": qte,
                    "Article": l.get('article', 'SANS_REF'), "Désignation": l.get('designation', ''),
                    "PU_Systeme": montant/qte if (montant > 0 and qte > 0) else 0,
                    "Famille": detecter_famille(l.get('designation', ''))
                })
        except: continue

    df = pd.DataFrame(all_rows)
    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIG", "📊 ANALYSE", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_config:
        st.header("🛠️ Règles")
        # FIX : On force la création des colonnes même si c'est vide
        if 'config_df' not in st.session_state:
            st.session_state['config_df'] = pd.DataFrame(columns=["Fournisseur", "Franco (Seuil €)", "Max Gestion (€)"])
        
        edited_config = st.data_editor(st.session_state['config_df'], num_rows="dynamic", use_container_width=True)
        st.session_state['config_df'] = edited_config
        
        # FIX : On vérifie que la table n'est pas vide avant de l'utiliser
        config_dict = {}
        if not edited_config.empty and "Fournisseur" in edited_config.columns:
            config_dict = edited_config.set_index('Fournisseur').to_dict('index')

    with tab_analyse:
        if df.empty: st.info("Charge des factures dans l'onglet IMPORT.")
        else: st.dataframe(df, use_container_width=True)

    with tab_import:
        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button("🗑️ RAZ BASE", type="primary"):
                supabase.table("audit_results").delete().neq("file_name", "0").execute()
                st.rerun()
        with c2:
            uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
            if uploaded and st.button("🚀 LANCER"):
                for f in uploaded:
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    traiter_un_fichier(f.name)
                st.rerun()

    with tab_brut:
        if memoire_full:
            choix = st.selectbox("Fichier :", list(memoire_full.keys()))
            if choix: st.text_area("Scan complet", memoire_full[choix].get('raw_text', ''), height=500)

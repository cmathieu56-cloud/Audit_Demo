import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time

# ==============================================================================
# 1. CONFIGURATION
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
# 2. LOGIQUE MÉTIER
# ==============================================================================

def clean_float(val):
    if isinstance(val, (float, int)): return float(val)
    if not isinstance(val, str): return 0.0
    val = val.replace(' ', '').replace('€', '').replace('EUR', '').replace(',', '.')
    try: return float(val)
    except: return 0.0

def traiter_un_fichier(nom_fichier):
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = "Analyse cette facture et donne le JSON : fournisseur, date, num_facture, lignes (quantite, article, designation, prix_net, montant)."
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        data_json = json.loads(re.search(r"(\{.*\})", res.text, re.DOTALL).group(1))
        
        # SAUVEGARDE AVEC SCAN TOTAL
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
    except: memoire, memoire_full = {}, {}

    # Extraction des données pour le tableau
    all_rows = []
    for f_name, json_str in memoire.items():
        try:
            data = json.loads(json_str)
            for l in data.get('lignes', []):
                all_rows.append({
                    "Fichier": f_name, "Fournisseur": data.get('fournisseur', '?'),
                    "Article": l.get('article', '?'), "Montant": clean_float(l.get('montant', 0))
                })
        except: continue
    df = pd.DataFrame(all_rows)

    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIG", "📊 ANALYSE", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_import:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.write("📂 **Fichiers en base :**", len(memoire))
            st.divider()
            if st.button("🗑️ TOUT EFFACER (RAZ)", type="primary"):
                supabase.table("audit_results").delete().neq("file_name", "0").execute()
                st.rerun()
        
        with c2:
            uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
            if uploaded and st.button("🚀 LANCER"):
                # RETOUR DE LA BARRE DE PROGRESSION ET DU STATUT
                barre = st.progress(0)
                status = st.empty()
                for i, f in enumerate(uploaded):
                    status.write(f"⏳ Analyse en cours : **{f.name}**...")
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    ok, msg = traiter_un_fichier(f.name)
                    barre.progress((i + 1) / len(uploaded))
                status.success("✅ Traitement terminé !")
                time.sleep(1)
                st.rerun()

    with tab_brut:
        st.header("🔍 Scan total")
        if memoire_full:
            choix = st.selectbox("Choisir un fichier :", list(memoire_full.keys()))
            if choix: st.text_area("Texte brut Gemini", memoire_full[choix].get('raw_text', ''), height=500)

    with tab_analyse:
        st.dataframe(df, use_container_width=True)

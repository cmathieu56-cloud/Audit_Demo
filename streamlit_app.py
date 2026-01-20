import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time

# --- CONFIGURATION ---
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

st.set_page_config(page_title="Audit V21 - SQL Power", page_icon="🏗️", layout="wide")

try:
    supabase = create_client(URL_SUPABASE, CLE_ANON)
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Erreur connexion : {e}")

# --- LOGIQUE MÉTIER ---
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
    if "FRAIS_ANNEXE" in ref_up or any(x in label_up for x in ["FRAIS FACT", "GESTION"]): return "FRAIS GESTION"
    if any(x in label_up for x in ["PORT", "LIVRAISON", "TRANSPORT"]): return "FRAIS PORT"
    return "PRODUIT"

def extraire_json_robuste(texte):
    try:
        match = re.search(r"(\{.*\})", texte, re.DOTALL)
        if match: return json.loads(match.group(1))
    except: pass
    return None

def traiter_un_fichier(nom_fichier, user_id):
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("models/gemini-3-flash-preview")
        prompt = "Analyse cette facture et extrais les données en JSON : fournisseur, date, num_facture, ref_commande, lignes (quantite, article, designation, prix_brut, remise, prix_net, montant, num_bl_ligne)."
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        data_json = extraire_json_robuste(res.text)
        if data_json:
            supabase.table("audit_results").upsert({
                "file_name": nom_fichier, "user_id": user_id,
                "fournisseur": data_json.get('fournisseur', 'INCONNU').upper(),
                "analyse_complete": json.dumps(data_json), "raw_text": res.text
            }).execute()
            return True, "OK"
    except Exception as e: return False, str(e)

def afficher_rapport_sql(fournisseur_nom):
    res = supabase.table("vue_litiges_articles").select("*").eq("fournisseur", fournisseur_nom).execute()
    if not res.data:
        st.success(f"✅ Aucun litige détecté pour {fournisseur_nom}")
        return
    df_litiges = pd.DataFrame(res.data)
    st.markdown(f"### 🎸 Rapport de Litige SQL - {fournisseur_nom}")
    for article, group in df_litiges.groupby('ref'):
        total_art = group['perte_ligne'].sum()
        with st.expander(f"📦 {article} - {group['designation'].iloc[0]} (Perte : {total_art:.2f} €)"):
            st.table(group[['qte', 'num_facture', 'paye_u', 'cible_u', 'perte_ligne']])

# --- INTERFACE ---
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    user_id = session["user"]["id"]
    st.title("🏗️ Audit V21 - Logique SQL")

    res_db = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
    memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    
    all_rows = []
    for f_name, json_str in memoire.items():
        data = json.loads(json_str)
        for l in data.get('lignes', []):
            qte = clean_float(l.get('quantite', 1))
            montant = clean_float(l.get('montant', 0))
            p_net = clean_float(str(l.get('prix_net', '0')))
            all_rows.append({
                "Fichier": f_name, "Fournisseur": data.get('fournisseur', 'INCONNU').upper(),
                "Facture": data.get('num_facture', '-'), "Date": data.get('date', '-'),
                "Quantité": qte, "Article": l.get('article', 'SANS_REF'),
                "Désignation": l.get('designation', ''), "Montant": montant,
                "PU_Systeme": montant/qte if qte > 0 else 0,
                "Famille": detecter_famille(l.get('designation', ''), l.get('article', ''))
            })

    tab_analyse, tab_import = st.tabs(["📊 ANALYSE", "📥 IMPORT"])

    with tab_analyse:
        if not all_rows: st.warning("Importez des factures.")
        else:
            df = pd.DataFrame(all_rows)
            # Simplification : on utilise les totaux de perte calculés en SQL pour le podium
            res_total = supabase.table("vue_litiges_articles").select("fournisseur, perte_ligne").execute()
            df_pertes = pd.DataFrame(res_total.data)
            
            st.subheader("🏆 Podium des Dettes")
            if not df_pertes.empty:
                stats = df_pertes.groupby('fournisseur')['perte_ligne'].sum().reset_index()
                st.metric("💸 PERTE TOTALE", f"{stats['perte_ligne'].sum():.2f} €")
                
                sel = st.dataframe(stats, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
                
                if sel.selection.rows:
                    fourn_sel = stats.iloc[sel.selection.rows[0]]['fournisseur']
                    st.divider()
                    afficher_rapport_sql(fourn_sel)
            else:
                st.success("Aucune anomalie détectée.")

    with tab_import:
        uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
        if uploaded and st.button("🚀 LANCER"):
            for f in uploaded:
                with st.status(f"Analyse {f.name}..."):
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    traiter_un_fichier(f.name, user_id)
            st.rerun()

st.write(f"Dernière mise à jour : {time.strftime('%H:%M:%S')}")

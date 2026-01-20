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
    div.stButton > button:first-child { font-weight: bold; }
    div.stButton.delete-btn > button:first-child { 
        background-color: #ff4b4b; 
        color: white; 
        border-color: #ff4b4b;
    }
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
    if ',' in val and '.' in val:
        val = val.replace('.', '').replace(',', '.')
    else:
        val = val.replace(',', '.')
    try:
        return float(val)
    except:
        return 0.0

def detecter_famille(label, ref=""):
    if not isinstance(label, str): label = ""
    if not isinstance(ref, str): ref = ""
    label_up, ref_up = label.upper(), ref.upper()
    
    mots_taxes = ["ENERG", "TAXE", "CONTRIBUTION", "DEEE", "SORECOP", "ECO-PART", "ECO "]
    if any(x in label_up for x in mots_taxes) or any(x in ref_up for x in mots_taxes): 
        return "TAXE"

    if "FRAIS_ANNEXE" in ref_up: return "FRAIS GESTION"
    if label_up.strip() == "FF" or "FF " in label_up or " FF" in label_up:
        return "FRAIS GESTION"
    if any(x in label_up for x in ["FRAIS FACT", "FACTURE", "GESTION", "ADMINISTRATIF"]): 
        return "FRAIS GESTION"

    keywords_port = ["PORT", "LIVRAISON", "TRANSPORT", "EXPEDITION"]
    is_real_product_ref = len(ref) > 4 and not any(k in ref_up for k in ["PORT", "FRAIS"])
    if any(x in label_up for x in keywords_port) and not is_real_product_ref:
        exclusions_port = ["SUPPORT", "SUPORT", "PORTS", "RJ45", "DATA", "PANNEAU"]
        if not any(ex in label_up for x in exclusions_port): 
            return "FRAIS PORT"
            
    if "EMBALLAGE" in label_up: return "EMBALLAGE"

    mots_cles_frais_ref = ["PORT", "FRAIS", "SANS_REF", "DIVERS"]
    is_ref_exclusion = any(kw in ref_up for kw in mots_cles_frais_ref)
    ref_is_technique = (len(ref) > 3) and (not is_ref_exclusion)
    
    if ref_is_technique:
        if any(x in label_up for x in ["CLIM", "PAC", "POMPE A CHALEUR", "SPLIT"]): return "CLIM / PAC"
        if any(x in label_up for x in ["CABLE", "FIL ", "COURONNE", "U1000", "R2V"]): return "CABLAGE"
        if any(x in label_up for x in ["COLASTIC", "MASTIC", "CHIMIQUE", "COLLE"]): return "CONSOMMABLE"
        return "AUTRE_PRODUIT"
    
    return "AUTRE_PRODUIT"

def extraire_json_robuste(texte):
    try:
        match = re.search(r"(\{.*\})", texte, re.DOTALL)
        if match: return json.loads(match.group(1))
    except: pass
    return None

def traiter_un_fichier(nom_fichier, user_id):
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        # Correction du nom du modèle ici
        model = genai.GenerativeModel("models/gemini-3-flash-preview")
        
        prompt = """
        Analyse cette facture et extrais TOUTES les données structurées.
        JSON ATTENDU : { "fournisseur": "...", "date": "...", "num_facture": "...", "ref_commande": "...", "lignes": [...] }
        """
        
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        data_json = extraire_json_robuste(res.text)
        
        if data_json:
            supabase.table("audit_results").upsert({
                "file_name": nom_fichier,
                "user_id": user_id,
                "analyse_complete": json.dumps(data_json),
                "raw_text": res.text
            }).execute()
            return True, "OK"
    except Exception as e: return False, str(e)

# Nouvelle fonction SQL pour ton rapport sans IA
def afficher_rapport_sql(fournisseur_nom):
    res = supabase.table("vue_litiges_articles").select("*").eq("fournisseur", fournisseur_nom).execute()
    if not res.data:
        st.info(f"✅ Aucun litige détecté par SQL pour {fournisseur_nom}.")
        return
    df_litiges = pd.DataFrame(res.data)
    st.subheader(f"🎸 Rapport de Litige SQL - {fournisseur_nom}")
    for article, group in df_litiges.groupby('ref'):
        perte_totale = group['perte_ligne'].sum()
        with st.expander(f"📦 {article} - {group['designation'].iloc[0]} (Perte : {perte_totale:.2f} €)", expanded=True):
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
                    "Fichier": f_name, "Facture": data.get('num_facture', '-'),
                    "Date": data.get('date', '-'), "Fournisseur": fourn,
                    "Quantité": qte, "Article": l.get('article', 'SANS_REF'),
                    "Désignation": l.get('designation', ''), "Montant": montant,
                    "PU_Systeme": montant/qte if qte > 0 else 0,
                    "Famille": detecter_famille(l.get('designation', ''), l.get('article', '')),
                    "Ref_Cmd": data.get('ref_commande', '-'), "BL": l.get('num_bl_ligne', '-')
                })
        except: continue

    df = pd.DataFrame(all_rows)
    tab_analyse, tab_import, tab_brut = st.tabs(["📊 ANALYSE", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_analyse:
        if not all_rows: st.warning("Importez des données.")
        else:
            stats_fourn = df.groupby('Fournisseur')['Montant'].sum().reset_index()
            st.metric("💸 TOTAL", f"{df['Montant'].sum():.2f} €")
            selection_podium = st.dataframe(stats_fourn, on_select="rerun", selection_mode="single-row", hide_index=True)
            
            if selection_podium.selection.rows:
                fourn_selected = stats_fourn.iloc[selection_podium.selection.rows[0]]['Fournisseur']
                df_final = df[df['Fournisseur'] == fourn_selected]
                st.dataframe(df_final, hide_index=True)
                
                # Le 3ème truc : Branchement SQL
                st.markdown("---")
                afficher_rapport_sql(fourn_selected)

    with tab_import:
        uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
        if uploaded and st.button("🚀 LANCER"):
            for f in uploaded:
                with st.status(f"Analyse {f.name}..."):
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    traiter_un_fichier(f.name, user_id)
            st.rerun()

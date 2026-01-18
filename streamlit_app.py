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
# 1. CONFIGURATION & CONNEXIONS
# ==============================================================================
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

st.set_page_config(page_title="Audit V18 - Prod", page_icon="🏗️", layout="wide")

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
# 2. INTELLIGENCE MÉTIER
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
    
    mots_cles_frais_ref = ["PORT", "FRAIS", "SANS_REF", "DEEE", "TAXE", "ECO", "DIVERS"]
    ref_is_technique = (len(ref) > 3) and (ref_up not in mots_cles_frais_ref)
    
    if ref_is_technique:
        if any(x in label_up for x in ["CLIM", "PAC", "POMPE A CHALEUR", "SPLIT"]): return "CLIM / PAC"
        if any(x in label_up for x in ["CABLE", "FIL ", "COURONNE", "U1000", "R2V"]): return "CABLAGE"
        if any(x in label_up for x in ["COLASTIC", "MASTIC", "CHIMIQUE", "COLLE"]): return "CONSOMMABLE"
        return "AUTRE_PRODUIT"

    if any(x in label_up for x in ["FRAIS FACT", "FACTURE", "GESTION", "ADMINISTRATIF", "FF "]): return "FRAIS GESTION"
    if any(x in label_up for x in ["PORT", "LIVRAISON", "TRANSPORT", "EXPEDITION"]): return "FRAIS PORT"
    if any(x in label_up for x in ["ENERG", "TAXE", "CONTRIBUTION", "DEEE", "SORECOP", "ECO-PART"]): return "TAXE"
    if "EMBALLAGE" in label_up: return "EMBALLAGE"
    
    return "AUTRE_PRODUIT"

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
        
        prompt = """
        Analyse cette facture.
        1. INFOS CLÉS : Client / Fournisseur, DATE (YYYY-MM-DD), NUMÉRO DE FACTURE, NUMÉRO DE COMMANDE.
        2. TABLEAU PRODUITS : Extrais ligne par ligne (quantite, article, designation, prix_net, montant, num_bl_ligne).
        ⚠️ IGNORE DEEE/TVA/Eco-part.
        3. FRAIS CACHÉS : Port, Gestion, Energie... -> article="FRAIS_DETECTE".
        """
        
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        if not res.text: return False, "Vide"
        data_json = extraire_json_robuste(res.text)
        if not data_json: return False, "JSON Invalide"

        # --- SAUVEGARDE DU SCAN ---
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
        # --- CHARGEMENT DU SCAN ---
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except: 
        memoire = {}
        memoire_full = {}

    all_rows = []
    fournisseurs_detectes = set()

    for f_name, json_str in memoire.items():
        try:
            data = json.loads(json_str)
            fourn = data.get('fournisseur', 'INCONNU').upper()
            date_fac = data.get('date', 'Inconnue')
            num_fac = data.get('num_facture', '-')
            ref_cmd = data.get('ref_commande', '-')

            if "YESSS" in fourn: fourn = "YESSS ELECTRIQUE"
            elif "AUSTRAL" in fourn: fourn = "AUSTRAL HORIZON"
            elif "PARTEDIS" in fourn: fourn = "PARTEDIS"
            fournisseurs_detectes.add(fourn)
            
            for l in data.get('lignes', []):
                qte_ia = clean_float(l.get('quantite', 1))
                if qte_ia == 0: qte_ia = 1
                montant = clean_float(l.get('montant', 0))
                p_net = clean_float(l.get('prix_net', 0))
                num_bl = l.get('num_bl_ligne', '-')
                
                # Logic correction Plaques/Câbles
                qte_finale = qte_ia
                if montant > 0 and p_net > 0:
                    ratio = montant / p_net
                    if abs(ratio - round(ratio)) < 0.05: 
                         qte_math = round(ratio)
                         if qte_math != qte_ia and qte_math > 0:
                             qte_finale = qte_math

                if montant > 0 and qte_finale > 0: pu_systeme = montant / qte_finale
                elif p_net > 0: pu_systeme = p_net 
                else: pu_systeme = 0

                article = l.get('article', 'SANS_REF')
                if not article or article == "None" or article == "SANS_REF":
                    article = l.get('designation', 'SANS_NOM')[:20]
                famille = detecter_famille(l.get('designation', ''), article)

                all_rows.append({
                    "Fichier": f_name, "Facture": num_fac, "Date": date_fac, "Ref_Cmd": ref_cmd,
                    "BL": num_bl, "Fournisseur": fourn, "Quantité": qte_finale, "Article": article,
                    "Désignation": l.get('designation', ''), "Prix Net": p_net, "Montant": montant,
                    "PU_Systeme": pu_systeme, "Famille": famille
                })
        except: continue

    df = pd.DataFrame(all_rows)
    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIGURATION", "📊 ANALYSE & PREUVES", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_config:
        st.header("🛠️ Règles")
        default_data = [{"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0} for f in fournisseurs_detectes]
        if 'config_df' not in st.session_state: st.session_state['config_df'] = pd.DataFrame(default_data)
        edited_config = st.data_editor(st.session_state['config_df'], num_rows="dynamic", use_container_width=True)
        st.session_state['config_df'] = edited_config
        config_dict = edited_config.set_index('Fournisseur').to_dict('index')

    with tab_analyse:
        if df.empty: st.warning("⚠️ Aucune donnée.")
        else:
            # Ici tout ton code d'analyse original (Podium, Dettes, etc.)
            df_produits = df[~df['Famille'].isin(['FRAIS PORT', 'FRAIS GESTION', 'TAXE'])]
            ref_map = {}
            if not df_produits.empty:
                df_clean = df_produits[df_produits['Article'] != 'SANS_REF']
                if not df_clean.empty:
                    best_rows = df_clean.sort_values('PU_Systeme').drop_duplicates('Article', keep='first')
                    ref_map = best_rows.set_index('Article')[['PU_Systeme', 'Facture', 'Date']].to_dict('index')

            facture_totals = df.groupby('Fichier')['Montant'].sum().to_dict()
            anomalies = []
            for idx, row in df.iterrows():
                f_name, fourn = row['Fichier'], row['Fournisseur']
                rules = config_dict.get(fourn, {"Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
                perte, motif, cible = 0, "", 0
                
                if row['Famille'] == 'FRAIS PORT' and rules["Franco (Seuil €)"] > 0 and facture_totals.get(f_name, 0) >= rules["Franco (Seuil €)"]:
                    perte, motif = row['Montant'], "Hors Franco"
                elif row['Article'] in ref_map and row['PU_Systeme'] > ref_map[row['Article']]['PU_Systeme'] + 0.005:
                    perte = (row['PU_Systeme'] - ref_map[row['Article']]['PU_Systeme']) * row['Quantité']
                    motif, cible = "Hausse Prix", ref_map[row['Article']]['PU_Systeme']

                if perte > 0.01:
                    anomalies.append({**row, "Perte": perte, "Motif": motif, "Cible (U)": cible})

            if anomalies:
                df_ano = pd.DataFrame(anomalies)
                st.metric("💸 PERTE TOTALE", f"{df_ano['Perte'].sum():.2f} €")
                st.dataframe(df_ano, use_container_width=True)
            else: st.success("✅ Clean sheet.")

    with tab_import:
        st.header("📥 Charger")
        c_i, c_d = st.columns([1, 2])
        with c_i:
            st.write("📂 **En mémoire :**")
            st.dataframe(pd.DataFrame({"Fichiers": list(memoire.keys())}), hide_index=True)
            st.divider()
            # TON BOUTON ROUGE
            if st.button("🗑️ TOUT EFFACER (RAZ BASE)", type="primary", key="raz"):
                supabase.table("audit_results").delete().neq("file_name", "0").execute()
                st.rerun()
        with c_d:
            uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
            if uploaded and st.button("🚀 LANCER"):
                for f in uploaded:
                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                    traiter_un_fichier(f.name)
                st.rerun()

    with tab_brut:
        st.header("🔍 Scan total des documents")
        if memoire_full:
            choix = st.selectbox("Fichier :", list(memoire_full.keys()))
            if choix:
                st.text_area("Résultat Scan", memoire_full[choix].get('raw_text', 'Aucun scan'), height=500)

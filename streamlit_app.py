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
        
        1. INFOS CLÉS :
           - Client / Fournisseur
           - DATE de la facture (Format YYYY-MM-DD). C'est très important.
           - NUMÉRO DE FACTURE
           - NUMÉRO DE COMMANDE (Ref Client / Chantier)

        2. TABLEAU PRODUITS :
           Extrais ligne par ligne (quantite, article, designation, prix_net, montant, num_bl_ligne).
           
        JSON ATTENDU :
        {
            "fournisseur": "...",
            "date": "2025-03-31",
            "num_facture": "...",
            "lignes": [...]
        }
        """
        
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        if not res.text: return False, "Vide"
        data_json = extraire_json_robuste(res.text)
        if not data_json: return False, "JSON Invalide"

        # --- MODIFICATION 1 : AJOUT DU RAW_TEXT ---
        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "analyse_complete": json.dumps(data_json),
            "raw_text": res.text  # On sauvegarde l'intégralité de la réponse IA
        }).execute()
        return True, "OK"
    except Exception as e: return False, str(e)

# ==============================================================================
# 3. INTERFACE
# ==============================================================================
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    st.title("🏗️ Audit V18 - Prod")

    # --- CHARGEMENT ---
    try:
        res_db = supabase.table("audit_results").select("*").execute()
        # --- MODIFICATION 2 : ON GARDE TOUTES LES INFOS (dont le raw_text) ---
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except: 
        memoire = {}
        memoire_full = {}

    # --- PROCESSING ---
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
                
                qte_finale = qte_ia
                if montant > 0 and p_net > 0:
                    ratio = montant / p_net
                    if abs(ratio - round(ratio)) < 0.05: 
                         qte_math = round(ratio)
                         if qte_math != qte_ia and qte_math > 0:
                             qte_finale = qte_math

                if montant > 0 and qte_finale > 0:
                    pu_systeme = montant / qte_finale
                elif p_net > 0:
                    pu_systeme = p_net 
                else:
                    pu_systeme = 0

                article = l.get('article', 'SANS_REF')
                if not article or article == "None" or article == "SANS_REF":
                    article = l.get('designation', 'SANS_NOM')[:20]

                famille = detecter_famille(l.get('designation', ''), article)

                all_rows.append({
                    "Fichier": f_name,
                    "Facture": num_fac,
                    "Date": date_fac,
                    "Ref_Cmd": ref_cmd,
                    "BL": num_bl,
                    "Fournisseur": fourn,
                    "Quantité": qte_finale,
                    "Article": article,
                    "Désignation": l.get('designation', ''),
                    "Prix Net": p_net, 
                    "Montant": montant,
                    "PU_Systeme": pu_systeme,
                    "Famille": famille
                })
        except: continue

    df = pd.DataFrame(all_rows)

    # --- TABS ---
    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIGURATION", "📊 ANALYSE & PREUVES", "📥 IMPORT", "🔍 SCAN TOTAL"])

    # --- TAB 1 : CONFIG ---
    with tab_config:
        st.header("🛠️ Règles")
        
        default_data = []
        if fournisseurs_detectes:
            for f in fournisseurs_detectes:
                default_data.append({"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
        else:
            default_data.append({"Fournisseur": "EXEMPLE", "Franco (Seuil €)": 300.0, "Max Gestion (€)": 5.0})

        if 'config_df' not in st.session_state:
            st.session_state['config_df'] = pd.DataFrame(default_data)
        
        current_suppliers = st.session_state['config_df']['Fournisseur'].unique()
        for f in fournisseurs_detectes:
            if f not in current_suppliers:
                new_row = pd.DataFrame([{"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0}])
                st.session_state['config_df'] = pd.concat([st.session_state['config_df'], new_row], ignore_index=True)

        c1, c2 = st.columns([2, 1])
        with c1:
            edited_config = st.data_editor(st.session_state['config_df'], num_rows="dynamic", use_container_width=True)
            st.session_state['config_df'] = edited_config
            config_dict = edited_config.set_index('Fournisseur').to_dict('index')

    # --- TAB 2 : ANALYSE ---
    with tab_analyse:
        if df.empty:
            st.warning("⚠️ Aucune donnée.")
        else:
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
                f_name = row['Fichier']
                num_facture = row['Facture']
                fourn = row['Fournisseur']
                rules = config_dict.get(fourn, {"Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
                seuil_franco = rules["Franco (Seuil €)"]
                max_gestion = rules["Max Gestion (€)"]
                
                perte = 0
                motif = ""
                cible = 0.0 
                source_cible = "-"
                detail_tech = ""

                if row['Famille'] == 'FRAIS PORT':
                    total_fac = facture_totals.get(f_name, 0)
                    if seuil_franco > 0 and total_fac >= seuil_franco:
                        perte = row['Montant']
                        cible = 0.0
                        motif = "Hors Franco"
                        detail_tech = f"Total commande {total_fac}€ > Seuil {seuil_franco}€"
                    elif seuil_franco == 0:
                        perte = row['Montant']
                        cible = 0.0
                        motif = "Port Facturé"
                        detail_tech = "Config réglée à 0€"

                elif row['Famille'] == 'FRAIS GESTION':
                    if row['Montant'] > max_gestion:
                        perte = row['Montant'] - max_gestion
                        cible = max_gestion
                        motif = "Frais Abusifs"

                elif row['Article'] in ref_map and row['Famille'] not in ['FRAIS PORT', 'FRAIS GESTION', 'TAXE']:
                    best_info = ref_map[row['Article']]
                    best_price = best_info['PU_Systeme']
                    best_fac = best_info['Facture']
                    best_date = best_info.get('Date', '?')
                    
                    if row['PU_Systeme'] > best_price + 0.005:
                        ecart_u = row['PU_Systeme'] - best_price
                        perte = ecart_u * row['Quantité']
                        cible = best_price
                        motif = "Hausse Prix"
                        source_cible = f"{best_date}"
                        detail_tech = f"Meilleur: {best_price:.3f}€ (Facture {best_fac})"

                if perte > 0.01:
                    anomalies.append({
                        "Fournisseur": fourn,
                        "Qte": row['Quantité'],
                        "Ref": row['Article'],
                        "Payé (U)": row['PU_Systeme'],
                        "Cible (U)": cible,
                        "Source Cible": source_cible,
                        "Perte": perte,
                        "Motif": motif,
                        "Désignation": row['Désignation'],
                        "Num Facture": num_facture,
                        "Date Facture": row['Date'],
                        "Ref_Cmd": row['Ref_Cmd'],
                        "BL": row['BL'],
                        "Détails Techniques": detail_tech
                    })

            if anomalies:
                df_ano = pd.DataFrame(anomalies)
                total_perte = df_ano['Perte'].sum()

                st.subheader("🏆 Podium des Dettes")
                stats_fourn = df_ano.groupby('Fournisseur').agg(
                    Nb_Erreurs=('Perte', 'count'),
                    Total_Perte=('Perte', 'sum')
                ).reset_index().sort_values('Total_Perte', ascending=False)
                
                c_podium, c_metric = st.columns([2, 1])
                with c_metric:
                    st.metric("💸 PERTE TOTALE", f"{total_perte:.2f} €", delta_color="inverse")

                with c_podium:
                    selection_podium = st.dataframe(stats_fourn, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

                if selection_podium.selection.rows:
                    idx_podium = selection_podium.selection.rows[0]
                    fourn_selected = stats_fourn.iloc[idx_podium]['Fournisseur']
                    st.subheader(f"📉 Preuves pour : {fourn_selected}")
                    df_final = df_ano[df_ano['Fournisseur'] == fourn_selected]
                    st.dataframe(df_final, use_container_width=True, hide_index=True)

            else:
                st.success("✅ Clean sheet.")

    # --- TAB 3 : IMPORT ---
    with tab_import:
        st.header("📥 Charger")
        uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
        if uploaded and st.button("🚀 LANCER"):
            for f in uploaded:
                supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                traiter_un_fichier(f.name)
            st.rerun()

    # --- TAB 4 : SCAN TOTAL ---
    with tab_brut:
        st.header("🔍 Scan total des documents")
        # --- MODIFICATION 3 : AFFICHAGE DU TEXTE COMPLET ---
        if memoire_full:
            choix_file = st.selectbox("Choisir un fichier pour voir le scan complet :", list(memoire_full.keys()))
            if choix_file:
                st.subheader(f"Texte brut extrait de : {choix_file}")
                # Affiche le contenu de la colonne raw_text
                st.text_area("Résultat Gemini (Full Scan)", memoire_full[choix_file].get('raw_text', 'Aucun scan disponible'), height=500)
        else:
            st.info("Aucune donnée disponible.")

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
    
    # 1. PRIORITÉ ABSOLUE : LES TAXES (DEEE, ECO-PART, ETC.)
    # On regarde si le mot magique est dans la Désignation OU dans la Référence
    mots_taxes = ["ENERG", "TAXE", "CONTRIBUTION", "DEEE", "SORECOP", "ECO-PART", "ECO "]
    if any(x in label_up for x in mots_taxes) or any(x in ref_up for x in mots_taxes): 
        return "TAXE"

    # 2. Ensuite on traite le reste comme d'habitude
    mots_cles_frais_ref = ["PORT", "FRAIS", "SANS_REF", "DIVERS"]
    is_ref_exclusion = any(kw in ref_up for kw in mots_cles_frais_ref)
    ref_is_technique = (len(ref) > 3) and (not is_ref_exclusion)
    
    if ref_is_technique:
        if any(x in label_up for x in ["CLIM", "PAC", "POMPE A CHALEUR", "SPLIT"]): return "CLIM / PAC"
        if any(x in label_up for x in ["CABLE", "FIL ", "COURONNE", "U1000", "R2V"]): return "CABLAGE"
        if any(x in label_up for x in ["COLASTIC", "MASTIC", "CHIMIQUE", "COLLE"]): return "CONSOMMABLE"
        return "AUTRE_PRODUIT"

    if any(x in label_up for x in ["FRAIS FACT", "FACTURE", "GESTION", "ADMINISTRATIF", "FF "]): return "FRAIS GESTION"
    if any(x in label_up for x in ["PORT", "LIVRAISON", "TRANSPORT", "EXPEDITION"]): return "FRAIS PORT"
    if "EMBALLAGE" in label_up: return "EMBALLAGE"
    
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

def traiter_un_fichier(nom_fichier, user_id):
    try:
        path_storage = f"{user_id}/{nom_fichier}"
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        # 👇 PROMPT DURCI POUR ÉVITER LA RECOPIE FACTURE = COMMANDE
        prompt = """
        Analyse cette facture et extrais TOUTES les données structurées.
        
        1. INFOS ENTREPRISE & SÉCURITÉ :
           - Fournisseur (Nom complet)
           - Adresse du fournisseur (Ville/CP)
           - NUMÉRO DE TVA Intracommunautaire du fournisseur
           - IBAN / RIB : Cherche le code IBAN complet du fournisseur.
           - DATE de la facture (Format YYYY-MM-DD).
           - NUMÉRO DE FACTURE
           - NUMÉRO DE COMMANDE / CHANTIER : Cherche une mention "V/Réf", "Réf Client", "Chantier" ou "Commande". 
             ⚠️ INTERDICTION FORMELLE DE RECOPIER LE NUMÉRO DE FACTURE ICI.
             Si tu ne trouves aucune référence client distincte, mets simplement "-" (tiret).

        2. EXTRACTION INTELLIGENTE DES LIGNES :
           - Extrais le tableau principal des produits.
           - Cherche si un NUMÉRO DE BL (Bon de Livraison) est mentionné pour chaque ligne ou groupe de lignes.
           
           - ⚠️ RÈGLE D'OR (BAS DE PAGE) : Scanne minutieusement le bas de la facture (zone des totaux/taxes).
           Si tu trouves un MONTANT qui s'ajoute au total mais qui n'est pas de la TVA (exemple: une somme forfaitaire, un port, un emballage, ou une colonne "Divers/FF")...
           ... ALORS C'EST UN FRAIS !
           
           Pour ces montants trouvés en bas de page :
           - Cree une ligne avec quantite = 1
           - article = "FRAIS_ANNEXE" (ou "SANS_REF" s'il n'y a rien devant)
           - designation = Le nom de la colonne ou "Frais détecté"
           - prix_net = Le montant trouvé
           - montant = Le montant trouvé

        JSON ATTENDU :
        {
            "fournisseur": "...",
            "adresse_fournisseur": "...",
            "tva_fournisseur": "...",
            "iban": "...",
            "date": "2025-01-01",
            "num_facture": "...",
            "ref_commande": "...",
            "lignes": [
                {"quantite": 1, "article": "REF123", "prix_net": 10.0, "montant": 10.0, "num_bl_ligne": "..."}
            ]
        }
        """
        
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        if not res.text: return False, "Vide"
     data_json = extraire_json_robuste(res.text)
        if not data_json: return False, "JSON Invalide"

        # --- CORRECTIF : Si Facture = Commande, on efface ! ---
        n_fac = data_json.get('num_facture', '').strip()
        n_cmd = data_json.get('ref_commande', '').strip()
        
        # On nettoie si c'est identique ou si ça contient le numéro de facture
        if n_fac and n_cmd and (n_fac in n_cmd or n_cmd in n_fac):
             data_json['ref_commande'] = "-"
        # ------------------------------------------------------

        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "user_id": user_id,
            "analyse_complete": json.dumps(data_json),
            "raw_text": res.text
        }).execute()
        return True, "OK"
    except Exception as e: return False, str(e)

# ==============================================================================
# 3. INTERFACE PRINCIPALE
# ==============================================================================
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    if 'uploader_key' not in st.session_state:
        st.session_state['uploader_key'] = 0    
    user_id = session["user"]["id"]
    st.title("🏗️ Audit V21 - Logique Universelle")

    try:
        res_db = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except Exception as e: 
        st.error(f"Erreur chargement base : {e}")
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
            
            iban_f = data.get('iban', '-')
            tva_f = data.get('tva_fournisseur', '-')
            adr_f = data.get('adresse_fournisseur', '-')

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
                    "IBAN": iban_f,
                    "TVA_Intra": tva_f,
                    "Adresse": adr_f,
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

    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIGURATION", "📊 ANALYSE & PREUVES", "📥 IMPORT", "🔍 SCAN TOTAL"])

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
        
        if 'Fournisseur' in st.session_state['config_df'].columns:
            current_suppliers = st.session_state['config_df']['Fournisseur'].unique()
            for f in fournisseurs_detectes:
                if f not in current_suppliers:
                    new_row = pd.DataFrame([{"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0}])
                    st.session_state['config_df'] = pd.concat([st.session_state['config_df'], new_row], ignore_index=True)

        c1, c2 = st.columns([2, 1])
        with c1:
            edited_config = st.data_editor(st.session_state['config_df'], num_rows="dynamic", use_container_width=True)
            st.session_state['config_df'] = edited_config
            
            config_dict = {}
            if not edited_config.empty and "Fournisseur" in edited_config.columns:
                config_dict = edited_config.set_index('Fournisseur').to_dict('index')

    with tab_analyse:
        if df.empty:
            st.warning("⚠️ Aucune donnée pour ce compte. Allez dans IMPORT.")
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
                seuil_franco = rules.get("Franco (Seuil €)", 0.0)
                max_gestion = rules.get("Max Gestion (€)", 0.0)
                
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
                    selection_podium = st.dataframe(
                        stats_fourn, 
                        use_container_width=True, 
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        column_config={
                            "Total_Perte": st.column_config.NumberColumn("Total à Réclamer", format="%.2f €"),
                        }
                    )

                if selection_podium.selection.rows:
                    idx_podium = selection_podium.selection.rows[0]
                    fourn_selected = stats_fourn.iloc[idx_podium]['Fournisseur']
                    
                    st.divider()
                    st.subheader(f"📉 Preuves pour : {fourn_selected}")
                    df_final = df_ano[df_ano['Fournisseur'] == fourn_selected]

                    selection_detail = st.dataframe(
                        df_final,
                        use_container_width=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        hide_index=True,
                        column_order=["Date Facture", "Qte", "Ref", "Désignation", "Payé (U)", "Cible (U)", "Perte", "Motif"],
                        column_config={
                            "Date Facture": st.column_config.TextColumn("Date", width="small"),
                            "Qte": st.column_config.NumberColumn("Qte", format="%.0f", width="small"),
                            "Ref": st.column_config.TextColumn("Ref", width="small"),
                            "Payé (U)": st.column_config.NumberColumn("Payé (Calc)", format="%.3f €"),
                            "Cible (U)": st.column_config.NumberColumn("Cible", format="%.3f €"),
                            "Perte": st.column_config.NumberColumn("Perte", format="%.2f €"),
                            "Désignation": st.column_config.TextColumn("Désignation", width="medium"),
                        }
                    )
                    
                    if selection_detail.selection.rows:
                        idx_det = selection_detail.selection.rows[0]
                        row_sel = df_final.iloc[idx_det]
                        
                        st.info(f"🔎 **{row_sel['Ref']}**")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Prix Payé (Calc)", f"{row_sel['Payé (U)']:.3f} €")
                        c2.metric("Meilleur Prix", f"{row_sel['Cible (U)']:.3f} €")
                        c3.metric("Perte", f"{row_sel['Perte']:.2f} €")
                        
                        st.markdown("---")
                        st.write("🤠 **PIÈCES À CONVICTION :**")
                        st.write(f"📄 **Facture N° :** `{row_sel['Num Facture']}` (du {row_sel['Date Facture']})")
                        st.write(f"🚚 **Bon de Livraison :** `{row_sel['BL']}`")
                        st.write(f"🏗️ **Réf Chantier :** `{row_sel['Ref_Cmd']}`")
                        
                        if row_sel['Motif'] == "Hausse Prix":
                            st.warning(f"📉 **Historique :** C'était moins cher ({row_sel['Cible (U)']:.3f}€) le {row_sel['Source Cible']}. {row_sel['Détails Techniques']}")

            else:
                st.success("✅ Clean sheet. Aucune anomalie détectée.")
            
            st.divider()
            with st.expander("📝 Données brutes (Nettoyées)"):
                # 1. On masque les TAXES
                df_view = df[df['Famille'] != 'TAXE']
                
                # 2. On masque les colonnes "administratives" inutiles pour l'analyse
                cols_inutiles = ['IBAN', 'TVA_Intra', 'Adresse']
                df_view = df_view.drop(columns=cols_inutiles, errors='ignore')
                
                st.dataframe(df_view, use_container_width=True)

    with tab_import:
        st.header("📥 Charger")
        col_info, col_drop = st.columns([1, 2])
        
        with col_info:
            st.write("📂 **En mémoire (Compte actuel) :**")
            if memoire:
                st.dataframe(pd.DataFrame({"Fichiers": list(memoire.keys())}), hide_index=True, height=300)
            else:
                st.info("Vide")
            
            st.divider()
            if st.button("🗑️ TOUT EFFACER (CE COMPTE)", type="primary"):
                try:
                    supabase.table("audit_results").delete().eq("user_id", user_id).execute()
                    st.success("💥 Vos données sont vidées !")
                    st.session_state['uploader_key'] += 1 # 👈 C'est ça qui vide la liste
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

        with col_drop:
            # 👇 La clé magique est ici
            uploaded = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True, key=f"uploader_{st.session_state['uploader_key']}")
            force_rewrite = st.checkbox("⚠️ Écraser doublons (Forcer ré-analyse)", value=False)
            
            if uploaded: 
                if st.button("🚀 LANCER"):
                    barre = st.progress(0)
                    status = st.empty()
                    for i, f in enumerate(uploaded):
                        time.sleep(2)
                        
                        if f.name in memoire and not force_rewrite:
                            status.warning(f"⚠️ {f.name} ignoré (déjà présent).")
                            time.sleep(0.5)
                        else:
                            status.write(f"⏳ Analyse ({i+1}/{len(uploaded)}) : **{f.name}**...")
                            try:
                                supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                                ok, msg = traiter_un_fichier(f.name, user_id)
                                if ok: st.toast(f"✅ {f.name} OK")
                                else: st.error(f"❌ {f.name}: {msg}")
                            except Exception as up_err:
                                st.error(f"Erreur Upload {f.name}: {up_err}")
                                
                        barre.progress((i + 1) / len(uploaded))
                    status.success("✅ Traitement terminé !")
                    st.session_state['uploader_key'] += 1 # 👈 Et ici pour vider après succès
                    time.sleep(1)
                    st.rerun()

    with tab_brut:
        st.header("🔍 Scan total des documents")
        if memoire_full:
            choix_file = st.selectbox("Choisir un fichier pour voir le scan complet :", list(memoire_full.keys()))
            if choix_file:
                st.subheader(f"Texte brut extrait de : {choix_file}")
                raw_txt = memoire_full[choix_file].get('raw_text', 'Aucun scan disponible')
                st.text_area("Résultat Gemini (Full Scan)", raw_txt, height=400)
        else:
            st.info("Aucune donnée enregistrée pour ce compte.")








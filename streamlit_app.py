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
    
    # 1. TAXES (Priorité absolue)
    mots_taxes = ["ENERG", "TAXE", "CONTRIBUTION", "DEEE", "SORECOP", "ECO-PART", "ECO "]
    if any(x in label_up for x in mots_taxes) or any(x in ref_up for x in mots_taxes): 
        return "TAXE"

    # 2. FRAIS DE GESTION (C'est ici qu'on attrape le FF et le FRAIS_ANNEXE)
    if "FRAIS_ANNEXE" in ref_up: return "FRAIS GESTION"
    
    if label_up.strip() == "FF" or "FF " in label_up or " FF" in label_up:
        return "FRAIS GESTION"
        
    if any(x in label_up for x in ["FRAIS FACT", "FACTURE", "GESTION", "ADMINISTRATIF"]): 
        return "FRAIS GESTION"

    # 3. FRAIS DE PORT (Avec sécurité anti-faux positif)
    keywords_port = ["PORT", "LIVRAISON", "TRANSPORT", "EXPEDITION"]
    
    # Si la référence est longue (ex: AXIPAN10), c'est un produit, pas du port !
    # On considère qu'une vraie ref technique fait plus de 4 caractères
    is_real_product_ref = len(ref) > 4 and not any(k in ref_up for k in ["PORT", "FRAIS"])
    
    if any(x in label_up for x in keywords_port) and not is_real_product_ref:
        # Double sécurité : on évite les mots composés comme "SUPPORT" ou le pluriel "PORTS"
        exclusions_port = ["SUPPORT", "SUPORT", "PORTS", "RJ45", "DATA", "PANNEAU"]
        if not any(ex in label_up for ex in exclusions_port): 
            return "FRAIS PORT"
            
    if "EMBALLAGE" in label_up: return "EMBALLAGE"

    # 4. TRI TECHNIQUE
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

def appliquer_correctifs_specifiques(data, texte_complet):
    """
    C'est ici que tu reprends le contrôle manuel.
    Si l'IA rate un truc connu sur un fournisseur connu, on le force par code.
    """
    fourn = data.get('fournisseur', '').upper()
    
    # --- CAS SPÉCIFIQUE : YESSS ELECTRIQUE ---
    # Ils cachent le FF (Frais Facture) en bas dans le tableau de TVA
    if "YESSS" in fourn:
        # On cherche le motif "FF" suivi d'un montant (ex: FF 8.99) dans le texte brut
        # Le regex cherche : FF, espaces, puis des chiffres avec point ou virgule
        match_ff = re.search(r"FF\s+([\d\.,]+)", texte_complet)
        
        if match_ff:
            montant_ff = clean_float(match_ff.group(1))
            if montant_ff > 0:
                # On vérifie si la ligne existe déjà pour pas faire de doublon
                existe = any(l.get('article') == "FRAIS_ANNEXE" for l in data.get('lignes', []))
                
                if not existe:
                    # On injecte la ligne manuellement
                    data['lignes'].append({
                        "quantite": 1,
                        "article": "FRAIS_ANNEXE",
                        "designation": "Frais Facturation (Détecté par Script)",
                        "prix_brut": montant_ff,
                        "remise": 0,
                        "prix_net": montant_ff,
                        "montant": montant_ff,
                        "num_bl_ligne": "Script"
                    })
    
    return data

def traiter_un_fichier(nom_fichier, user_id):
    try:
        path_storage = f"{user_id}/{nom_fichier}"
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        
        model = genai.GenerativeModel("gemini-3-flash-preview")
        
        prompt = """
        Analyse cette facture et extrais TOUTES les données structurées.
        Utilise ta capacité de raisonnement pour valider chaque chiffre.

        1. INFOS ENTREPRISE & SÉCURITÉ :
           - Fournisseur (Nom complet), Adresse, TVA, IBAN, Date, Numéro Facture.
           - Numéro Commande : Cherche "V/Réf", "Chantier". Si vide, mets "-".

        2. EXTRACTION DES LIGNES (RÈGLES CRITIQUES) :
           - Extrais le tableau principal avec ces colonnes précises :
             * quantite : Le nombre d'unités. 🚨 RÈGLE D'OR : Vérifie que (Montant / Prix Net) = Quantité.
             * article : La référence technique.
             * designation : Le nom du produit.
             * prix_brut : Le prix catalogue (garde le slash /100 si présent).
             * remise : Le pourcentage de remise.
             * prix_net : Le prix payé (garde le slash /100 si présent).
             * montant : Le total HT de la ligne.
             * num_bl_ligne : Le numéro de BL.

        3. RÈGLE "FRAIS CACHÉS" :
           - Scanne le bas de la facture pour "FF", "Frais", "Port". 
           - Si trouvé, crée une ligne avec l'article "FRAIS_ANNEXE".

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
                {
                    "quantite": 1,
                    "article": "...",
                    "designation": "...",
                    "prix_brut": "...",
                    "remise": "...",
                    "prix_net": "...",
                    "montant": 0.0,
                    "num_bl_ligne": "..."
                }
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
        
        if n_fac and n_cmd and (n_fac in n_cmd or n_cmd in n_fac):
             data_json['ref_commande'] = "-"
        # ------------------------------------------------------

        # --- PATCH MANUEL : On repasse derrière l'IA pour les cas tordus ---
        data_json = appliquer_correctifs_specifiques(data_json, res.text)
        # -------------------------------------------------------------------

        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "user_id": user_id,
            "analyse_complete": json.dumps(data_json),
            "raw_text": res.text
       }).execute()
        return True, "OK"
    except Exception as e: return False, str(e)

def afficher_rapport_sql(fournisseur_nom):

    # Appel à la vue SQL (Calcul instantané en base)
    res = supabase.table("vue_litiges_articles").select("*").eq("fournisseur", fournisseur_nom).execute()
    
    if not res.data:
        st.info(f"✅ Aucun litige détecté par SQL pour {fournisseur_nom}.")
        return

    df_litiges = pd.DataFrame(res.data)
    st.subheader(f"🎸 Rapport de Litige SQL - {fournisseur_nom}")
    
    for article, group in df_litiges.groupby('ref'):
        perte_totale = group['perte_ligne'].sum()
        with st.expander(f"📦 {article} - {group['Désignation'].iloc[0]} (Perte : {perte_totale:.2f} €)", expanded=True):
            st.dataframe(
                group[['Qte', 'Num Facture', 'Payé (U)', 'Cible (U)', 'Perte']],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Qte": st.column_config.NumberColumn("Qte", width="small"),
                    "Payé (U)": st.column_config.NumberColumn("Payé (U)", format="%.4f €"),
                    "Cible (U)": st.column_config.NumberColumn("Cible (U)", format="%.4f €"),
                    "Perte": st.column_config.NumberColumn("Perte", format="%.2f €")
                }
            )
            
# ==============================================================================
# 3. INTERFACE PRINCIPALE
# ==============================================================================
session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    supabase.postgrest.auth(session["access_token"])
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
                
                # Calcul du Net (ex: 21.23 / 100)
                raw_net = str(l.get('prix_net', '0'))
                p_net = clean_float(raw_net)
                if '/' in raw_net:
                    try:
                        div = float(raw_net.split('/')[-1].replace(' ', ''))
                        if div > 0: p_net = p_net / div
                    except: pass

                # Calcul du Brut (ex: 141.50 / 100)
                raw_brut = str(l.get('prix_brut', '0'))
                p_brut = clean_float(raw_brut)
                if '/' in raw_brut:
                    try:
                        div_b = float(raw_brut.split('/')[-1].replace(' ', ''))
                        if div_b > 0: p_brut = p_brut / div_b
                    except: pass
                
                remise = str(l.get('remise', '-'))
                # --- FIN DU BLOC A INSERER ---
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
                    "Prix Brut": raw_brut,
                    "Remise": remise,
                    "Prix Net": p_net, 
                    "Montant": montant,
                    "PU_Systeme": pu_systeme,
                    "Famille": famille
                })
        except: continue

    df = pd.DataFrame(all_rows)

    tab_config, tab_analyse, tab_import, tab_brut = st.tabs(["⚙️ CONFIGURATION", "📊 ANALYSE & PREUVES", "📥 IMPORT", "🔍 SCAN TOTAL"])

    with tab_config:
        st.header("🛠️ Réglages Fournisseurs")
        
        # 1. Chargement initial depuis Supabase
        if 'config_df' not in st.session_state:
            try:
                res_cfg = supabase.table("user_configs").select("*").eq("user_id", user_id).execute()
                if res_cfg.data:
                    st.session_state['config_df'] = pd.DataFrame(res_cfg.data).rename(
                        columns={'franco': 'Franco (Seuil €)', 'max_gestion': 'Max Gestion (€)', 'fournisseur': 'Fournisseur'}
                    )[['Fournisseur', 'Franco (Seuil €)', 'Max Gestion (€)']]
                else:
                    st.session_state['config_df'] = pd.DataFrame(columns=['Fournisseur', 'Franco (Seuil €)', 'Max Gestion (€)'])
            except:
                st.session_state['config_df'] = pd.DataFrame(columns=['Fournisseur', 'Franco (Seuil €)', 'Max Gestion (€)'])

        # 2. Ajout des nouveaux fournisseurs détectés dans le scan
        current_df = st.session_state['config_df']
        for f in fournisseurs_detectes:
            if f not in current_df['Fournisseur'].values:
                new_line = pd.DataFrame([{"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0}])
                current_df = pd.concat([current_df, new_line], ignore_index=True)
        
        # 3. Édition du tableau
        edited_config = st.data_editor(current_df, num_rows="dynamic", use_container_width=True, key="editor_cfg")
        st.session_state['config_df'] = edited_config

        # 4. BOUTON DE SAUVEGARDE
        if st.button("💾 SAUVEGARDER LES RÉGLAGES", type="primary"):
            with st.spinner("Enregistrement..."):
                try:
                    for _, row in edited_config.iterrows():
                        supabase.table("user_configs").upsert({
                            "user_id": user_id,
                            "fournisseur": row['Fournisseur'],
                            "franco": float(row['Franco (Seuil €)']),
                            "max_gestion": float(row['Max Gestion (€)'])
                        }).execute()
                    st.success("✅ Réglages enregistrés !")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur de sauvegarde : {e}")
        
        config_dict = edited_config.set_index('Fournisseur').to_dict('index')

    with tab_analyse:
        if df.empty:
            st.warning("⚠️ Aucune donnée pour ce compte. Allez dans IMPORT.")
        else:
            # --- DEBUT AJOUT : TABLEAU DE BORD ACHATS (VERSION CENTRÉE) ---
            st.subheader("📈 Synthèse des Achats par Année")
            
            # 1. Préparation des données
            df_calc = df.copy()
            df_calc['Date_Ref'] = pd.to_datetime(df_calc['Date'], errors='coerce')
            
            # 2. Extraction Année
            df_calc['Année'] = df_calc['Date_Ref'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')

            # 3. Pivot
            df_pivot = df_calc.groupby(['Fournisseur', 'Année'])['Montant'].sum().reset_index()
            
            if not df_pivot.empty:
                matrice_achats = df_pivot.pivot(index='Fournisseur', columns='Année', values='Montant').fillna(0)
                matrice_achats['TOTAL PÉRIODE'] = matrice_achats.sum(axis=1)
                matrice_achats = matrice_achats.sort_values('TOTAL PÉRIODE', ascending=False)
                
                # 4. Affichage STYLE (Centré)
                # On utilise .style pour forcer l'alignement au centre des entêtes et des cellules
                st.dataframe(
                    matrice_achats.style
                    .format("{:.2f} €")
                    .set_properties(**{'text-align': 'center'})
                    .set_table_styles([dict(selector='th', props=[('text-align', 'center')])]),
                    use_container_width=True
                )
                st.divider()
            # --- FIN AJOUT ---

            df_produits = df[~df['Famille'].isin(['FRAIS PORT', 'FRAIS GESTION', 'TAXE'])]
            ref_map = {}
            if not df_produits.empty:
                df_clean = df_produits[df_produits['Article'] != 'SANS_REF']
                if not df_clean.empty:
                    best_rows = df_clean.sort_values('PU_Systeme').drop_duplicates('Article', keep='first')
                    # 1. MÉMOIRE (Déjà présente dans ton code, je garde)
                    ref_map = best_rows.set_index('Article')[['PU_Systeme', 'Facture', 'Date', 'Remise', 'Prix Brut']].to_dict('index')

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
                # 2. INITIALISATION (Corrigée : Placée ICI, avant les IF)
                remise_cible_str = "-" 
                
                # --- LOGIQUE 1 : FRAIS (Gestion & Port) ---
                if row['Famille'] == "FRAIS GESTION":
                    if row['Montant'] > max_gestion:
                        perte = row['Montant'] - max_gestion
                        cible = max_gestion
                        motif = "Frais Facturation Abusifs"
                        detail_tech = f"(Max autorisé: {max_gestion}€)"
                
                elif row['Famille'] == "FRAIS PORT":
                    total_fac = facture_totals.get(f_name, 0)
                    if total_fac >= seuil_franco:
                        perte = row['Montant']
                        motif = "Port facturé malgré Franco"
                        cible = 0.0
                        detail_tech = f"(Total Facture: {total_fac:.2f}€ > Franco: {seuil_franco}€)"
                        remise_cible_str = "100%"

                # --- LOGIQUE 2 : PRODUITS (Hausse de prix) ---
                else:
                    article_courant = row['Article']
                    if article_courant in ref_map and article_courant != 'SANS_REF':
                        ref_info = ref_map[article_courant]
                        best_price = ref_info['PU_Systeme']
                        best_fac = ref_info['Facture']
                        best_date = ref_info['Date']
                        
                        # 3. LOGIQUE RÉCUPÉRATION (Avec Sécurité Anti-Crash)
                        best_remise = str(ref_info.get('Remise', '-')) 
                        
                        try:
                            # On essaie de convertir, si ça rate on met 0.0
                            val_temp = ref_info.get('Prix Brut', 0.0)
                            best_brut = float(str(val_temp).replace(',', '.').strip())
                        except:
                            best_brut = 0.0
                            
                        curr_brut = 0.0
                        if row['Prix Brut']:
                            try:
                                curr_brut = float(str(row['Prix Brut']).replace(',', '.').strip())
                            except: pass

                        if row['PU_Systeme'] > best_price + 0.005:
                            ecart_u = row['PU_Systeme'] - best_price
                            perte = ecart_u * row['Quantité']
                            cible = best_price
                            motif = "Hausse Prix"
                            source_cible = f"{best_date}"
                            
                            # On stocke la remise texte
                            remise_cible_str = best_remise
                            
                            details = [f"(Facture {best_fac})"]
                            # ALERTE HAUSSE BRUT
                            if best_brut > 0 and curr_brut > best_brut + 0.01:
                                details.append(f"Hausse Tarif Brut ({best_brut:.2f} -> {curr_brut:.2f})")
                            
                            detail_tech = " ".join(details)

                if perte > 0.01:
                    # --- Nettoyage Affichage Prix Brut ---
                    prix_brut_affiche = row['Prix Brut']
                    try:
                        val_float = float(str(prix_brut_affiche).replace(' ', '').replace(',', '.'))
                        prix_brut_affiche = f"{val_float:.2f}"
                    except: pass
                    
                    if remise_cible_str == "-" and row['Famille'] not in ["FRAIS GESTION", "FRAIS PORT"]:
                         remise_cible_str = "?"

                    anomalies.append({
                        "Fournisseur": fourn,
                        "Num Facture": row['Facture'],
                        "Ref_Cmd": row['Ref_Cmd'], 
                        "BL": row['BL'], 
                        "Famille": row['Famille'],
                        "PU_Systeme": row['PU_Systeme'],
                        "Montant": row['Montant'],
                        "Prix Brut": prix_brut_affiche,
                        "Remise": row['Remise'],
                        "Remise Cible": remise_cible_str, # 4. AFFICHAGE (Corrigé)
                        "Qte": row['Quantité'],
                        "Ref": row['Article'],
                        "Désignation": row['Désignation'],
                        "Payé (U)": row['PU_Systeme'],
                        "Cible (U)": cible,
                        "Perte": perte,
                        "Motif": motif,
                        "Date Facture": row['Date'],
                        "Source Cible": source_cible,     
                        # --- LIGNE DE REPÈRE AVANT ---
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
                    # APPEL DE LA FONCTION SQL (Analyse rapide)
                    st.subheader(f"📊 Détail des Anomalies (Audit Python) - {fourn_selected}")
                    
                    # Filtrage des anomalies calculées en Python pour ce fournisseur
                    df_litiges_fourn = pd.DataFrame([a for a in anomalies if a['Fournisseur'] == fourn_selected])
                    
                    if not df_litiges_fourn.empty:
                        for article, group in df_litiges_fourn.groupby('Ref'):
                            perte_totale = group['Perte'].sum()
                            
                            # Récupération des infos de référence pour le titre
                            prix_ref = group['Cible (U)'].iloc[0]
                            date_ref = group['Source Cible'].iloc[0]
                            remise_ref = group['Remise Cible'].iloc[0] # Récupération du format "60+5"
                            
                            # Affichage du nom de l'article avec remise identique à la facture
                            st.markdown(f"### 📦 {article} - {group['Désignation'].iloc[0]} | {prix_ref:.4f} € (Remise: {remise_ref}) (le {date_ref})")
                            
                            st.dataframe(
                                group[['Num Facture', 'Date Facture', 'Qte', 'Remise', 'Payé (U)', 'Perte']], 
                                hide_index=True, 
                                use_container_width=True,
                                column_config={
                                    "Qte": st.column_config.NumberColumn("Qte", width=10),
                                    "Remise": st.column_config.TextColumn("Remise"),
                                    "Payé (U)": st.column_config.NumberColumn("Payé (U)", format="%.4f €"),
                                    "Perte": st.column_config.NumberColumn("Perte", format="%.2f €")
                                }
                            )   
                    else:
                        st.info(f"✅ Aucune anomalie détectée pour {fourn_selected}.")


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
                    for i, f in enumerate(uploaded):
                        with st.status(f"Analyse de {f.name}...", expanded=True) as status_box:
                            if f.name in memoire and not force_rewrite:
                                status_box.update(label=f"⚠️ {f.name} ignoré", state="error")
                            else:
                                status_box.write("📤 Étape 1 : Envoi vers Supabase...")
                                try:
                                    supabase.storage.from_("factures_audit").upload(f.name, f.getvalue(), {"upsert": "true"})
                                    status_box.write("🧠 Étape 2 : L'IA calcule (15-20s)...")
                                    ok, msg = traiter_un_fichier(f.name, user_id)
                                    
                                    if ok:
                                        status_box.update(label=f"✅ {f.name} fini", state="complete", expanded=False)
                                    else:
                                        status_box.update(label=f"❌ Erreur {f.name}", state="error")
                                        st.error(msg)
                                except Exception as up_err:
                                    status_box.update(label="❌ Erreur technique", state="error")
                                    st.error(up_err)
                        
                        barre.progress((i + 1) / len(uploaded))

                    st.session_state['uploader_key'] += 1 
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




























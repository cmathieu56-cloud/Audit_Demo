import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time
import os
from datetime import datetime
from io import BytesIO

# ==============================================================================
# 1. CONFIGURATION & REGISTRE
# ==============================================================================
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

try:
    supabase = create_client(URL_SUPABASE, CLE_ANON)
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Erreur connexion : {e}") 

def charger_registre():
    """LOUIS : Cette fonction récupère tous les contrats et promos déjà enregistrés en base SQL"""
    try:
        res = supabase.table("accords_commerciaux").select("*").execute()
        return {r['article']: {
            'type': r['type_accord'], 
            'valeur': r['valeur'], 
            'unite': r['unite'], 
            'designation': r.get('designation', ''),
            'fournisseur': r.get('fournisseur', ''),
            'marque': r.get('marque', ''),
            'date': r['date_maj']
        } for r in res.data}
    except:
        return {}

def sauvegarder_accord(article, type_accord, valeur, unite="EUR", designation="", fournisseur="", marque=""):
    """LOUIS : Cette fonction enregistre tes clics (Promo/Contrat) dans les nouvelles colonnes SQL"""
    try:
        supabase.table("accords_commerciaux").upsert({
            "article": article,
            "type_accord": type_accord,
            "valeur": valeur,
            "unite": unite,
            "designation": designation,
            "fournisseur": fournisseur,
            "marque": marque,
            "date_maj": datetime.now().strftime("%Y-%m-%d"),
            "modifie_par": "Système"
        }).execute()
    except Exception as e:
        st.error(f"Erreur sauvegarde Supabase : {e}")

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

def calculer_remise_combine(val_str):
    """Convertit '60+10' ou '80+25' en pourcentage total (ex: 85.0)"""
    if not isinstance(val_str, str): return 0.0
    val_str = val_str.replace('%', '').replace(' ', '').replace('EUR', '').replace(',', '.')
    
    if not val_str: return 0.0
    
    try:
        parts = val_str.split('+')
        reste_a_payer = 1.0
        
        for p in parts:
            if p.strip():
                reste_a_payer *= (1 - float(p.strip())/100)
                
        remise_totale = (1 - reste_a_payer) * 100
        return round(remise_totale, 2)
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
        if not any(ex in label_up for ex in exclusions_port): 
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

def detecter_famille_cuivre(article, designation):
    """Identifie si un article contient du cuivre (prix variable)."""
    label_up = str(designation).upper()
    ref_up = str(article).upper()
    
    keywords_cuivre = [
        "CABLE", "U1000", "R2V", "H07", "FIL", "COURONNE", 
        "TOURET", "ICTA", "XVB", "RO2V", "CUIVRE", "GAINE PREFILEE"
    ]
    
    return any(kw in label_up or kw in ref_up for kw in keywords_cuivre)

def calculer_seuil_tolerance(is_cuivre):
    if is_cuivre:
        return 1.30
    else:
        return 1.10

def extraire_json_robuste(texte):
    try:
        match = re.search(r"(\{.*\})", texte, re.DOTALL)
        if match: return json.loads(match.group(1))
    except: pass
    return None

def appliquer_correctifs_specifiques(data, texte_complet):
    """Correctifs manuels pour les cas tordus (FF cachés, etc.)"""
    fourn = data.get('fournisseur', '').upper()
    
    if "YESSS" in fourn:
        match_ff = re.search(r"FF\s+([\d\.,]+)", texte_complet)
        
        if match_ff:
            montant_ff = clean_float(match_ff.group(1))
            if montant_ff > 0:
                existe = any(l.get('article') == "FRAIS_ANNEXE" for l in data.get('lignes', []))
                
                if not existe:
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


# ==============================================================================
# FIX PRINCIPAL : CALCUL DU PRIX UNITAIRE RÉEL
# ==============================================================================
def calculer_prix_unitaire_reel(ligne, qte):
    """
    LOUIS : C'est LA fonction clé qui résout ton problème.
    
    Elle calcule le VRAI prix unitaire en utilisant la méthode la plus fiable :
    Montant Total / Quantité = Prix Unitaire Réel
    
    Peu importe ce que Gemini renvoie dans prix_net ou base_facturation,
    le montant et la quantité sont TOUJOURS corrects sur la facture.
    """
    montant = clean_float(ligne.get('montant', 0))
    
    # Sécurité : si qte est 0 ou montant est 0, on essaie autrement
    if qte <= 0:
        qte = clean_float(ligne.get('quantite', 1))
        if qte <= 0:
            qte = 1
    
    if montant > 0 and qte > 0:
        # MÉTHODE 1 (PRIORITAIRE) : Montant / Qté = PU réel
        return montant / qte
    
    # MÉTHODE 2 (Fallback) : Utiliser prix_net avec base_facturation
    prix_net_brut = clean_float(ligne.get('prix_net_unitaire', ligne.get('prix_net', 0)))
    base_fac = float(ligne.get('base_facturation', 1))
    if base_fac <= 0:
        base_fac = 1
    
    # Détection auto de la base si pas renseignée
    # Si le prix_net est > 10€ et la qté >= 100, c'est probablement un prix /100
    if base_fac == 1 and prix_net_brut > 10 and qte >= 100:
        # Vérification : est-ce que prix_net/100 * qte ≈ montant ?
        test_pu = prix_net_brut / 100
        if montant > 0 and abs(test_pu * qte - montant) < 0.10:
            base_fac = 100
    
    if prix_net_brut > 0:
        return prix_net_brut / base_fac
    
    return 0.0


def traiter_un_fichier(nom_fichier, user_id):
    try:
        path_storage = f"{user_id}/{nom_fichier}"
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        prompt = """
        Analyse cette facture et extrais TOUTES les données structurées.
        Utilise ta capacité de raisonnement pour valider chaque chiffre.

        1. INFOS ENTREPRISE & SÉCURITÉ :
           - Fournisseur (Nom complet), Adresse, TVA, IBAN, Date, Numéro Facture.
           - Numéro Commande : Cherche "V/Réf", "Chantier", "REF CLIENT". Si vide, mets "-".

        2. EXTRACTION DES LIGNES (RÈGLES CRITIQUES) :
           - Extrais le tableau principal avec ces colonnes précises :
             * quantite : Le nombre d'unités commandées (ex: 100, 50, 1).
             * article : La référence technique (ex: 52041, S520445).
             * designation : Le nom du produit.
             * prix_brut_unitaire : Le prix catalogue BRUT affiché.
             * base_facturation : IMPORTANT ! Regarde la colonne "Unité" ou après le prix.
               - Si tu vois "/ 100" ou "/100" → mets 100
               - Si tu vois "/ 1000" ou "/1000" → mets 1000  
               - Si tu vois "/ 1" ou rien → mets 1
             * remise : Le pourcentage de remise (ex: "60+10", "70", "80+25").
             * prix_net_unitaire : Le prix NET affiché (après remise, AVANT multiplication par qté).
             * montant : Le TOTAL HT de la ligne (= prix_net × qté, ou prix_net × qté/base si applicable).
             * num_bl_ligne : Le numéro de BL.

        3. RÈGLE DE VALIDATION :
           - Vérifie que : montant ≈ (prix_net_unitaire / base_facturation) × quantite
           - Si ça ne colle pas, ajuste base_facturation.

        4. RÈGLE "FRAIS CACHÉS" :
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
                    "quantite": 100,
                    "article": "52041",
                    "designation": "EUR OHM Boite plc d67 p 40 XL",
                    "prix_brut_unitaire": 137.37,
                    "base_facturation": 100,
                    "remise": "70",
                    "prix_net_unitaire": 41.21,
                    "montant": 41.21,
                    "num_bl_ligne": "..."
                }
            ]
        }
        """
        
        res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        if not res.text: return False, "Vide"
        
        data_json = extraire_json_robuste(res.text)
        if not data_json: return False, "JSON Invalide"

        n_fac = data_json.get('num_facture', '').strip()
        n_cmd = data_json.get('ref_commande', '').strip()
        
        if n_fac and n_cmd and (n_fac in n_cmd or n_cmd in n_fac):
             data_json['ref_commande'] = "-"

        data_json = appliquer_correctifs_specifiques(data_json, res.text)

        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "user_id": user_id,
            "analyse_complete": json.dumps(data_json),
            "raw_text": res.text
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
    st.title("🏗️ Audit V22 - Calcul PU Corrigé")

    try:
        res_db = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except Exception as e: 
        if "JWT expired" in str(e):
            st.session_state.clear()
            st.rerun()
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
                # =====================================================
                # FIX V22 : CALCUL SIMPLIFIÉ ET ROBUSTE DU PU
                # =====================================================
                qte_raw = clean_float(l.get('quantite', 1))
                if qte_raw == 0: qte_raw = 1
                
                montant = clean_float(l.get('montant', 0))
                
                # NOUVELLE MÉTHODE : On utilise la fonction centralisée
                pu_systeme = calculer_prix_unitaire_reel(l, qte_raw)
                
                # Calcul de la quantité finale (vérification croisée)
                qte_finale = qte_raw
                if montant > 0 and pu_systeme > 0.001:
                    ratio = montant / pu_systeme
                    if abs(ratio - round(ratio)) < 0.05: 
                         qte_math = round(ratio)
                         if qte_math != qte_raw and qte_math > 0:
                             qte_finale = qte_math

                # Récupération du prix brut pour affichage
                base_fac = float(l.get('base_facturation', 1))
                if base_fac <= 0: base_fac = 1
                
                p_brut_lu = clean_float(l.get('prix_brut_unitaire', l.get('prix_brut', 0)))
                p_brut = p_brut_lu / base_fac
                
                # Détection auto base_facturation si Gemini l'a ratée
                if base_fac == 1 and p_brut_lu > 50 and qte_finale >= 100:
                    # Test : est-ce que ça colle mieux avec /100 ?
                    if montant > 0:
                        test_pu_100 = p_brut_lu / 100
                        # On regarde si le prix net est cohérent avec /100
                        p_net_lu = clean_float(l.get('prix_net_unitaire', l.get('prix_net', 0)))
                        if p_net_lu > 10 and abs((p_net_lu/100) * qte_finale - montant) < 1:
                            p_brut = p_brut_lu / 100
                            base_fac = 100
                
                raw_brut = f"{p_brut:.4f}"
                
                # Remise
                raw_remise = str(l.get('remise', '0'))
                val_remise = calculer_remise_combine(raw_remise)
                remise = f"{val_remise:g}%" if val_remise > 0 else "-"
                
                # Autres champs
                num_bl = l.get('num_bl_ligne', '-')
                p_net = pu_systeme  # Le prix net unitaire = PU système

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
                    "Famille": famille,
                    "Base_Fac": base_fac  # DEBUG : pour voir ce que Gemini renvoie
                })
        except: continue

    df = pd.DataFrame(all_rows)

    tab_config, tab_analyse, tab_import, tab_brut, tab_debug = st.tabs([
        "⚙️ CONFIGURATION", "📊 ANALYSE & PREUVES", "📥 IMPORT", "🔍 SCAN TOTAL", "🐛 DEBUG"
    ])

    with tab_config:
        st.header("🛠️ Réglages Fournisseurs")
        
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

        current_df = st.session_state['config_df']
        for f in fournisseurs_detectes:
            if f not in current_df['Fournisseur'].values:
                new_line = pd.DataFrame([{"Fournisseur": f, "Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0}])
                current_df = pd.concat([current_df, new_line], ignore_index=True)
        
        edited_config = st.data_editor(current_df, num_rows="dynamic", use_container_width=True, key="editor_cfg")
        st.session_state['config_df'] = edited_config

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

    # ==============================================================================
    # TAB DEBUG : Pour voir ce qui se passe vraiment
    # ==============================================================================
    with tab_debug:
        st.header("🐛 Debug - Données brutes par article")
        
        if not df.empty:
            # Filtre par article
            articles_uniques = sorted(df['Article'].unique().tolist())
            article_choisi = st.selectbox("Choisir un article à inspecter :", articles_uniques)
            
            if article_choisi:
                df_art = df[df['Article'] == article_choisi].sort_values('Date')
                
                st.subheader(f"📦 Données pour : {article_choisi}")
                st.dataframe(
                    df_art[['Date', 'Facture', 'Quantité', 'Prix Brut', 'Remise', 'Prix Net', 'Montant', 'PU_Systeme', 'Base_Fac']],
                    hide_index=True,
                    use_container_width=True
                )
                
                # Calcul du meilleur prix
                best_pu = df_art['PU_Systeme'].min()
                best_row = df_art[df_art['PU_Systeme'] == best_pu].iloc[0]
                
                worst_pu = df_art['PU_Systeme'].max()
                worst_row = df_art[df_art['PU_Systeme'] == worst_pu].iloc[0]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.success(f"""
                    **✅ MEILLEUR PRIX :**
                    - PU : {best_pu:.4f} €
                    - Date : {best_row['Date']}
                    - Remise : {best_row['Remise']}
                    - Facture : {best_row['Facture']}
                    """)
                
                with col2:
                    st.error(f"""
                    **❌ PIRE PRIX :**
                    - PU : {worst_pu:.4f} €
                    - Date : {worst_row['Date']}
                    - Remise : {worst_row['Remise']}
                    - Facture : {worst_row['Facture']}
                    """)
                
                ecart = worst_pu - best_pu
                if ecart > 0.01:
                    st.warning(f"⚠️ ÉCART : {ecart:.4f} € par unité ({(ecart/best_pu)*100:.1f}% de hausse)")
        else:
            st.info("Aucune donnée. Importez des factures d'abord.")

    with tab_analyse:
        if df.empty:
            st.warning("⚠️ Aucune donnée pour ce compte. Allez dans IMPORT.")
        else:
            st.subheader("📈 Synthèse des Achats par Année")
            
            df_calc = df.copy()
            df_calc['Date_Ref'] = pd.to_datetime(df_calc['Date'], errors='coerce')
            df_calc['Année'] = df_calc['Date_Ref'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')

            df_pivot = df_calc.groupby(['Fournisseur', 'Année'])['Montant'].sum().reset_index()
            
            if not df_pivot.empty:
                matrice_achats = df_pivot.pivot(index='Fournisseur', columns='Année', values='Montant').fillna(0)
                matrice_achats['TOTAL PÉRIODE'] = matrice_achats.sum(axis=1)
                matrice_achats = matrice_achats.sort_values('TOTAL PÉRIODE', ascending=False)
                
                matrice_achats.index.name = None
                matrice_achats.columns.name = None
                
                html_code = matrice_achats.style.format("{:.2f} €")\
                    .set_properties(**{
                        'text-align': 'center', 
                        'border': '2px solid black', 
                        'color': 'black',
                        'font-weight': 'bold'
                    })\
                    .set_table_styles([
                        {'selector': 'th', 'props': [
                            ('background-color', '#e0e0e0'), 
                            ('color', 'black'), 
                            ('text-align', 'center'), 
                            ('border', '2px solid black'),
                            ('font-size', '16px')
                        ]},
                        {'selector': 'table', 'props': [
                            ('border-collapse', 'collapse'),
                            ('width', '100%')
                        ]}
                    ]).to_html()
                
                st.markdown(html_code, unsafe_allow_html=True)
                st.divider()

            df_produits = df[~df['Famille'].isin(['FRAIS PORT', 'FRAIS GESTION', 'TAXE'])]
            ref_map = {}
            registre = charger_registre()
            
            if not df_produits.empty:
                df_clean = df_produits[df_produits['Article'] != 'SANS_REF'].copy()
                df_clean['Remise_Val'] = df_clean['Remise'].apply(lambda x: clean_float(str(x).replace('%', '')))
                
                for art, group in df_clean.groupby('Article'):
                    accord = registre.get(art)
                    
                    # =====================================================
                    # FIX V22 : SÉLECTION CORRECTE DU MEILLEUR PRIX
                    # =====================================================
                    # On trie par PU_Systeme croissant pour avoir le VRAI meilleur prix
                    valid_prices = group[group['PU_Systeme'] > 0.001].sort_values('PU_Systeme', ascending=True)
                    valid_remises = group[group['Remise_Val'] > 0].sort_values('Remise_Val', ascending=False)

                    # Gestion des PROMOS marquées
                    idx_r, idx_p = 0, 0
                    if accord and accord['type'] == "PROMO":
                        if not valid_prices.empty:
                            prix_promo = valid_prices.iloc[0]['PU_Systeme']
                            valid_prices = valid_prices[abs(valid_prices['PU_Systeme'] - prix_promo) > 0.10]
                            valid_remises = valid_remises[abs(valid_remises['PU_Systeme'] - prix_promo) > 0.10]

                    best_p_row = valid_prices.iloc[idx_p] if not valid_prices.empty else group.iloc[0]
                    best_r_row = valid_remises.iloc[idx_r] if not valid_remises.empty else group.iloc[0]

                    # Remise finale
                    remise_finale = accord['valeur'] if (accord and accord['type'] == "CONTRAT") else best_r_row['Remise_Val']

                    # Correction logique "Prix Net" vs "Prix Brut"
                    p_net_record = best_p_row['PU_Systeme']
                    p_net_standard = best_r_row['PU_Systeme']
                    
                    if p_net_record < (p_net_standard - 0.05) and best_p_row['Remise_Val'] == 0:
                        brut_ref = clean_float(best_r_row['Prix Brut'])
                        if brut_ref > 0:
                            taux_virtuel = (1 - (p_net_record / brut_ref)) * 100
                            remise_finale = round(taux_virtuel, 2)

                    # Alertes prix forcé
                    lignes_sans_remise = group[group['Remise_Val'] == 0]
                    alerte_prix_force = None
                    derniere_commande_mois = 0
                    
                    if not lignes_sans_remise.empty:
                        meilleur_prix_force = lignes_sans_remise['PU_Systeme'].min()
                        
                        if meilleur_prix_force < best_p_row['PU_Systeme'] - 0.10:
                            alerte_prix_force = "PROMO_OK"
                        elif meilleur_prix_force > best_p_row['PU_Systeme'] + 0.10:
                            alerte_prix_force = "SUSPECT"
                        
                        try:
                            date_derniere = pd.to_datetime(group['Date']).max()
                            date_actuelle = datetime.now()
                            delta_mois = (date_actuelle.year - date_derniere.year) * 12 + (date_actuelle.month - date_derniere.month)
                            derniere_commande_mois = delta_mois
                            
                            if delta_mois > 12 and meilleur_prix_force > 0:
                                if alerte_prix_force != "SUSPECT":
                                    alerte_prix_force = "ANCIEN"
                        except:
                            pass
                    
                    ref_map[art] = {
                        'Best_Remise': remise_finale,
                        'Best_Brut_Associe': clean_float(best_r_row['Prix Brut']),
                        'Best_Price_Net': best_p_row['PU_Systeme'],
                        'Price_At_Best_Remise': best_r_row['PU_Systeme'],
                        'Date_Remise': accord['date'] if (accord and accord['type'] == "CONTRAT") else best_r_row['Date'],
                        'Date_Price': best_p_row['Date'],
                        'Alerte_Prix_Force': alerte_prix_force,
                        'Derniere_Commande_Mois': derniere_commande_mois
                    }
            
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
                prix_historique_ref = 0.
                detail_tech = ""
                remise_cible_str = "-" 
                is_cuivre = False
                
                # FRAIS GESTION
                if row['Famille'] == "FRAIS GESTION":
                    if row['Montant'] > max_gestion:
                        perte = row['Montant'] - max_gestion
                        cible = max_gestion
                        motif = "Frais Facturation Abusifs"
                        detail_tech = f"(Max autorisé: {max_gestion}€)"
                
                # FRAIS PORT
                elif row['Famille'] == "FRAIS PORT":
                    total_fac = facture_totals.get(f_name, 0)
                    if total_fac >= seuil_franco:
                        perte = row['Montant']
                        motif = "Port facturé malgré Franco"
                        cible = 0.0
                        detail_tech = f"(Total Facture: {total_fac:.2f}€ > Franco: {seuil_franco}€)"
                        remise_cible_str = "100%"

                # PRODUITS
                else:
                    art = row['Article']
                    remise_actuelle = clean_float(str(row['Remise']).replace('%', ''))
                    pu_paye = row['PU_Systeme']
                    brut_actuel = clean_float(row['Prix Brut'])
                    is_cuivre = detecter_famille_cuivre(art, row['Désignation'])
                    
                    if art in ref_map and art != 'SANS_REF':
                        m = ref_map[art]
                        accord = registre.get(art)
                        
                        # CAS 1 : CONTRAT FORCÉ
                        if accord and accord['type'] == "CONTRAT":
                            remise_contractuelle = accord['valeur']
                            if remise_actuelle < remise_contractuelle - 0.5:
                                if brut_actuel > 0:
                                    prix_contractuel = brut_actuel * (1 - remise_contractuelle/100)
                                    if pu_paye > prix_contractuel + 0.01:
                                        perte = (pu_paye - prix_contractuel) * row['Quantité']
                                        cible = prix_contractuel
                                        motif = f"Remise {remise_actuelle}% < Contrat {remise_contractuelle}%"
                                        remise_cible_str = f"{remise_contractuelle}%"
                                        detail_tech = f"(Contrat du {accord['date']})"
                                        prix_historique_ref = cible
                        
                        # CAS 2 : PROMO MARQUÉE
                        elif accord and accord['type'] == "PROMO":
                            perte = 0
                        
                        # CAS 3 : ANALYSE AUTOMATIQUE
                        else:
                            best_pu = m['Best_Price_Net']
                            
                            # Si le prix payé est supérieur au meilleur prix connu
                            if pu_paye > best_pu + 0.01:
                                perte = (pu_paye - best_pu) * row['Quantité']
                                cible = best_pu
                                
                                # Calcul de l'écart en %
                                ecart_pct = ((pu_paye / best_pu) - 1) * 100 if best_pu > 0 else 0
                                
                                if remise_actuelle < m['Best_Remise'] - 0.5:
                                    motif = f"Remise {remise_actuelle}% < Historique {m['Best_Remise']}%"
                                    remise_cible_str = f"{m['Best_Remise']:g}%"
                                else:
                                    motif = f"Prix +{ecart_pct:.1f}% vs meilleur historique"
                                    remise_cible_str = f"{m['Best_Remise']:g}%"
                                
                                source_cible = m['Date_Price']
                                prix_historique_ref = best_pu
                                
                                if is_cuivre:
                                    detail_tech = f"(CUIVRE - Tolérance ±30%)"
                                    seuil = calculer_seuil_tolerance(True)
                                    if pu_paye <= best_pu * seuil:
                                        perte = 0  # Dans la tolérance cuivre

                if perte > 0.01:
                    prix_brut_affiche = row['Prix Brut']
                    try:
                        val_float = float(str(prix_brut_affiche).replace(' ', '').replace(',', '.'))
                        prix_brut_affiche = f"{val_float:.2f}"
                    except: pass
                    
                    if remise_cible_str == "-" and row['Famille'] not in ["FRAIS GESTION", "FRAIS PORT"]:
                         remise_cible_str = "?"

                    anomalies.append({
                        "Fichier_Source": f_name,
                        "Fournisseur": fourn,                        
                        "Num Facture": row['Facture'],
                        "Ref_Cmd": row['Ref_Cmd'], 
                        "BL": row['BL'], 
                        "Famille": row['Famille'],
                        "PU_Systeme": row['PU_Systeme'],
                        "Montant": row['Montant'],
                        "Prix Brut": prix_brut_affiche,
                        "Remise": row['Remise'],
                        "Remise Cible": remise_cible_str,
                        "Qte": row['Quantité'],
                        "Ref": row['Article'],
                        "Désignation": row['Désignation'],
                        "Payé (U)": row['PU_Systeme'],
                        "Cible (U)": cible,
                        "Prix Cible": f"{cible:.4f} €",
                        "Perte": perte,                        
                        "Prix_Ref_Hist": prix_historique_ref,
                        "Motif": motif,
                        "Date Facture": row['Date'],
                        "Source Cible": source_cible,     
                        "Détails Techniques": detail_tech
                    })
            
            if anomalies:
                df_ano = pd.DataFrame(anomalies)
                total_perte = df_ano['Perte'].sum()
                
                st.subheader("🏆 Podium des Dettes & Évolution")
                
                df_ventes = df.copy()
                df_ventes['Date_DT'] = pd.to_datetime(df_ventes['Date'], errors='coerce')
                df_ventes['Année'] = df_ventes['Date_DT'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')
                stats_ventes = df_ventes.groupby(['Fournisseur', 'Année'])['Montant'].sum().reset_index()

                df_ano['Date_DT'] = pd.to_datetime(df_ano['Date Facture'], errors='coerce')
                df_ano['Année'] = df_ano['Date_DT'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')
                stats_pertes = df_ano.groupby(['Fournisseur', 'Année'])['Perte'].sum().reset_index()

                merge_stats = pd.merge(stats_ventes, stats_pertes, on=['Fournisseur', 'Année'], how='left').fillna(0)
                merge_stats['Taux'] = merge_stats.apply(lambda x: (x['Perte'] / x['Montant'] * 100) if x['Montant'] > 0 else 0, axis=1)
                
                merge_stats['Affiche'] = merge_stats.apply(
                    lambda x: f"{x['Perte']:.2f} € ({x['Taux']:.1f}%)" if x['Perte'] > 0.01 else "-", 
                    axis=1
                )

                pivot_combo = merge_stats.pivot(index='Fournisseur', columns='Année', values='Affiche').fillna("-")
                
                total_dette_fourn = df_ano.groupby('Fournisseur')['Perte'].sum()
                pivot_combo["Dette Totale (€)"] = total_dette_fourn
                
                pivot_combo = pivot_combo.sort_values("Dette Totale (€)", ascending=False)

                row_total = {"Dette Totale (€)": total_perte}
                
                cols_annee = [c for c in pivot_combo.columns if c != "Dette Totale (€)"]
                for c_annee in cols_annee:
                    sub = merge_stats[merge_stats['Année'] == c_annee]
                    sum_p = sub['Perte'].sum()
                    sum_m = sub['Montant'].sum()
                    
                    if sum_m > 0:
                        t_glo = (sum_p / sum_m) * 100
                        row_total[c_annee] = f"{sum_p:.2f} € ({t_glo:.1f}%)"
                    elif sum_p > 0:
                         row_total[c_annee] = f"{sum_p:.2f} € (-)"
                    else:
                         row_total[c_annee] = "-"

                df_total_row = pd.DataFrame([row_total], index=["TOTAL GÉNÉRAL"])
                pivot_combo = pd.concat([pivot_combo, df_total_row])

                pivot_combo.index.name = None
                pivot_combo.columns.name = None
                
                html_podium = pivot_combo.style.format({'Dette Totale (€)': "{:.2f} €"})\
                .set_properties(**{
                    'text-align': 'center', 
                    'border': '2px solid black', 
                    'color': 'black', 
                    'font-weight': 'bold',
                    'white-space': 'pre-wrap'
                })\
                .set_table_styles([
                    {'selector': 'th', 'props': [('background-color', '#ffcccb'), ('color', 'black'), ('text-align', 'center'), ('border', '2px solid black')]},
                    {'selector': 'table', 'props': [('border-collapse', 'collapse'), ('width', '100%')]}
                ]).to_html()
                
                st.markdown(html_podium, unsafe_allow_html=True)
                
                st.divider()
                
                liste_fichiers_avec_erreurs = sorted(df_ano['Fichier_Source'].unique().tolist(), reverse=True)
                
                choix_affichage = st.selectbox(
                    "👁️ Filtrer les détails ci-dessous par facture :", 
                    ["TOUT LE DOSSIER (GLOBAL)"] + liste_fichiers_avec_erreurs
                )
                
                st.subheader("🕵️ Détails par Fournisseur")
        
                for fourn_nom in pivot_combo.index:
                    if fourn_nom == "TOTAL GÉNÉRAL": continue
                    
                    fourn_dette = total_dette_fourn.get(fourn_nom, 0)
                    
                    with st.expander(f"📂 {fourn_nom} - Dette : {fourn_dette:.2f} €", expanded=False):
                        df_litiges_fourn = df_ano[df_ano['Fournisseur'] == fourn_nom]
                        
                        if choix_affichage != "TOUT LE DOSSIER (GLOBAL)":
                            df_litiges_fourn = df_litiges_fourn[df_litiges_fourn['Fichier_Source'] == choix_affichage]
                        
                        if df_litiges_fourn.empty:
                            st.info(f"✅ Aucune erreur sur la facture {choix_affichage} pour ce fournisseur.")
                            continue
                        
                        for article, group in df_litiges_fourn.groupby('Ref'):
                            source_brute = group['Source Cible'].iloc[0]
                            date_ref = source_brute if source_brute != "-" else group['Date Facture'].iloc[0]
                            remise_ref = group['Remise Cible'].iloc[0]
                            nom_art = group['Désignation'].iloc[0]
                            
                            # Récupération des vrais meilleurs/pires prix depuis ref_map
                            if article in ref_map:
                                prix_min = ref_map[article]['Best_Price_Net']
                                date_min = ref_map[article]['Date_Price']
                                remise_min = f"{ref_map[article]['Best_Remise']:.4g}%"
                            else:
                                prix_min = group['Payé (U)'].min()
                                date_min = group[group['Payé (U)'] == prix_min]['Date Facture'].iloc[0]
                                remise_min = group[group['Payé (U)'] == prix_min]['Remise'].iloc[0]
                            
                            prix_actuel = group['Payé (U)'].iloc[-1]
                            date_actuel = group['Date Facture'].iloc[-1]
                            remise_actuelle = group['Remise'].iloc[-1]
                            
                            ecart_euros = prix_actuel - prix_min
                            ecart_pct = ((prix_actuel / prix_min) - 1) * 100 if prix_min > 0 else 0
                            
                            remise_min_txt = remise_min if remise_min != "-" else "0%"
                            remise_actuelle_txt = remise_actuelle if remise_actuelle != "-" else "0%"
                            
                            badge_alerte = ""
                            couleur_box = "#f0f0f0"
                            if article in ref_map:
                                alerte = ref_map[article].get('Alerte_Prix_Force')
                                if alerte == "PROMO_OK":
                                    badge_alerte = "🟢 Promo légitime détectée"
                                    couleur_box = "#d4edda"
                                elif alerte == "SUSPECT":
                                    badge_alerte = "🔴 ALERTE : Prix sans remise suspect"
                                    couleur_box = "#f8d7da"
                                elif alerte == "ANCIEN":
                                    mois = ref_map[article].get('Derniere_Commande_Mois', 0)
                                    badge_alerte = f"🟠 À vérifier : Dernière commande il y a {mois} mois"
                                    couleur_box = "#fff3cd"
                            
                            st.markdown(f"### 📦 {article} - {nom_art}")
                            if badge_alerte:
                                st.markdown(f"**{badge_alerte}**")
                            
                            st.markdown(f"""
                            <div style="background-color: {couleur_box}; padding: 15px; border-radius: 10px; border: 2px solid #333; margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0;">🏆 MEILLEUR PRIX HISTORIQUE</h4>
                                <p style="font-size: 24px; font-weight: bold; margin: 5px 0; color: #28a745;">
                                    {prix_min:.4f} € <span style="font-size: 14px; color: #666;">📅 {date_min}</span>
                                </p>
                                <p style="font-size: 16px; margin: 5px 0; color: #666;">
                                    ✨ Remise obtenue : <strong>{remise_min_txt}</strong>
                                </p>
                                <hr style="margin: 15px 0; border: 1px solid #ccc;">
                                <h4 style="margin: 10px 0;">📊 PRIX ACTUEL</h4>
                                <p style="font-size: 20px; font-weight: bold; margin: 5px 0; color: {'#dc3545' if ecart_euros > 0.10 else '#28a745'};">
                                    {prix_actuel:.4f} € <span style="font-size: 14px; color: #666;">📅 {date_actuel}</span>
                                </p>
                                <p style="font-size: 16px; margin: 5px 0; color: #666;">
                                    💰 Remise actuelle : <strong>{remise_actuelle_txt}</strong>
                                </p>
                                {'<p style="margin: 10px 0; font-weight: bold; color: #dc3545;">⚠️ Tu payes ' + f'{ecart_euros:.2f}€ de PLUS ({ecart_pct:.1f}%)</p>' if ecart_euros > 0.10 else '<p style="margin: 10px 0; font-weight: bold; color: #28a745;">✅ Prix stable ou en baisse</p>'}
                            </div>
                            """, unsafe_allow_html=True)
                            
                            st.markdown("**🎯 ACTION REQUISE :**")
                            c_bt1, c_bt2, c_bt3 = st.columns(3)
                            cle_unique = f"{fourn_nom}_{article}".replace(" ", "_")
                            
                            accord_existant = registre.get(article)
                            
                            with c_bt1:
                                if accord_existant and accord_existant['type'] == "CONTRAT":
                                    st.write(f"🔒 Contrat : **{accord_existant['valeur']}{accord_existant['unite']}**")
                                    col_mod_input, col_mod_btn = st.columns([2, 3])
                                    with col_mod_input:
                                        nouvelle_remise_val = st.number_input(
                                            label="Modif",
                                            value=float(accord_existant['valeur']),
                                            step=0.5,
                                            format="%.2f",
                                            key=f"input_mod_{cle_unique}",
                                            label_visibility="collapsed"
                                        )
                                    with col_mod_btn:
                                        if st.button(f"💾 Valider {nouvelle_remise_val}%", key=f"btn_mod_{cle_unique}"):
                                            sauvegarder_accord(article, "CONTRAT", nouvelle_remise_val, "%", nom_art, fourn_nom, "")
                                            st.rerun()
                                else:
                                    if st.button(f"🚀 Contrat ({remise_ref})", key=f"v_{cle_unique}", use_container_width=True):
                                        sauvegarder_accord(article, "CONTRAT", clean_float(remise_ref.replace('%','')), "%", nom_art, fourn_nom, "")
                                        st.rerun()
                            
                            with c_bt2:
                                val_promo_sql = prix_min
                                if st.button("🟢 C'était une PROMO", key=f"p_{cle_unique}", use_container_width=True):
                                    sauvegarder_accord(article, "PROMO", val_promo_sql, "EUR", nom_art, fourn_nom, "")
                                    st.rerun()
                            
                            with c_bt3:
                                if st.button("⚪ IGNORER", key=f"e_{cle_unique}", use_container_width=True):
                                    sauvegarder_accord(article, "ERREUR", 0, "EUR", nom_art, fourn_nom, "")
                                    st.rerun()
                            
                            with st.expander("📋 Voir l'historique complet des achats"):
                                sub_df = group[['Num Facture', 'Date Facture', 'Qte', 'Remise', 'Payé (U)', 'Perte', 'Prix Cible']]
                                st.dataframe(
                                    sub_df,
                                    hide_index=True,
                                    use_container_width=True,
                                    column_config={
                                        "Qte": st.column_config.NumberColumn("Qte", format="%d"),
                                        "Payé (U)": st.column_config.NumberColumn("Payé (U)", format="%.4f €"),
                                        "Perte": st.column_config.NumberColumn("Perte", format="%.2f €")
                                    }
                                )
                            
                            st.markdown("---")
            else:
                st.success("✅ Aucune anomalie détectée ! Tout est conforme.")
                    

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
                    st.session_state['uploader_key'] += 1
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

        with col_drop:
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

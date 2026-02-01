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
def generer_pdf_facture(num_facture, date_facture, fournisseur, anomalies_facture, total_perte, nom_entreprise="SARL CEDRIC MATHIEU"):
    """Louis : Génère un PDF propre pour envoyer au commercial"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from io import BytesIO
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    story = []
    
    # Style personnalisé
    style_titre = ParagraphStyle('Titre', parent=styles['Title'], fontSize=16, spaceAfter=6)
    style_sous = ParagraphStyle('Sous', parent=styles['Normal'], fontSize=10, textColor=colors.grey)
    style_cell = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, leading=10)
    
    # EN-TETE
    story.append(Paragraph(nom_entreprise, style_titre))
    story.append(Paragraph("RAPPORT D'ANOMALIES TARIFAIRES", styles['Heading2']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Fournisseur : <b>{fournisseur}</b>", styles['Normal']))
    story.append(Paragraph(f"Facture : <b>{num_facture}</b> du <b>{date_facture}</b>", styles['Normal']))
    story.append(Spacer(1, 12))
    
    # TABLEAU
    is_global = num_facture == "GLOBAL"
    
    if is_global:
        header = ['Facture', 'Date', 'Article', 'Designation', 'Qte', 'Brut', 'Remise', 'Paye', 'Cible', 'Perte']
    else:
        header = ['Article', 'Designation', 'Qte', 'Brut', 'Remise', 'Paye', 'Cible', 'Perte']
    data_table = [header]
    
    for a in anomalies_facture:
        row_data = []
        if is_global:
            row_data.append(Paragraph(str(a.get('Facture', '')), style_cell))
            row_data.append(str(a.get('Date', ''))[:10])
        row_data.extend([
            str(a.get('Article', '')),
            Paragraph(str(a.get('Designation', ''))[:40], style_cell),
            str(a.get('Qte', '')),
            f"{clean_float(str(a.get('Prix Brut', 0))):.2f}",
            str(a.get('Remise', '')),
            f"{clean_float(str(a.get('Payé (U)', 0))):.2f}",
            f"{clean_float(str(a.get('Prix Cible', 0))):.2f}",
            f"{clean_float(str(a.get('Perte', 0))):.2f}"
        ])
        data_table.append(row_data)
    
    # Ligne total
    nb_cols = len(header)
    total_row = [''] * (nb_cols - 2) + ['TOTAL', f"{total_perte:.2f}"]
    data_table.append(total_row)
    
    if is_global:
        col_widths = [60, 50, 45, 90, 22, 38, 38, 38, 38, 38]
    else:
        col_widths = [55, 120, 25, 45, 45, 45, 45, 45]
    t = Table(data_table, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f0f0f0')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e74c3c')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(t)
    
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"Dette totale sur cette facture : <b>{total_perte:.2f} EUR HT</b>", styles['Heading3']))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Document genere automatiquement - Audit tarifaire", style_sous))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def charger_registre(user_id=None):
    """Louis : On récupère l'accord, sa valeur et son unité (EUR ou %) depuis Supabase - CLOISONNÉ par user"""
    try:
        query = supabase.table("accords_commerciaux").select("*")
        if user_id:
            query = query.eq("user_id", user_id)
        res = query.execute()
        return {r['article']: {'type': r['type_accord'], 'valeur': r['valeur'], 'unite': r['unite'], 'date': r['date_maj']} for r in res.data}
    except:
        return {}

def sauvegarder_accord(article, type_accord, valeur, unite="EUR", user_id=None):
    """Louis : On enregistre la valeur ET l'unité - CLOISONNÉ par user"""
    try:
        supabase.table("accords_commerciaux").upsert({
            "article": article,
            "type_accord": type_accord,
            "valeur": valeur,
            "unite": unite,
            "user_id": user_id,
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
    """Convertit '60+10' en 64 (float) et nettoie le format"""
    if not isinstance(val_str, str): return 0.0
    # Nettoyage de base
    val_str = val_str.replace('%', '').replace(' ', '').replace('EUR', '').replace(',', '.')
    
    if not val_str: return 0.0
    
    try:
        # Gestion des remises cumulées (ex: 60+10)
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
    
    # 1. TAXES (Priorité absolue)
    mots_taxes = ["ENERG", "TAXE", "CONTRIBUTION", "DEEE", "SORECOP", "ECO-PART", "ECO "]
    if any(x in label_up for x in mots_taxes) or any(x in ref_up for x in mots_taxes): 
        return "TAXE"

    # 2. FRAIS DE GESTION (C'est ici qu'on attrape le FF et le FRAIS_ANNEXE)
    if "FRAIS_ANNEXE" in ref_up:
        desig_up = label_up
        if any(x in desig_up for x in ["DEEE", "ECO", "RECYCL", "SORECOP"]):
            return "TAXE"
        return "FRAIS GESTION"
    
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

def prompt_avoir():
    """Louis : Prompt dédié pour les avoirs (retours + corrections prix)"""
    return """
    Analyse cette facture d'AVOIR et extrais les données structurées.
    
    IMPORTANT : Identifie le TYPE d'avoir :
    - "RETOUR" : Simple retour de marchandise (pas de "Prix avant" / "Prix après")
    - "CORRECTION" : Révision de prix (contient "Prix avant" et "Prix après")
    
    RÈGLES :
    - Si c'est un RETOUR : extrais les lignes normalement avec montants négatifs
    - Si c'est une CORRECTION : pour chaque ligne extrais le "Prix après" (nouvelle remise)
    - Le champ "type_avoir" doit être "RETOUR" ou "CORRECTION"
    - Le champ "facture_origine" = le numéro de facture corrigée si visible
    
    JSON ATTENDU :
    {
        "fournisseur": "...",
        "adresse_fournisseur": "...",
        "tva_fournisseur": "...",
        "iban": "...",
        "date": "2025-01-01",
        "num_facture": "...",
        "type_avoir": "RETOUR ou CORRECTION",
        "facture_origine": "...",
        "lignes": [
            {
                "quantite": 1,
                "article": "...",
                "designation": "...",
                "prix_brut_unitaire": 0.0,
                "base_facturation": 1,
                "remise_avant": "...",
                "remise_apres": "...",
                "prix_net_avant": 0.0,
                "prix_net_apres": 0.0,
                "montant": 0.0
            }
        ]
    }
    """

def traiter_un_fichier(nom_fichier, user_id):
    try:
        path_storage = f"{user_id}/{nom_fichier}"
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        # [MODIFICATION] : Passage à Gemini 3.0 Flash-preview (Stable & mais lent) la version 2 est trop pourrier pour le test
        # On remplace la version "3-preview" qui lag par la référence de vitesse actuelle.
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
             * prix_brut_unitaire : Le prix catalogue affiché AVANT toute division.
             * base_facturation : Si le prix est pour 100 ou 1000 unités (ex: câbles), note le nombre (100, 1000). Sinon mets 1.
             * remise : Le pourcentage de remise (ex: "60+10" ou "70").
             * prix_net_unitaire : Le prix payé unitaire affiché AVANT toute division.
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
                    "prix_brut_unitaire": 0.0,
                    "base_facturation": 1,
                    "remise": "...",
                    "prix_net_unitaire": 0.0,
                    "montant": 0.0,
                    "num_bl_ligne": "..."
                }
            ]
        }
        """
        
        # Louis : On fait un premier appel rapide pour voir si c'est un avoir
        # Si le nom du fichier contient un montant négatif (ex: -27_04) c'est probablement un avoir
        is_avoir = bool(re.search(r"-\d", nom_fichier))
        
        if is_avoir:
            res = model.generate_content([prompt_avoir(), {"mime_type": "application/pdf", "data": file_data}])
        else:
            res = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        if not res.text: return False, "Vide"
        
        data_json = extraire_json_robuste(res.text)
        if not data_json: return False, "JSON Invalide"
        
        # Louis : Si c'est un avoir CORRECTION, on transforme les lignes
        # pour que le "prix après" devienne la référence dans le système
        if data_json.get('type_avoir') == "CORRECTION":
            lignes_converties = []
            for l in data_json.get('lignes', []):
                lignes_converties.append({
                    "quantite": abs(l.get('quantite', 1)),
                    "article": l.get('article', ''),
                    "designation": l.get('designation', ''),
                    "prix_brut_unitaire": l.get('prix_brut_unitaire', 0),
                    "base_facturation": l.get('base_facturation', 1),
                    "remise": l.get('remise_apres', l.get('remise', '0')),
                    "prix_net_unitaire": l.get('prix_net_apres', l.get('prix_net_unitaire', 0)),
                    "montant": abs(l.get('prix_net_apres', 0)) * abs(l.get('quantite', 1)),
                    "num_bl_ligne": "AVOIR_CORRECTION"
                })
            data_json['lignes'] = lignes_converties
            data_json['ref_commande'] = data_json.get('facture_origine', '-')
        
        # --- CORRECTIF : Si Facture = Commande, on efface ! ---
        n_fac = data_json.get('num_facture', '').strip()
        n_cmd = data_json.get('ref_commande', '').strip()
        
        if n_fac and n_cmd and (n_fac in n_cmd or n_cmd in n_fac):
             data_json['ref_commande'] = "-"
        # ------------------------------------------------------

        # --- PATCH MANUEL : On repasse derrière l'IA pour les cas tordus ---
        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "user_id": user_id,
            "analyse_complete": json.dumps(data_json),
            "raw_text": res.text
       }, on_conflict="file_name,user_id").execute()
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
        # Louis : On interroge Supabase pour récupérer tes factures
        res_db = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
        # Louis : On prépare les données pour l'affichage (ne pas supprimer ces deux lignes !)
        memoire_full = {r['file_name']: r for r in res_db.data}
        memoire = {r['file_name']: r['analyse_complete'] for r in res_db.data}
    except Exception as e: 
        # Louis : Si ton badge de sécurité a expiré (erreur JWT), on vide tout et on te reconnecte
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
                qte_ia = clean_float(l.get('quantite', 1))
                if qte_ia == 0: qte_ia = 1
                
                montant = clean_float(l.get('montant', 0))
                
                # --- NOUVELLE LOGIQUE UNIVERSELLE DE CALCUL ---
                base_fac = float(l.get('base_facturation', 1))
                if base_fac <= 0: base_fac = 1

                # Calcul du Net Réel
                p_net_lu = clean_float(l.get('prix_net_unitaire', l.get('prix_net', 0)))
                p_net = p_net_lu / base_fac
                
                # Sécurité Rétro-compatibilité (si l'IA a mis le slash dans l'ancien champ)
                raw_net = str(l.get('prix_net', '0'))
                if '/' in raw_net and base_fac == 1:
                    try: p_net = clean_float(raw_net.split('/')[0]) / float(raw_net.split('/')[1])
                    except: pass

                # Calcul du Brut Réel
                p_brut_lu = clean_float(l.get('prix_brut_unitaire', l.get('prix_brut', 0)))
                p_brut = p_brut_lu / base_fac

                if '/' in str(l.get('prix_brut', '')) and base_fac == 1:
                    try: p_brut = clean_float(str(l.get('prix_brut')).split('/')[0]) / float(str(l.get('prix_brut')).split('/')[1])
                    except: pass
                
                # On stocke le brut "propre" pour l'affichage
                raw_brut = f"{p_brut:.4f}"
                # ----------------------------------------------
                
                raw_remise = str(l.get('remise', '0'))
                val_remise = calculer_remise_combine(raw_remise)
                remise = f"{val_remise:g}%" if val_remise > 0 else "-"
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
            # --- DEBUT AJOUT : TABLEAU HTML (FORCE BRUTE POUR LE STYLE) ---
            st.subheader("📈 Synthèse des Achats par Année")
            
            # 1. Préparation
            df_calc = df.copy()
            df_calc['Date_Ref'] = pd.to_datetime(df_calc['Date'], errors='coerce')
            df_calc['Année'] = df_calc['Date_Ref'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')

            # 2. Pivot
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
                        # Entêtes (Th) en gris clair avec bordure noire
                        {'selector': 'th', 'props': [
                            ('background-color', '#e0e0e0'), 
                            ('color', 'black'), 
                            ('text-align', 'center'), 
                            ('border', '2px solid black'),
                            ('font-size', '16px')
                        ]},
                        # Le tableau global
                        {'selector': 'table', 'props': [
                            ('border-collapse', 'collapse'),
                            ('width', '100%')
                        ]}
                    ]).to_html()
                
                # Injection du HTML
                st.markdown(html_code, unsafe_allow_html=True)
                st.divider()
                # --- FIN AJOUT ---

            df_produits = df[~df['Famille'].isin(['FRAIS PORT', 'FRAIS GESTION', 'TAXE'])]
            ref_map = {}
            registre = charger_registre(user_id)
            
            if not df_produits.empty:
                df_clean = df_produits[df_produits['Article'] != 'SANS_REF'].copy()
                df_clean['Remise_Val'] = df_clean['Remise'].apply(lambda x: clean_float(str(x).replace('%', '')))
                
                for art, group in df_clean.groupby('Article'):
                    # On vérifie si on a déjà pris une décision sur cet article
                    accord = registre.get(art)
                    
                    # Logique de sélection des records
                    valid_remises = group[group['Remise_Val'] > 0].sort_values('Remise_Val', ascending=False)
                    valid_prices = group[group['PU_Systeme'] > 0.01].sort_values('PU_Systeme', ascending=True) # <--- LIGNE DE REPERE AVANT

                    # --- CORRECTION PROMO ---
                    # Louis : C'est ici qu'on résout le bug. Si tu marques un article comme "PROMO",
                    # on identifie le prix de cette promo (le moins cher de la liste).
                    # Ensuite, on dit au programme d'ignorer TOUTES les factures qui ont ce prix promo.
                    # De cette façon, il va chercher le prix suivant (ton prix normal à 129 €) pour calculer la perte.
                    idx_r, idx_p = 0, 0
                    if accord and accord['type'] == "PROMO":
                        if not valid_prices.empty:
                            prix_promo = valid_prices.iloc[0]['PU_Systeme']
                            # On filtre : on ne garde que les factures dont le prix est différent de la promo
                            valid_prices = valid_prices[abs(valid_prices['PU_Systeme'] - prix_promo) > 0.10]
                            valid_remises = valid_remises[abs(valid_remises['PU_Systeme'] - prix_promo) > 0.10]
                    # -------------------------

                    best_r_row = valid_remises.iloc[idx_r] if not valid_remises.empty else group.iloc[0] # <--- LIGNE DE REPERE APRES
                    best_p_row = valid_prices.iloc[idx_p] if not valid_prices.empty else group.iloc[0]

                    # Si c'est un CONTRAT forcé, on écrase la remise par celle du registre
                    remise_finale = accord['valeur'] if (accord and accord['type'] == "CONTRAT") else best_r_row['Remise_Val']

                    # --- CORRECTION LOGIQUE "PRIX NET" vs "PRIX BRUT" ---
                    # Si le meilleur prix est un "Net" (0 remise) et qu'il est meilleur que le prix remisé habituel
                    # Alors on recalcule la remise théorique en utilisant le Brut du prix remisé.
                    p_net_record = best_p_row['PU_Systeme']
                    p_net_standard = best_r_row['PU_Systeme']
                    
                    if p_net_record < (p_net_standard - 0.05) and best_p_row['Remise_Val'] == 0:
                        # Louis : Le meilleur prix a remise 0% (prix forcé type 19.58€)
                        # On cherche le VRAI brut sur les autres factures qui ONT une remise
                        lignes_avec_remise = group[group['Remise_Val'] > 0]
                        if not lignes_avec_remise.empty:
                            brut_ref = clean_float(lignes_avec_remise.iloc[0]['Prix Brut'])
                        else:
                            brut_ref = clean_float(best_r_row['Prix Brut'])
                        if brut_ref > 0:
                            # Calcul inverse : Quelle remise donne ce prix net sur ce brut ?
                            taux_virtuel = (1 - (p_net_record / brut_ref)) * 100
                            remise_finale = round(taux_virtuel, 2)                  
                    

                    # Louis : On cherche le brut le plus bas, mais on accepte +5% d'une année à l'autre
                    # Comme ça dans 3 ans on a toujours un brut réf à jour
                    tous_bruts = group[group['PU_Systeme'] > 0.01].copy()
                    tous_bruts['Brut_Float'] = tous_bruts['Prix Brut'].apply(clean_float)
                    tous_bruts['Annee'] = pd.to_datetime(tous_bruts['Date'], errors='coerce').dt.year
                    # Louis : On exclut les prix forcés (remise 0% où brut = net) car c'est pas le vrai brut catalogue
                    bruts_valides = tous_bruts[(tous_bruts['Brut_Float'] > 0) & (tous_bruts['Remise_Val'] > 0)].sort_values('Annee')
                    # Si aucune ligne avec remise, on garde tout comme avant
                    if bruts_valides.empty:
                        bruts_valides = tous_bruts[tous_bruts['Brut_Float'] > 0].sort_values('Annee')
                    
                    if not bruts_valides.empty:
                        brut_le_plus_bas = bruts_valides.iloc[0]['Brut_Float']
                        for _, row_b in bruts_valides.iterrows():
                            hausse = ((row_b['Brut_Float'] / brut_le_plus_bas) - 1) * 100
                            if hausse <= 5:
                                brut_le_plus_bas = row_b['Brut_Float']
                    else:
                        brut_le_plus_bas = 0

                    ref_map[art] = {
                        'Best_Remise': remise_finale,
                        'Best_Brut_Associe': brut_le_plus_bas if brut_le_plus_bas > 0 else clean_float(best_r_row['Prix Brut']),
                        # Louis : Le brut du jour où on a eu le meilleur prix net (pour prouver le gonflage)
                        'Brut_Du_Best_Price': clean_float(best_p_row['Prix Brut']),
                        'Best_Price_Net': best_p_row['PU_Systeme'],
                        'Price_At_Best_Remise': best_r_row['PU_Systeme'],
                        'Date_Remise': accord['date'] if (accord and accord['type'] == "CONTRAT") else best_r_row['Date'],
                        'Date_Price': best_p_row['Date']
                    }

                    # --- MODIFICATION : ON COMMENTE TOUT POUR ARRETER LE LAG ---
                    # if remise_finale > 0:
                    #     try:
                    #         supabase.table("market_rates").upsert({
                    #             "user_id": user_id,
                    #             "article": art,
                    #             "fournisseur": best_r_row['Fournisseur'],
                    #             "remise": remise_finale,
                    #             "date_ref": best_r_row['Date']
                    #         }).execute()
                    #     except: pass
                    # -----------------------------------------------------------
            
            facture_totals = df.groupby('Fichier')['Montant'].sum().to_dict()
            
            # Louis : On récupère les factures corrigées par un avoir CORRECTION
            # Pour ne pas compter la perte 2 fois (facture originale + avoir)
            articles_corriges = {}  # {num_facture_origine: [liste d'articles corrigés]}
            for f_name_av, json_str_av in memoire.items():
                try:
                    data_av = json.loads(json_str_av)
                    if data_av.get('type_avoir') == "CORRECTION":
                        fac_orig = data_av.get('facture_origine', '')
                        if fac_orig:
                            if fac_orig not in articles_corriges:
                                articles_corriges[fac_orig] = []
                            for l_av in data_av.get('lignes', []):
                                articles_corriges[fac_orig].append(l_av.get('article', ''))
                except:
                    continue
            
            anomalies = []

            for idx, row in df.iterrows():
                f_name = row['Fichier']
                num_facture = row['Facture']
                fourn = row['Fournisseur']
                
                # Louis : Si cette ligne a été corrigée par un avoir, on la saute
                if num_facture in articles_corriges and row['Article'] in articles_corriges[num_facture]:
                    continue
                
                rules = config_dict.get(fourn, {"Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
                seuil_franco = rules.get("Franco (Seuil €)", 0.0)
                max_gestion = rules.get("Max Gestion (€)", 0.0)
                
                perte = 0
                motif = ""
                cible = 0.0                 
                source_cible = "-"
                # Louis : On crée une variable vide au début de chaque ligne.
                # Elle servira à stocker le "Vrai Prix Historique" si on en trouve un.
                prix_historique_ref = 0.
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

                # --- LOGIQUE HYBRIDE V3 : LE NET EST JUGE ---
                else:
                    art = row['Article']
                    remise_actuelle = clean_float(str(row['Remise']).replace('%', ''))
                    pu_paye = row['PU_Systeme']
                    
                    if art in ref_map and art != 'SANS_REF':
                        m = ref_map[art]

# --- AJOUT SPECIAL LOUIS : RECUPERATION DU PRIX ---
                        # Louis : C'est ICI qu'on va chercher l'info dans le "Cerveau" (ref_map).
                        # On lui dit : "Ressors-moi le prix net en Euros qui correspond à la meilleure remise qu'on a jamais eue".
                        # Comme ça, on a le VRAI chiffre (56.75€) et pas un calcul théorique foireux.
                        prix_historique_ref = m['Price_At_Best_Remise']
                        
                        # REGLE 1 : SECURITE ABSOLUE (Berner)
                        # Si on paye le prix record ou moins, perte = 0
                        if pu_paye <= m['Best_Price_Net'] + 0.05:
                            perte = 0
                        
                        # REGLE 2 : RESPECT DE LA REMISE (Thermor)
                        elif m['Best_Remise'] > 0 and remise_actuelle >= m['Best_Remise'] - 0.1:
                            perte = 0
                        
                        # REGLE 2.5 : TOLERANCE HAUSSE ANNUELLE
                        # Si même remise (±0.5 point) et brut augmente de max 5%, on tolère
                        elif m['Best_Remise'] > 0 and abs(remise_actuelle - m['Best_Remise']) <= 0.5:
                            brut_actuel = clean_float(row['Prix Brut'])
                            brut_ref = m['Best_Brut_Associe']
                            if brut_ref > 0:
                                hausse_brut = ((brut_actuel / brut_ref) - 1) * 100
                                if 0 <= hausse_brut <= 5:
                                    perte = 0
                                               
                           
                        # REGLE 3 : CALCUL DE LA PERTE
                        else:
                            # On cherche la meilleure cible possible entre le prix record et la remise théorique
                            cible_remise = 999999.0
                            brut_facture = clean_float(row['Prix Brut'])
                            if m['Best_Brut_Associe'] > 0:
                                # Louis : Si le brut facture est quasi = au prix net (prix forcé, remise 0%)
                                # alors on utilise le brut réf pour calculer la cible, pas le brut bidon
                                remise_ligne = clean_float(str(row['Remise']).replace('%', ''))
                                if remise_ligne == 0 and brut_facture > 0:
                                    cible_remise = m['Best_Brut_Associe'] * (1 - m['Best_Remise']/100)
                                else:
                                    cible_remise = brut_facture * (1 - m['Best_Remise']/100)
                                    if (brut_facture / m['Best_Brut_Associe']) < 0.5:
                                        cible_remise = m['Best_Brut_Associe'] * (1 - m['Best_Remise']/100)
                            
                            cible = min(m['Best_Price_Net'], cible_remise)
                            
                            if pu_paye > cible + 0.05:
                                perte = (pu_paye - cible) * row['Quantité']
                                motif = "Hausse de prix"
                                source_cible = m['Date_Price'] if m['Best_Price_Net'] < cible_remise else m['Date_Remise']
                                remise_cible_str = f"{m['Best_Remise']:g}%"

                # Seuil 3% : on ignore le bruit (arrondis, écotaxe) - SAUF frais et port
                if row['Famille'] in ["FRAIS GESTION", "FRAIS PORT"]:
                    filtre_ok = perte > 0.01
                else:
                    ecart_pourcent = (perte / (cible * row['Quantité'])) * 100 if (cible > 0 and row['Quantité'] > 0) else 0
                    filtre_ok = perte > 0.01 and ecart_pourcent >= 3
                if filtre_ok:
                    # --- Nettoyage Affichage Prix Brut ---
                    prix_brut_affiche = row['Prix Brut']
                    try:
                        val_float = float(str(prix_brut_affiche).replace(' ', '').replace(',', '.'))
                        prix_brut_affiche = f"{val_float:.2f}"
                    except: pass
                    
                    if remise_cible_str == "-" and row['Famille'] not in ["FRAIS GESTION", "FRAIS PORT"]:
                         remise_cible_str = "?"

                    anomalies.append({
                        "Fichier_Source": f_name, # Pour le filtre d'affichage
                        "Fournisseur": fourn,                        
                        "Num Facture": row['Facture'],
                        "Ref_Cmd": row['Ref_Cmd'], 
                        "BL": row['BL'], 
                        "Famille": row['Famille'],
                        "PU_Systeme": row['PU_Systeme'],
                        "Montant": row['Montant'],
                        "Prix Brut": prix_brut_affiche,
                        "Brut Réf": ref_map[row['Article']]['Best_Brut_Associe'] if row['Article'] in ref_map else 0,
                        "Remise": row['Remise'],
                        "Remise Cible": remise_cible_str, # 4. AFFICHAGE (Corrigé)
                        "Qte": row['Quantité'],
                        "Ref": row['Article'],
                        "Désignation": row['Désignation'],
                        "Payé (U)": row['PU_Systeme'],
                        "Cible (U)": cible,
                        # On utilise 'remise_cible_str' car c'est la seule variable qui existe ici.
                        "Prix Cible": f"{(clean_float(str(row['Prix Brut'])) * (1 - clean_float(str(remise_cible_str).replace('%',''))/100)):.4f} €",
                        "Perte": perte,                        
# --- AJOUT SPECIAL LOUIS : ON MET L'INFO DANS LE TUYAU ---
                        # Louis : On ajoute une colonne invisible "Prix_Ref_Hist" dans les données.
                        # Elle sert juste à transporter le prix de 56.75€ jusqu'à l'affichage du titre plus bas.
                        "Prix_Ref_Hist": prix_historique_ref,
                        "Motif": motif,
                        "Date Facture": row['Date'],
                        "Source Cible": source_cible,     
                        # --- LIGNE DE REPÈRE AVANT ---
                        "Détails Techniques": detail_tech

                    })
            
            if anomalies:
                df_ano = pd.DataFrame(anomalies)
                total_perte = df_ano['Perte'].sum()
                # --- BLOC PODIUM : MONTANT + % ---
                st.subheader("🏆 Podium des Dettes & Évolution")
                
                # 1. Dénominateur : Ventes
                df_ventes = df.copy()
                df_ventes['Date_DT'] = pd.to_datetime(df_ventes['Date'], errors='coerce')
                df_ventes['Année'] = df_ventes['Date_DT'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')
                stats_ventes = df_ventes.groupby(['Fournisseur', 'Année'])['Montant'].sum().reset_index()

                # 2. Numérateur : Pertes
                df_ano['Date_DT'] = pd.to_datetime(df_ano['Date Facture'], errors='coerce')
                df_ano['Année'] = df_ano['Date_DT'].dt.year.fillna(0).astype(int).astype(str).replace('0', 'Inconnue')
                stats_pertes = df_ano.groupby(['Fournisseur', 'Année'])['Perte'].sum().reset_index()

                # 3. Fusion et Calcul
                merge_stats = pd.merge(stats_ventes, stats_pertes, on=['Fournisseur', 'Année'], how='left').fillna(0)
                merge_stats['Taux'] = merge_stats.apply(lambda x: (x['Perte'] / x['Montant'] * 100) if x['Montant'] > 0 else 0, axis=1)
                
                # Cellule "Combo" (Texte pour l'affichage)
                merge_stats['Affiche'] = merge_stats.apply(
                    lambda x: f"{x['Perte']:.2f} € ({x['Taux']:.1f}%)" if x['Perte'] > 0.01 else "-", 
                    axis=1
                )

                # 4. Pivot
                pivot_combo = merge_stats.pivot(index='Fournisseur', columns='Année', values='Affiche').fillna("-")
                
                # Ajout de la colonne Total (Floats pour le tri) à la FIN (Droite)
                total_dette_fourn = df_ano.groupby('Fournisseur')['Perte'].sum()
                pivot_combo["Dette Totale (€)"] = total_dette_fourn
                
                # On trie les fournisseurs par dette décroissante
                pivot_combo = pivot_combo.sort_values("Dette Totale (€)", ascending=False)

                # --- AJOUT LIGNE TOTAL GÉNÉRAL (BAS DE TABLEAU) ---
                row_total = {"Dette Totale (€)": total_perte}
                
                # Calcul des totaux par année (pour avoir les bons %)
                cols_annee = [c for c in pivot_combo.columns if c != "Dette Totale (€)"]
                for c_annee in cols_annee:
                    # On filtre les stats brutes pour l'année concernée
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

                # Insertion de la ligne TOTAL en bas
                df_total_row = pd.DataFrame([row_total], index=["TOTAL GÉNÉRAL"])
                pivot_combo = pd.concat([pivot_combo, df_total_row])

                # --- FINITION ---
                # Suppression des noms d'index parasites (Ligne rose)
                pivot_combo.index.name = None
                pivot_combo.columns.name = None
                
                # --- SUPPRESSION DU DOUBLE AFFICHAGE (st.metric retiré) ---
                # On affiche directement le tableau HTML sans les colonnes parasites
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
                # --- FILTRE AFFICHAGE (POUR LE FREROT) ---
                # Explication : On récupère la liste de toutes les factures qui ont des soucis
                # et on propose à l'utilisateur de choisir s'il veut tout voir ou juste une facture.
                liste_fichiers_avec_erreurs = sorted(df_ano['Fichier_Source'].unique().tolist(), reverse=True)
                
                choix_affichage = st.selectbox(
                    "👁️ Filtrer les détails ci-dessous par facture :", 
                    ["TOUT LE DOSSIER (GLOBAL)"] + liste_fichiers_avec_erreurs
                )
                # -----------------------------------------
                st.subheader("🕵️ Détails par Fournisseur")
        
                # 6. Détails
               # 6. Détails
                for fourn_nom in pivot_combo.index:
                    # [CORRECTION] : On ignore la ligne de total pour les dossiers détails
                    if fourn_nom == "TOTAL GÉNÉRAL": continue
                    
                    fourn_dette = total_dette_fourn.get(fourn_nom, 0)
                    
                    with st.expander(f"📂 {fourn_nom} - Dette : {fourn_dette:.2f} €", expanded=False):
                        df_litiges_fourn = df_ano[df_ano['Fournisseur'] == fourn_nom]
                        # --- FILTRE ACTIF (POUR LE FREROT) ---
                        # Si l'utilisateur a choisi une facture précise dans le menu du dessus,
                        # on ne garde QUE les lignes de cette facture.
                        if choix_affichage != "TOUT LE DOSSIER (GLOBAL)":
                            df_litiges_fourn = df_litiges_fourn[df_litiges_fourn['Fichier_Source'] == choix_affichage]
                        
                        # Si après le filtre le tableau est vide (ex: ce fournisseur n'a pas d'erreur sur cette facture),
                        # on affiche un petit message et on passe au suivant.
                        if df_litiges_fourn.empty:
                            st.info(f"✅ Aucune erreur sur la facture {choix_affichage} pour ce fournisseur.")
                            continue
                        # --- BOUTON EXPORT PDF ---
                        anomalies_pour_pdf = []
                        for _, row_pdf in df_litiges_fourn.iterrows():
                            anomalies_pour_pdf.append({
                                'Facture': row_pdf.get('Num Facture', ''),
                                'Date': row_pdf.get('Date Facture', ''),
                                'Article': row_pdf.get('Ref', ''),
                                'Designation': row_pdf.get('Désignation', ''),
                                'Qte': row_pdf.get('Qte', 1),
                                'Prix Brut': row_pdf.get('Prix Brut', 0),
                                'Remise': row_pdf.get('Remise', ''),
                                'Payé (U)': row_pdf.get('Payé (U)', 0),
                                'Prix Cible': row_pdf.get('Prix Cible', 0),
                                'Perte': row_pdf.get('Perte', 0)
                            })
                        total_perte_pdf = df_litiges_fourn['Perte'].sum()
                        date_fac_pdf = df_litiges_fourn['Date Facture'].iloc[0] if 'Date Facture' in df_litiges_fourn.columns else ""
                        num_fac_pdf = choix_affichage if choix_affichage != "TOUT LE DOSSIER (GLOBAL)" else "GLOBAL"
                        
                        pdf_bytes = generer_pdf_facture(num_fac_pdf, date_fac_pdf, fourn_nom, anomalies_pour_pdf, total_perte_pdf)
                        st.download_button(
                            label="📄 Exporter PDF",
                            data=pdf_bytes,
                            file_name=f"anomalies_{fourn_nom}_{num_fac_pdf}.pdf",
                            mime="application/pdf",
                            key=f"pdf_{fourn_nom}_{num_fac_pdf}"
                        )
                        # -------------------------------------
                        for article, group in df_litiges_fourn.groupby('Ref'):
                                    # On ne récupère plus le prix_ref pour l'affichage
                                    date_ref = group['Source Cible'].iloc[0]
                                    remise_ref = group['Remise Cible'].iloc[0]
                                    nom_art = group['Désignation'].iloc[0]

# --- CORRECTION FINALE TITRE (SPECIAL LOUIS) ---
                                    # Louis : Au lieu de faire un calcul (Prix * %), on lit juste la valeur qu'on a transportée.
                                    try:
                                        val_hist = group['Prix_Ref_Hist'].iloc[0]
                                        
                                        # Si on a un prix historique (ex: 56.75), on l'affiche.
                                        if val_hist > 0:
                                            txt_prix_cible = f" 👉 Soit **{val_hist:.4f} €**"
                                        else:
                                            txt_prix_cible = ""
                                    except:
                                        txt_prix_cible = ""
                                    # Louis : On affiche le brut de réf pour qu'il voit sur quelle base la remise s'applique
                                    try:
                                        brut_ref_affiche = ref_map.get(article, {}).get('Brut_Du_Best_Price', 0)
                                        txt_brut = f" (Brut Réf: **{brut_ref_affiche:.2f} €**)" if brut_ref_affiche > 0 else ""
                                    except:
                                        txt_brut = ""
                                    st.markdown(f"**📦 {article}** - {nom_art} | 🎯 Objectif Remise : **{remise_ref}**{txt_brut}{txt_prix_cible} (Vu le {date_ref})")
                                    
                                    # --- INTERFACE D'ARBITRAGE(CORRECTIF CLÉ UNIQUE) ---
                                    c_bt1, c_bt2, c_bt3 = st.columns(3)
                                    # On crée une clé unique en combinant Fournisseur + Article
                                    # Cela empêche l'erreur "DuplicateKey" si une ref existe chez 2 fournisseurs
                                    cle_unique = f"{fourn_nom}_{article}".replace(" ", "_")
                                    
                                    with c_bt1:

# --- REMPLACEMENT AVEC COMMENTAIRES POUR LOUIS ---
                                        # 1. On interroge le registre : Est-ce qu'on a déjà signé un truc pour cet article ?
                                        accord_existant = registre.get(article)

                                        if accord_existant and accord_existant['type'] == "CONTRAT": # <--- LIGNE DE REPERE AVANT
                                            # Louis : Si un contrat est déjà signé, on affiche sa valeur verrouillée.
                                            st.write(f"🔒 Contrat actuel : **{accord_existant['valeur']}{accord_existant['unite']}**")
                                            
                                            col_mod_input, col_mod_btn = st.columns([2, 3])
                                            with col_mod_input:
                                                nouvelle_remise_val = st.number_input(
                                                    label="Modif Remise",
                                                    value=float(accord_existant['valeur']),
                                                    step=0.5,
                                                    format="%.2f",
                                                    key=f"input_mod_{cle_unique}",
                                                    label_visibility="collapsed"
                                                )
                                            with col_mod_btn:
                                                if st.button(f"💾 Valider {nouvelle_remise_val}%", key=f"btn_mod_{cle_unique}"):
                                                    # On met à jour le contrat avec l'unité % par défaut
                                                    sauvegarder_accord(article, "CONTRAT", nouvelle_remise_val, "%", user_id)
                                                    st.rerun()
                                        else:
                                            # Louis : Si c'est libre, on propose de verrouiller la remise cible calculée par l'IA.
                                            if st.button(f"🚀 Verrouiller Contrat ({remise_ref})", key=f"v_{cle_unique}"):
                                                sauvegarder_accord(article, "CONTRAT", clean_float(remise_ref.replace('%','')), "%", user_id)
                                                st.rerun()
                                    with c_bt2:
                                        # Louis : On décide intelligemment si on stocke un % (YESSS) ou un prix Net (EUR).
                                        val_promo_sql = clean_float(remise_ref.replace('%',''))
                                        unite_promo_sql = "%"
                                        
                                        if val_promo_sql <= 0:
                                            val_promo_sql = val_hist
                                            unite_promo_sql = "EUR"
                                        if st.button("🎁 Marquer comme Promo", key=f"p_{cle_unique}"):
                                            sauvegarder_accord(article, "PROMO", val_promo_sql, unite_promo_sql, user_id)
                                            st.rerun()
                                    with c_bt3:
                                        if st.button("❌ Ignorer Erreur", key=f"e_{cle_unique}"):
                                            sauvegarder_accord(article, "ERREUR", 0, "EUR", user_id)
                                            st.rerun()

                                    # Louis : On prépare l'affichage du petit tableau avec les colonnes de preuves techniques.
                                    sub_df = group[['Num Facture', 'Date Facture', 'Qte', 'Prix Brut', 'Brut Réf', 'Remise', 'Payé (U)', 'Perte', 'Prix Cible']] 
                                    
                                    html_detail = (
                                        sub_df.style.format({'Qte': "{:g}", 'Prix Brut': "{}", 'Brut Réf': "{:.4f}", 'Payé (U)': "{:.4f} €", 'Perte': "{:.2f} €"})
                                        .set_properties(**{
                                            'text-align': 'center', 'border': '1px solid black', 'color': 'black'
                                        })
                                        .set_table_styles([
                                            {'selector': 'th', 'props': [('background-color', '#e0e0e0'), ('color', 'black'), ('text-align', 'center'), ('border', '1px solid black')]},
                                            {'selector': 'table', 'props': [('border-collapse', 'collapse'), ('width', '100%'), ('margin-bottom', '20px')]}
                                        ])
                                        .hide(axis="index")
                                        .to_html()
                                    )
                                    
                                    st.markdown(html_detail, unsafe_allow_html=True)
                    

    with tab_import:
   
        st.header("📥 Charger")
        col_info, col_drop = st.columns([1, 2])
        
        with col_info:
            st.write("📂 **En mémoire (Compte actuel) :**")
            if memoire:
                # Louis : J'ai ajouté sorted() autour de la liste pour que tes fichiers soient rangés par ordre alphabétique (A->Z)
                st.dataframe(pd.DataFrame({"Fichiers": sorted(list(memoire.keys()))}), hide_index=True, height=300)
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
            # Louis : J'ajoute sorted() ici aussi pour que ta liste déroulante soit bien rangée de A à Z
            choix_file = st.selectbox("Choisir un fichier pour voir le scan complet :", sorted(list(memoire_full.keys())))                       
            if choix_file:
                st.subheader(f"Texte brut extrait de : {choix_file}")
                raw_txt = memoire_full[choix_file].get('raw_text', 'Aucun scan disponible')
                st.text_area("Résultat Gemini (Full Scan)", raw_txt, height=400)
        else:
            st.info("Aucune donnée enregistrée pour ce compte.")





























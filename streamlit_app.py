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
            Paragraph(str(a.get('Article', '')), style_cell),
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
        if any(x in label_up for x in ["CABLE", "FIL ", "COURONNE", "U1000", "R2V", "AR2V"]): return "CABLAGE"
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

def get_fournisseur_normalise(siret_ou_tva, nom_gemini, user_id):
    """Louis : Récupère le nom normalisé du fournisseur. Élimine le client automatiquement."""
    if not siret_ou_tva:
        return nom_gemini
    
    # Nettoyer pour extraire le SIREN (9 premiers chiffres)
    tva_clean = ''.join(c for c in str(siret_ou_tva) if c.isdigit())
    siren = tva_clean[:9] if len(tva_clean) >= 9 else tva_clean
    
    if not siren:
        return nom_gemini
    
    try:
        # Récupérer le SIREN du client (utilisateur)
        res_user = supabase.table("user_settings").select("siren").eq("user_id", user_id).execute()
        siren_client = res_user.data[0]['siren'] if res_user.data else None
        
        # Si c'est le SIREN du client, on ignore et on continue chercher le vrai fournisseur
        if siren_client and siren == siren_client:
            return nom_gemini
        
        # Chercher dans la table fournisseurs
        res = supabase.table("fournisseurs").select("nom_affiche").eq("siren", siren).execute()
        
        if res.data and len(res.data) > 0:
            return res.data[0]['nom_affiche']
        else:
            # Pas trouvé → créer une entrée avec le nom Gemini
            supabase.table("fournisseurs").insert({
                "nom_affiche": nom_gemini,
                "siren": siren,
                "tva": tva_clean
            }).execute()
            return nom_gemini
    except:
        return nom_gemini

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
           - Fournisseur (Nom complet), Adresse, IBAN, Date, Numéro Facture.
           - SIRET du fournisseur : Cherche "Siret :" dans l'en-tête de la facture (14 chiffres). C'est celui de l'AGENCE qui émet la facture.
           - TVA du fournisseur : Cherche en BAS de la facture "N° Identification T.V.A." (format FR + 11 chiffres). ATTENTION : ne pas confondre avec le TVA du CLIENT.
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
           - Scanne le bas de la facture pour les FRAIS DE FACTURATION (FF, Fr.F, Frais Fixes, Frais de Gestion).
           - Si trouvé, crée une ligne avec l'article "FRAIS_ANNEXE".
           - ⚠️ ATTENTION : NE PAS confondre avec :
             * L'ESCOMPTE (réduction pour paiement anticipé, souvent 0.5% ou 0.75%)
             * La TVA
             * La DEEE / Eco-taxe
             * Le TOTAL HT ou TTC
           - Les vrais frais de facturation sont généralement entre 6€ et 15€.

        JSON ATTENDU :
        {
            "fournisseur": "...",
            "adresse_fournisseur": "...",
            "siret_fournisseur": "...",
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
        
        # Louis : On éclate le JSON en lignes dans la table SQL
        type_doc = data_json.get('type_avoir', 'FACTURE')
        fac_origine = data_json.get('facture_origine', '')
        fournisseur_raw = data_json.get('fournisseur', '')
        siret_raw = data_json.get('siret_fournisseur', '')
        tva_raw = data_json.get('tva_fournisseur', '')
        fournisseur = get_fournisseur_normalise(siret_raw or tva_raw, fournisseur_raw, user_id)
        num_fac = data_json.get('num_facture', '')
        date_fac = data_json.get('date', '')
        
        # On supprime les anciennes lignes de ce fichier avant de réinsérer
        supabase.table("lignes_factures").delete().eq("user_id", user_id).eq("fichier", nom_fichier).execute()
        
        for l in data_json.get('lignes', []):
            ref = l.get('article', '')
            desig = l.get('designation', '')
            qte = clean_float(str(l.get('quantite', 1)))
            brut = clean_float(str(l.get('prix_brut_unitaire', 0)))
            base = clean_float(str(l.get('base_facturation', 1)))
            if base <= 0: base = 1
            remise_str = str(l.get('remise', '0'))
            # Louis : Calcul du taux équivalent pour les remises combinées (60+10 = 64%)
            remise_parts = remise_str.replace('%', '').split('+')
            remise_v = 0
            reste = 100
            for part in remise_parts:
                taux = clean_float(part)
                remise_v += reste * taux / 100
                reste = reste * (1 - taux / 100)
            remise_v = round(remise_v, 2)
            pnu = clean_float(str(l.get('prix_net_unitaire', l.get('prix_net', 0))))
            mont = clean_float(str(l.get('montant', 0)))
            bl = l.get('num_bl_ligne', '')
            famille = detecter_famille(desig, ref)
            
            supabase.table("lignes_factures").insert({
                "user_id": user_id,
                "fichier": nom_fichier,
                "num_facture": num_fac,
                "date_facture": date_fac,
                "fournisseur": fournisseur,
                "type_document": type_doc,
                "facture_origine": fac_origine,
                "article": ref,
                "designation": desig,
                "quantite": qte,
                "prix_brut": brut,
                "base_facturation": base,
                "remise": remise_str,
                "remise_val": remise_v,
                "prix_net": pnu / base if base > 1 else pnu,
                "montant": mont,
                "num_bl": bl,
                "famille": famille
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
        st.header("🏢 Mon Entreprise")
        
        # Chargement des infos entreprise
        if 'user_settings' not in st.session_state:
            try:
                res_settings = supabase.table("user_settings").select("*").eq("user_id", user_id).execute()
                if res_settings.data:
                    st.session_state['user_settings'] = res_settings.data[0]
                else:
                    st.session_state['user_settings'] = {}
            except:
                st.session_state['user_settings'] = {}
        
        settings = st.session_state['user_settings']
        
        col1, col2 = st.columns(2)
        with col1:
            nom_entreprise = st.text_input("Nom de l'entreprise", value=settings.get('nom_entreprise', ''))
            adresse = st.text_input("Adresse", value=settings.get('adresse', ''))
            code_postal = st.text_input("Code postal", value=settings.get('code_postal', ''))
            ville = st.text_input("Ville", value=settings.get('ville', ''))
            email = st.text_input("Email", value=settings.get('email', ''))
        with col2:
            siren = st.text_input("SIREN (9 chiffres)", value=settings.get('siren', ''))
            tva = st.text_input("N° TVA", value=settings.get('tva', ''))            
            telephone = st.text_input("Téléphone", value=settings.get('telephone', ''))
            nom_contact = st.text_input("Nom du gérant/contact", value=settings.get('nom_contact', ''))
        
        if st.button("💾 SAUVEGARDER MES INFOS", key="save_settings"):
            try:
                supabase.table("user_settings").upsert({
                    "user_id": user_id,
                    "nom_entreprise": nom_entreprise,
                    "adresse": adresse,
                    "code_postal": code_postal,
                    "ville": ville,
                    "siren": siren,
                    "tva": tva,
                    "iban": "",
                    "email": email,
                    "telephone": telephone,
                    "nom_contact": nom_contact
                }).execute()
                st.session_state['user_settings'] = {
                    "nom_entreprise": nom_entreprise, "adresse": adresse, "code_postal": code_postal,
                    "ville": ville, "siren": siren, "tva": tva, "iban": "", "email": email,
                    "telephone": telephone, "nom_contact": nom_contact
                }
                st.success("✅ Infos entreprise enregistrées !")
            except Exception as e:
                st.error(f"Erreur : {e}")
        
        st.divider()
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

            # ===== NOUVELLE LOGIQUE SQL =====
            # Louis : On utilise les vues SQL au lieu de calculer en Python
            
            registre = charger_registre(user_id)
            facture_totals = df.groupby('Fichier')['Montant'].sum().to_dict()
            
            # 1. Récupérer les anomalies PRIX depuis SQL (hors câbles et frais)
            res_prix = supabase.table("vue_anomalies_prix").select("*").eq("user_id", user_id).execute()
            anomalies_prix = res_prix.data if res_prix.data else []            
            if anomalies_prix:
                st.write("CLÉS:", list(anomalies_prix[0].keys()))
                st.write("EXEMPLE:", anomalies_prix[0])
            # 2. Récupérer les anomalies CABLAGE depuis SQL
            res_cable = supabase.table("vue_anomalies_cablage").select("*").eq("user_id", user_id).execute()
            anomalies_cable = res_cable.data if res_cable.data else []
            
            # 2.5 Récupérer les anomalies PRIX NET (fournisseurs sans remise)
            res_prix_net = supabase.table("vue_anomalies_prix_net").select("*").eq("user_id", user_id).execute()
            anomalies_prix_net = res_prix_net.data if res_prix_net.data else []
            
            # 3. Récupérer les FRAIS depuis SQL
            res_frais = supabase.table("vue_anomalies_frais").select("*").eq("user_id", user_id).execute()
            anomalies_frais_sql = res_frais.data if res_frais.data else []
            
            # 4. Articles corrigés par avoir (pour ne pas compter 2 fois)
            articles_corriges = {}
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
            
            # --- TRAITEMENT ANOMALIES PRIX (SQL) ---
            for a in anomalies_prix:
                art = a.get('article', '')
                num_fac = a.get('num_facture', '')
                
                # Skip si corrigé par avoir
                if num_fac in articles_corriges and art in articles_corriges[num_fac]:
                    continue
                
                # Skip si accord ERREUR ou PROMO
                # Skip si accord ERREUR
                accord = registre.get(art)
                if accord and accord['type'] == 'ERREUR':
                    continue
                
                # Si HAUSSE validée
                 if accord and accord['type'] == 'HAUSSE':
                     valeur_hausse = clean_float(str(accord['valeur']))
                     unite_hausse = accord.get('unite', 'EUR')
                
                if unite_hausse == 'BRUT' and valeur_hausse > 0:
                    # Fournisseur avec remise : on recalcule le net cible
                    # nouveau_cible = nouveau_brut × (1 - meilleure_remise)
                    best_remise = a.get('best_remise', 0)
                    cible = valeur_hausse * (1 - best_remise / 100)
                    paye = a.get('paye_unitaire', 0)
                    
                    if paye <= cible + 0.05:
                        continue
                    else:
                        perte = (paye - cible) * a.get('quantite', 1)
                elif valeur_hausse > 0:
                    # Fournisseur sans remise : on compare au net directement
                    prix_hausse = valeur_hausse
                    if a.get('paye_unitaire', 0) <= prix_hausse + 0.05:
                        continue
                    else:
                        perte = (a.get('paye_unitaire', 0) - prix_hausse) * a.get('quantite', 1)
                        cible = prix_hausse
                else:
                    perte = a.get('perte', 0)
                    cible = a.get('prix_cible', 0)
                else:
                    perte = a.get('perte', 0)
                    cible = a.get('prix_cible', 0)
                
                if perte > 0.01:
                    anomalies.append({
                        "Fichier_Source": a.get('fichier', ''),
                        "Fournisseur": a.get('fournisseur', ''),
                        "Num Facture": num_fac,
                        "Ref_Cmd": "",
                        "BL": "",
                        "Famille": a.get('famille', ''),
                        "PU_Systeme": a.get('paye_unitaire', 0),
                        "Montant": a.get('paye_unitaire', 0) * a.get('quantite', 1),
                        "Prix Brut": a.get('prix_brut', 0),
                        "Brut Réf": a.get('brut_ref', 0),
                        "Remise": a.get('remise', ''),
                        "Remise Cible": f"{a.get('best_remise', 0)}%",
                        "Qte": a.get('quantite', 1),
                        "Ref": art,
                        "Désignation": a.get('designation', ''),
                        "Payé (U)": a.get('paye_unitaire', 0),
                        "Cible (U)": cible,
                        "Prix Cible": f"{cible:.4f} €",
                        "Perte": perte,
                        "Prix_Ref_Hist": cible,
                        "Motif": "Hausse de prix",
                        "Gravite": a.get('gravite', 'MINEUR'),
                        "Date Facture": a.get('date_facture', ''),
                        "Source Cible": a.get('date_best', ''),
                        "Détails Techniques": f"Remise éq: {a.get('remise_equivalente', 0)}% vs Best: {a.get('best_remise', 0)}%"
                    })
            
            # --- TRAITEMENT ANOMALIES CABLAGE (SQL) ---
            for a in anomalies_cable:
                art = a.get('article', '')
                num_fac = a.get('num_facture', '')
                
                if num_fac in articles_corriges and art in articles_corriges[num_fac]:
                    continue
                
                accord = registre.get(art)
                if accord and accord['type'] == 'ERREUR':
                    continue
                
                perte = a.get('perte', 0)
                cible = a.get('prix_cible', 0)
                
                # Seuil 3% pour câbles
                ecart_pourcent = (perte / (cible * a.get('quantite', 1))) * 100 if cible > 0 else 0
                if perte > 0.01:
                    anomalies.append({
                        "Fichier_Source": a.get('fichier', ''),
                        "Fournisseur": a.get('fournisseur', ''),
                        "Num Facture": num_fac,
                        "Ref_Cmd": "",
                        "BL": "",
                        "Famille": "CABLAGE",
                        "PU_Systeme": a.get('paye_unitaire', 0),
                        "Montant": a.get('paye_unitaire', 0) * a.get('quantite', 1),
                        "Prix Brut": a.get('prix_brut', 0),
                        "Brut Réf": 0,
                        "Remise": a.get('remise', ''),
                        "Remise Cible": f"{a.get('best_remise', 0)}%",
                        "Qte": a.get('quantite', 1),
                        "Ref": art,
                        "Désignation": a.get('designation', ''),
                        "Payé (U)": a.get('paye_unitaire', 0),
                        "Cible (U)": cible,
                        "Prix Cible": f"{cible:.4f} €",
                        "Perte": perte,
                        "Prix_Ref_Hist": cible,
                        "Motif": "Remise insuffisante",
                        "Gravite": "ANOMALIE",
                        "Date Facture": a.get('date_facture', ''),
                        "Source Cible": a.get('date_best_remise', ''),
                        "Détails Techniques": f"Remise: {a.get('remise_val', 0)}% vs {a.get('best_remise', 0)}%"
                    })
            
            # --- TRAITEMENT ANOMALIES PRIX NET (fournisseurs sans remise) ---
            for a in anomalies_prix_net:
                art = a.get('article', '')
                num_fac = a.get('num_facture', '')
                
                if num_fac in articles_corriges and art in articles_corriges[num_fac]:
                    continue
                
                accord = registre.get(art)
                if accord and accord['type'] in ['ERREUR', 'PROMO']:
                    continue
                
                perte = a.get('perte', 0)
                cible = a.get('prix_cible', 0)
                
                if perte > 0.01:
                    anomalies.append({
                        "Fichier_Source": a.get('fichier', ''),
                        "Fournisseur": a.get('fournisseur', ''),
                        "Num Facture": num_fac,
                        "Ref_Cmd": "",
                        "BL": "",
                        "Famille": a.get('famille', ''),
                        "PU_Systeme": a.get('paye_unitaire', 0),
                        "Montant": a.get('paye_unitaire', 0) * a.get('quantite', 1),
                        "Prix Brut": a.get('prix_brut', 0),
                        "Brut Réf": 0,
                        "Remise": "-",
                        "Remise Cible": f"{cible:.2f}€",
                        "Qte": a.get('quantite', 1),
                        "Ref": art,
                        "Désignation": a.get('designation', ''),
                        "Payé (U)": a.get('paye_unitaire', 0),
                        "Cible (U)": cible,
                        "Prix Cible": f"{cible:.4f} €",
                        "Perte": perte,
                        "Prix_Ref_Hist": cible,
                        "Motif": "Hausse de prix",
                        "Gravite": a.get('gravite', 'MINEUR'),
                        "Date Facture": a.get('date_facture', ''),
                        "Source Cible": a.get('date_best', ''),
                        "Détails Techniques": f"Prix net: {a.get('paye_unitaire', 0)}€ vs Best: {cible}€"
                    })
            
            # --- TRAITEMENT FRAIS (SQL + config fournisseur) ---
            for a in anomalies_frais_sql:
                fourn = a.get('fournisseur', '')
                fichier = a.get('fichier', '')
                num_fac = a.get('num_facture', '')
                montant = a.get('montant_frais', 0)
                
                rules = config_dict.get(fourn, {"Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
                max_gestion = rules.get("Max Gestion (€)", 0.0)
                
                if montant > max_gestion:
                    perte = montant - max_gestion
                    anomalies.append({
                        "Fichier_Source": fichier,
                        "Fournisseur": fourn,
                        "Num Facture": num_fac,
                        "Ref_Cmd": "",
                        "BL": "",
                        "Famille": "FRAIS GESTION",
                        "PU_Systeme": montant,
                        "Montant": montant,
                        "Prix Brut": montant,
                        "Brut Réf": 0,
                        "Remise": "-",
                        "Remise Cible": "-",
                        "Qte": 1,
                        "Ref": "FRAIS_ANNEXE",
                        "Désignation": a.get('designation', 'Frais de facturation'),
                        "Payé (U)": montant,
                        "Cible (U)": max_gestion,
                        "Prix Cible": f"{max_gestion:.2f} €",
                        "Perte": perte,
                        "Prix_Ref_Hist": 0,
                        "Motif": "Frais Facturation Abusifs",
                        "Gravite": "ANOMALIE",
                        "Date Facture": a.get('date_facture', ''),
                        "Source Cible": "-",
                        "Détails Techniques": f"(Max autorisé: {max_gestion}€)"
                    })
            
            # --- TRAITEMENT FRAIS PORT (reste en Python car besoin du total facture) ---
            df_port = df[df['Famille'] == 'FRAIS PORT']
            for idx, row in df_port.iterrows():
                fourn = row['Fournisseur']
                f_name = row['Fichier']
                rules = config_dict.get(fourn, {"Franco (Seuil €)": 0.0, "Max Gestion (€)": 0.0})
                seuil_franco = rules.get("Franco (Seuil €)", 0.0)
                total_fac = facture_totals.get(f_name, 0)
                
                if total_fac >= seuil_franco and row['Montant'] > 0:
                    anomalies.append({
                        "Fichier_Source": f_name,
                        "Fournisseur": fourn,
                        "Num Facture": row['Facture'],
                        "Ref_Cmd": row.get('Ref_Cmd', ''),
                        "BL": row.get('BL', ''),
                        "Famille": "FRAIS PORT",
                        "PU_Systeme": row['PU_Systeme'],
                        "Montant": row['Montant'],
                        "Prix Brut": row['Montant'],
                        "Brut Réf": 0,
                        "Remise": "-",
                        "Remise Cible": "100%",
                        "Qte": 1,
                        "Ref": "PORT",
                        "Désignation": row['Désignation'],
                        "Payé (U)": row['Montant'],
                        "Cible (U)": 0,
                        "Prix Cible": "0.00 €",
                        "Perte": row['Montant'],
                        "Prix_Ref_Hist": 0,
                        "Motif": "Port facturé malgré Franco",
                        "Gravite": "CRITIQUE",
                        "Date Facture": row['Date'],
                        "Source Cible": "-",
                        "Détails Techniques": f"(Total: {total_fac:.2f}€ > Franco: {seuil_franco}€)"
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
                                    nom_art = group['Désignation'].iloc[0]
                                    date_ref = group['Source Cible'].iloc[0]
                                    remise_ref = group['Remise Cible'].iloc[0]
                                    prix_cible = group['Cible (U)'].iloc[0]
                                    famille = group['Famille'].iloc[0]
                                    
                                    if famille == "FRAIS GESTION":
                                        st.markdown(f"**📦 {article}** - {nom_art} | Frais max autorisé : **{prix_cible:.2f}€**")
                                    elif famille == "FRAIS PORT":
                                        st.markdown(f"**📦 {article}** - {nom_art} | Port facturé malgré Franco")
                                    else:
                                        grav = group['Gravite'].iloc[0]
                                        icone_grav = "🔴" if grav == "CRITIQUE" else "🟠" if grav == "ANOMALIE" else "🟡"
                                        st.markdown(f"{icone_grav} **📦 {article}** - {nom_art} | 🎯 Meilleure remise : **{remise_ref}** 👉 Cible **{prix_cible:.4f}€** (Vu le {date_ref})")
                                    
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
                                            val_promo_sql = prix_cible
                                            unite_promo_sql = "EUR"
                                        if st.button("🎁 Marquer comme Promo", key=f"p_{cle_unique}"):
                                            sauvegarder_accord(article, "PROMO", val_promo_sql, unite_promo_sql, user_id)
                                            st.rerun()
                                    with c_bt3:
                                        if st.button("❌ Ignorer Erreur", key=f"e_{cle_unique}"):
                                            sauvegarder_accord(article, "ERREUR", 0, "EUR", user_id)
                                            st.rerun()
                                    # Bouton Annuler : si un accord existe, on propose de le supprimer
                                    if accord_existant:
                                        c_bt_annul = st.columns([1])[0]
                                        with c_bt_annul:
                                            if st.button(f"🔓 Annuler ({accord_existant['type']})", key=f"annul_{cle_unique}"):
                                                try:
                                                    supabase.table("accords_commerciaux").delete().eq("article", article).eq("user_id", user_id).execute()
                                                    st.rerun()
                                                except Exception as e:
                                                    st.error(f"Erreur suppression : {e}")
                                    # Bouton hausse annuelle - BRUT pour fournisseurs avec remise, NET sinon
                                if group['Famille'].iloc[0] != "CABLAGE":
                                    c_bt4 = st.columns([1])[0]
                                    with c_bt4:
                                        best_remise_val = clean_float(str(group['Brut Réf'].iloc[0]))
                                        paye_net = group['Payé (U)'].iloc[0]
                                        prix_brut_ligne = clean_float(str(group['Prix Brut'].iloc[0]))
                                        
                                        # Si la meilleure remise > 0 → fournisseur avec remise → on valide le BRUT
                                        if group['Remise'].iloc[0] not in ['-', '0', ''] and clean_float(str(group['Remise'].iloc[0]).replace('%','')) > 0:
                                            if st.button(f"📈 Hausse annuelle (valider brut {prix_brut_ligne:.2f}€)", key=f"h_{cle_unique}"):
                                                sauvegarder_accord(article, "HAUSSE", prix_brut_ligne, "BRUT", user_id)
                                                st.rerun()
                                        else:
                                            # Fournisseur sans remise → on valide le NET
                                            if st.button(f"📈 Hausse annuelle (valider {paye_net:.2f}€)", key=f"h_{cle_unique}"):
                                                sauvegarder_accord(article, "HAUSSE", clean_float(str(paye_net)), "EUR", user_id)
                                                st.rerun()

                                    # Louis : On prépare l'affichage du petit tableau avec les colonnes de preuves techniques.
                                    sub_df = group[['Num Facture', 'Date Facture', 'Qte', 'Prix Brut', 'Brut Réf', 'Remise', 'Payé (U)', 'Perte', 'Prix Cible']].sort_values('Date Facture', ascending=False)
                                    
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

















































































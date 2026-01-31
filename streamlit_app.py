import streamlit as st
from supabase import create_client
from streamlit_supabase_auth import login_form
import google.generativeai as genai
import pandas as pd
import re
import json
import time
from datetime import datetime

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CLE_ANON = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

try:
    supabase = create_client(URL_SUPABASE, CLE_ANON)
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Erreur connexion : {e}")

# ==============================================================================
# 2. FONCTIONS UTILITAIRES
# ==============================================================================

def clean_float(val):
    """Nettoie et convertit une valeur en float."""
    if isinstance(val, (float, int)):
        return float(val)
    if not isinstance(val, str):
        return 0.0
    val = val.replace(' ', '').replace('€', '').replace('EUR', '')
    if ',' in val and '.' in val:
        val = val.replace('.', '').replace(',', '.')
    else:
        val = val.replace(',', '.')
    try:
        return float(val)
    except:
        return 0.0


def calculer_remise_combinee(val_str):
    """Convertit '60+10' en 64.0 (pourcentage total)."""
    if not isinstance(val_str, str):
        return 0.0
    val_str = val_str.replace('%', '').replace(' ', '').replace(',', '.')
    if not val_str:
        return 0.0
    try:
        parts = val_str.split('+')
        reste = 1.0
        for p in parts:
            if p.strip():
                reste *= (1 - float(p.strip()) / 100)
        return round((1 - reste) * 100, 2)
    except:
        return 0.0


def est_cuivre(article, designation):
    """Détecte si un article est du cuivre (câble, fil, etc.)."""
    txt = f"{article} {designation}".upper()
    keywords = [
        "CABLE", "U1000", "R2V", "H07", "FIL", "COURONNE",
        "TOURET", "ICTA", "XVB", "RO2V", "CUIVRE", "GAINE PREFILEE",
        "3G1.5", "3G2.5", "3G6", "4G1.5", "4G2.5", "5G1.5", "5G2.5"
    ]
    return any(kw in txt for kw in keywords)


def est_avoir(montant_total):
    """Détecte si c'est un avoir (montant négatif)."""
    return montant_total < 0


def detecter_famille_ligne(designation, article):
    """Catégorise une ligne (TAXE, FRAIS, PRODUIT)."""
    txt = f"{designation} {article}".upper()
    
    # Taxes
    if any(x in txt for x in ["DEEE", "ECO-PART", "ECOTAXE", "CONTRIBUTION"]):
        return "TAXE"
    
    # Frais
    if any(x in txt for x in ["FRAIS FACT", "FF ", " FF", "FRAIS GESTION"]):
        return "FRAIS"
    if "FRAIS_ANNEXE" in txt:
        return "FRAIS"
    if any(x in txt for x in ["PORT", "LIVRAISON", "TRANSPORT"]) and len(article) < 6:
        return "PORT"
    
    return "PRODUIT"


def calculer_prix_unitaire(ligne, qte):
    """
    Calcule le prix unitaire réel.
    Méthode prioritaire : Montant / Quantité
    """
    montant = clean_float(ligne.get('montant', 0))
    if qte <= 0:
        qte = clean_float(ligne.get('quantite', 1))
    if qte <= 0:
        qte = 1
    
    if montant > 0 and qte > 0:
        return abs(montant / qte)  # abs() pour gérer les avoirs
    
    # Fallback : prix_net / base_facturation
    prix_net = clean_float(ligne.get('prix_net_unitaire', ligne.get('prix_net', 0)))
    base = float(ligne.get('base_facturation', 1))
    if base <= 0:
        base = 1
    
    return abs(prix_net / base) if prix_net > 0 else 0.0


def calculer_prix_brut_unitaire(ligne):
    """Calcule le prix brut unitaire."""
    prix_brut = clean_float(ligne.get('prix_brut_unitaire', ligne.get('prix_brut', 0)))
    base = float(ligne.get('base_facturation', 1))
    if base <= 0:
        base = 1
    return prix_brut / base


# ==============================================================================
# 3. LOGIQUE YESSS KILLER
# ==============================================================================

def analyser_article_yesss(historique_df, article, annee_courante):
    """
    Analyse un article selon la logique YESSS KILLER.
    
    Retourne un dict avec :
    - famille : CUIVRE ou STABLE
    - reference : {prix_net, remise, prix_brut, date}
    - alertes : liste des anomalies détectées
    """
    if historique_df.empty:
        return None
    
    # Trier par date
    df = historique_df.sort_values('Date').copy()
    
    # Détecter la famille
    premiere_ligne = df.iloc[0]
    famille = "CUIVRE" if est_cuivre(article, premiere_ligne.get('Désignation', '')) else "STABLE"
    
    # Filtrer l'année courante pour la référence (STABLE uniquement)
    df_annee = df[df['Année'] == annee_courante]
    
    if famille == "CUIVRE":
        # CUIVRE : Référence = Meilleure remise (toutes années confondues)
        df_avec_remise = df[df['Remise_Val'] > 0]
        if df_avec_remise.empty:
            return None
        
        best_remise_idx = df_avec_remise['Remise_Val'].idxmax()
        best_row = df_avec_remise.loc[best_remise_idx]
        
        reference = {
            'remise': best_row['Remise_Val'],
            'date': best_row['Date'],
            'prix_brut': best_row['Prix_Brut_U'],
            'prix_net': best_row['PU_Systeme']
        }
        
    else:
        # STABLE : Référence = Meilleur prix net de l'année courante
        if df_annee.empty:
            # Pas de données cette année, on prend la dernière année disponible
            df_annee = df
        
        # Exclure les promos évidentes (prix net < 80% du prix moyen)
        prix_moyen = df_annee['PU_Systeme'].mean()
        df_normal = df_annee[df_annee['PU_Systeme'] >= prix_moyen * 0.80]
        
        if df_normal.empty:
            df_normal = df_annee
        
        # Meilleur prix net (le plus bas hors promo)
        best_prix_idx = df_normal['PU_Systeme'].idxmin()
        best_row = df_normal.loc[best_prix_idx]
        
        reference = {
            'prix_net': best_row['PU_Systeme'],
            'remise': best_row['Remise_Val'],
            'prix_brut': best_row['Prix_Brut_U'],
            'date': best_row['Date']
        }
    
    return {
        'famille': famille,
        'reference': reference,
        'historique': df
    }


def detecter_anomalie_yesss(ligne, ref_data):
    """
    Détecte les anomalies sur une ligne selon la logique YESSS KILLER.
    
    Retourne : (perte, motif, details) ou (0, None, None) si OK
    """
    if ref_data is None:
        return 0, None, None
    
    famille = ref_data['famille']
    ref = ref_data['reference']
    
    pu_paye = ligne['PU_Systeme']
    remise_actuelle = ligne['Remise_Val']
    brut_actuel = ligne['Prix_Brut_U']
    qte = ligne['Quantité']
    
    # ==========================================================================
    # CAS 1 : CUIVRE - On surveille uniquement la REMISE
    # ==========================================================================
    if famille == "CUIVRE":
        remise_ref = ref['remise']
        
        if remise_actuelle < remise_ref - 0.5:
            # Calculer le prix qu'on aurait dû payer
            prix_attendu = brut_actuel * (1 - remise_ref / 100)
            perte = (pu_paye - prix_attendu) * qte
            
            if perte > 0.01:
                return (
                    perte,
                    f"🔶 CUIVRE: Remise {remise_actuelle:.1f}% < Réf {remise_ref:.1f}%",
                    f"Prix attendu: {prix_attendu:.4f}€ | Payé: {pu_paye:.4f}€"
                )
        
        return 0, None, None
    
    # ==========================================================================
    # CAS 2 : STABLE - On surveille le PRIX NET avec tolérance sur le brut
    # ==========================================================================
    prix_net_ref = ref['prix_net']
    remise_ref = ref['remise']
    brut_ref = ref['prix_brut']
    
    # Calculer la variation du brut
    if brut_ref > 0:
        variation_brut = ((brut_actuel / brut_ref) - 1) * 100
    else:
        variation_brut = 0
    
    # --- PROMO AUTO-VALIDÉE ---
    # Si prix_net < référence - 15%, c'est une promo, on ignore
    if pu_paye < prix_net_ref * 0.85:
        return 0, None, None  # Promo, pas d'alerte
    
    # --- DOUBLE BRUT DÉTECTÉ ---
    # Brut augmente de +15% ou plus ET remise augmente MAIS prix_net augmente
    if variation_brut >= 15 and remise_actuelle > remise_ref and pu_paye > prix_net_ref * 1.05:
        perte = (pu_paye - prix_net_ref) * qte
        return (
            perte,
            f"🎭 DOUBLE BRUT ! Brut +{variation_brut:.0f}%, Remise {remise_ref:.0f}%→{remise_actuelle:.0f}%",
            f"Réf: {prix_net_ref:.2f}€ | Payé: {pu_paye:.2f}€ | ARNAQUE: +{pu_paye - prix_net_ref:.2f}€/u"
        )
    
    # --- HAUSSE BRUT LÉGITIME ---
    # Brut augmente de +5% à +15%, on tolère si prix_net suit proportionnellement
    if 5 <= variation_brut < 15:
        prix_attendu_ajuste = prix_net_ref * (1 + variation_brut / 100)
        
        if pu_paye <= prix_attendu_ajuste * 1.02:  # Tolérance 2%
            return 0, None, None  # Hausse brut OK, prix suit
        else:
            # Le prix a plus augmenté que le brut !
            perte = (pu_paye - prix_attendu_ajuste) * qte
            if perte > 0.01:
                return (
                    perte,
                    f"🟠 Brut +{variation_brut:.0f}% mais Prix +{((pu_paye/prix_net_ref)-1)*100:.0f}%",
                    f"Attendu: {prix_attendu_ajuste:.2f}€ | Payé: {pu_paye:.2f}€"
                )
    
    # --- BRUT STABLE, PRIX DOIT ÊTRE STABLE ---
    if variation_brut < 5:
        if pu_paye > prix_net_ref * 1.02:  # Tolérance 2%
            perte = (pu_paye - prix_net_ref) * qte
            
            if perte > 0.01:
                # Vérifier si c'est une baisse de remise
                if remise_actuelle < remise_ref - 0.5:
                    motif = f"🔴 Remise {remise_actuelle:.1f}% < Réf {remise_ref:.1f}%"
                else:
                    motif = f"🔴 Prix Net {pu_paye:.2f}€ > Réf {prix_net_ref:.2f}€"
                
                return (
                    perte,
                    motif,
                    f"Écart: +{((pu_paye/prix_net_ref)-1)*100:.1f}% | Perte: {perte:.2f}€"
                )
    
    return 0, None, None


# ==============================================================================
# 4. EXTRACTION GEMINI
# ==============================================================================

def extraire_json_robuste(texte):
    """Extrait le JSON de la réponse Gemini."""
    try:
        match = re.search(r"(\{.*\})", texte, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except:
        pass
    return None


def detecter_ff_cache(data, texte_brut):
    """Détecte les frais de facturation cachés (FF) chez YESSS."""
    fourn = data.get('fournisseur', '').upper()
    
    if "YESSS" in fourn or "CEF" in fourn:
        match = re.search(r"FF\s+([\d\.,]+)", texte_brut)
        if match:
            montant_ff = clean_float(match.group(1))
            if montant_ff > 0:
                existe = any(l.get('article') == "FRAIS_ANNEXE" for l in data.get('lignes', []))
                if not existe:
                    data['lignes'].append({
                        "quantite": 1,
                        "article": "FRAIS_ANNEXE",
                        "designation": "FF - Frais Facturation",
                        "prix_brut_unitaire": montant_ff,
                        "base_facturation": 1,
                        "remise": "0",
                        "prix_net_unitaire": montant_ff,
                        "montant": montant_ff,
                        "num_bl_ligne": "-"
                    })
    return data


def traiter_facture(nom_fichier, user_id):
    """Traite une facture PDF avec Gemini."""
    try:
        file_data = supabase.storage.from_("factures_audit").download(nom_fichier)
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        prompt = """
Analyse cette facture YESSS/CEF et extrais les données en JSON.

ATTENTION AUX PIÈGES YESSS :
1. La colonne "Unité" peut contenir /1, /100, /1000 → c'est la base_facturation
2. Les remises peuvent être combinées : "60+10" = 64% total
3. Le FF (Frais Facturation) est caché en bas dans le tableau TVA
4. Vérifie que Montant ≈ (Prix_Net / base) × Quantité

JSON ATTENDU :
{
    "fournisseur": "YESSS LORIENT",
    "date": "2025-01-15",
    "num_facture": "LOR-XXXXXX",
    "total_ht": 1234.56,
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
            "num_bl_ligne": "LOR/123456"
        }
    ]
}

RÈGLES :
- base_facturation : 1 si "/1", 100 si "/100", 1000 si "/1000"
- remise : garder le format original ("60+10", "70", etc.)
- Ignorer les lignes à 0.00€
"""
        
        response = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_data}])
        
        if not response.text:
            return False, "Réponse vide"
        
        data = extraire_json_robuste(response.text)
        if not data:
            return False, "JSON invalide"
        
        # Détecter FF caché
        data = detecter_ff_cache(data, response.text)
        
        # Sauvegarder
        supabase.table("audit_results").upsert({
            "file_name": nom_fichier,
            "user_id": user_id,
            "analyse_complete": json.dumps(data),
            "raw_text": response.text
        }).execute()
        
        return True, "OK"
        
    except Exception as e:
        return False, str(e)


# ==============================================================================
# 5. CHARGEMENT DES DONNÉES
# ==============================================================================

def charger_donnees(user_id):
    """Charge toutes les factures et construit le DataFrame."""
    try:
        res = supabase.table("audit_results").select("*").eq("user_id", user_id).execute()
    except Exception as e:
        if "JWT expired" in str(e):
            st.session_state.clear()
            st.rerun()
        return pd.DataFrame(), {}
    
    all_rows = []
    memoire = {r['file_name']: r for r in res.data}
    
    for f_name, record in memoire.items():
        try:
            data = json.loads(record['analyse_complete'])
            
            # Ignorer les avoirs
            total_ht = clean_float(data.get('total_ht', 0))
            if est_avoir(total_ht):
                continue
            
            fourn = data.get('fournisseur', 'INCONNU').upper()
            if "YESSS" in fourn or "CEF" in fourn:
                fourn = "YESSS ELECTRIQUE"
            
            date_fac = data.get('date', '')
            num_fac = data.get('num_facture', '-')
            
            # Extraire l'année
            try:
                annee = int(date_fac[:4])
            except:
                annee = datetime.now().year
            
            for ligne in data.get('lignes', []):
                qte = clean_float(ligne.get('quantite', 1))
                if qte == 0:
                    qte = 1
                
                montant = clean_float(ligne.get('montant', 0))
                
                # Ignorer les lignes à 0 ou négatives (avoirs ligne par ligne)
                if montant <= 0:
                    continue
                
                pu = calculer_prix_unitaire(ligne, qte)
                brut_u = calculer_prix_brut_unitaire(ligne)
                remise_str = str(ligne.get('remise', '0'))
                remise_val = calculer_remise_combinee(remise_str)
                
                article = ligne.get('article', '')
                designation = ligne.get('designation', '')
                
                if not article or article in ['None', 'SANS_REF', '.', '0']:
                    continue
                
                famille_ligne = detecter_famille_ligne(designation, article)
                if famille_ligne in ['TAXE', 'FRAIS', 'PORT']:
                    continue  # On ignore taxes et frais pour l'analyse prix
                
                all_rows.append({
                    'Fichier': f_name,
                    'Facture': num_fac,
                    'Date': date_fac,
                    'Année': annee,
                    'Fournisseur': fourn,
                    'Article': article,
                    'Désignation': designation,
                    'Quantité': qte,
                    'Prix_Brut_U': brut_u,
                    'Remise_Str': remise_str,
                    'Remise_Val': remise_val,
                    'PU_Systeme': pu,
                    'Montant': montant,
                    'Est_Cuivre': est_cuivre(article, designation)
                })
                
        except Exception as e:
            continue
    
    return pd.DataFrame(all_rows), memoire


# ==============================================================================
# 6. INTERFACE STREAMLIT
# ==============================================================================

session = login_form(url=URL_SUPABASE, apiKey=CLE_ANON)

if session:
    supabase.postgrest.auth(session["access_token"])
    user_id = session["user"]["id"]
    
    if 'uploader_key' not in st.session_state:
        st.session_state['uploader_key'] = 0
    
    st.title("🎯 YESSS KILLER V1")
    st.caption("Détection automatique des arnaques YESSS : Double Brut, Remise Baissée, Prix Gonflé")
    
    # Charger les données
    df, memoire = charger_donnees(user_id)
    annee_courante = datetime.now().year
    
    # Onglets
    tab_dashboard, tab_details, tab_import, tab_debug = st.tabs([
        "📊 DASHBOARD", "🔍 DÉTAILS", "📥 IMPORT", "🐛 DEBUG"
    ])
    
    # ==========================================================================
    # TAB DASHBOARD
    # ==========================================================================
    with tab_dashboard:
        if df.empty:
            st.warning("Aucune donnée. Importez des factures dans l'onglet IMPORT.")
        else:
            # Construire les références par article
            articles = df['Article'].unique()
            refs_articles = {}
            
            for art in articles:
                df_art = df[df['Article'] == art]
                refs_articles[art] = analyser_article_yesss(df_art, art, annee_courante)
            
            # Analyser chaque ligne
            anomalies = []
            
            for idx, row in df.iterrows():
                art = row['Article']
                ref_data = refs_articles.get(art)
                
                perte, motif, details = detecter_anomalie_yesss(row, ref_data)
                
                if perte > 0.01:
                    anomalies.append({
                        'Date': row['Date'],
                        'Facture': row['Facture'],
                        'Article': art,
                        'Désignation': row['Désignation'][:40],
                        'Qté': row['Quantité'],
                        'Payé': row['PU_Systeme'],
                        'Remise': row['Remise_Str'],
                        'Perte': perte,
                        'Motif': motif,
                        'Détails': details,
                        'Famille': ref_data['famille'] if ref_data else '?'
                    })
            
            # Afficher le résumé
            if anomalies:
                df_ano = pd.DataFrame(anomalies)
                total_perte = df_ano['Perte'].sum()
                
                col1, col2, col3 = st.columns(3)
                col1.metric("💰 DETTE TOTALE", f"{total_perte:.2f} €")
                col2.metric("📄 Lignes suspectes", len(df_ano))
                col3.metric("📦 Articles concernés", df_ano['Article'].nunique())
                
                st.divider()
                
                # Par type d'anomalie
                st.subheader("🎭 Répartition des anomalies")
                
                df_ano['Type'] = df_ano['Motif'].apply(lambda x: 
                    "DOUBLE BRUT" if "DOUBLE" in x else
                    "REMISE BAISSÉE" if "Remise" in x else
                    "PRIX GONFLÉ"
                )
                
                for type_ano, group in df_ano.groupby('Type'):
                    perte_type = group['Perte'].sum()
                    emoji = "🎭" if "DOUBLE" in type_ano else "🔶" if "REMISE" in type_ano else "🔴"
                    
                    with st.expander(f"{emoji} {type_ano} : {perte_type:.2f} € ({len(group)} lignes)"):
                        st.dataframe(
                            group[['Date', 'Facture', 'Article', 'Désignation', 'Remise', 'Payé', 'Perte', 'Motif']],
                            hide_index=True,
                            use_container_width=True
                        )
            else:
                st.success("✅ Aucune anomalie détectée ! YESSS se tient tranquille... pour l'instant.")
    
    # ==========================================================================
    # TAB DÉTAILS
    # ==========================================================================
    with tab_details:
        if df.empty:
            st.info("Aucune donnée.")
        else:
            articles_list = sorted(df['Article'].unique())
            art_choisi = st.selectbox("Choisir un article :", articles_list)
            
            if art_choisi:
                df_art = df[df['Article'] == art_choisi].sort_values('Date')
                ref_data = refs_articles.get(art_choisi)
                
                # Infos article
                st.subheader(f"📦 {art_choisi}")
                st.write(f"**{df_art['Désignation'].iloc[0]}**")
                
                col1, col2 = st.columns(2)
                
                if ref_data:
                    famille = ref_data['famille']
                    ref = ref_data['reference']
                    
                    col1.metric(
                        "Type",
                        "🔶 CUIVRE" if famille == "CUIVRE" else "📦 STABLE"
                    )
                    
                    if famille == "CUIVRE":
                        col2.metric("Remise Référence", f"{ref['remise']:.1f}%")
                    else:
                        col2.metric("Prix Net Référence", f"{ref['prix_net']:.4f} €")
                
                st.divider()
                
                # Historique
                st.subheader("📈 Historique des achats")
                
                st.dataframe(
                    df_art[['Date', 'Facture', 'Quantité', 'Prix_Brut_U', 'Remise_Str', 'PU_Systeme', 'Montant']],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        'Prix_Brut_U': st.column_config.NumberColumn("Brut/U", format="%.4f €"),
                        'PU_Systeme': st.column_config.NumberColumn("Net/U", format="%.4f €"),
                        'Montant': st.column_config.NumberColumn("Total", format="%.2f €")
                    }
                )
                
                # Graphique évolution prix
                if len(df_art) > 1:
                    st.subheader("📉 Évolution du prix unitaire")
                    chart_data = df_art[['Date', 'PU_Systeme']].copy()
                    chart_data['Date'] = pd.to_datetime(chart_data['Date'])
                    chart_data = chart_data.set_index('Date')
                    st.line_chart(chart_data)
    
    # ==========================================================================
    # TAB IMPORT
    # ==========================================================================
    with tab_import:
        st.header("📥 Importer des factures")
        
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.write(f"**{len(memoire)}** factures en mémoire")
            
            if st.button("🗑️ TOUT EFFACER", type="secondary"):
                supabase.table("audit_results").delete().eq("user_id", user_id).execute()
                st.session_state['uploader_key'] += 1
                st.rerun()
        
        with col2:
            uploaded = st.file_uploader(
                "Glissez vos PDFs ici",
                type="pdf",
                accept_multiple_files=True,
                key=f"upload_{st.session_state['uploader_key']}"
            )
            
            force = st.checkbox("Écraser les doublons")
            
            if uploaded and st.button("🚀 ANALYSER", type="primary"):
                progress = st.progress(0)
                
                for i, f in enumerate(uploaded):
                    if f.name in memoire and not force:
                        st.warning(f"⏭️ {f.name} ignoré (déjà présent)")
                    else:
                        with st.spinner(f"Analyse de {f.name}..."):
                            # Upload
                            supabase.storage.from_("factures_audit").upload(
                                f.name, f.getvalue(), {"upsert": "true"}
                            )
                            # Traitement
                            ok, msg = traiter_facture(f.name, user_id)
                            
                            if ok:
                                st.success(f"✅ {f.name}")
                            else:
                                st.error(f"❌ {f.name} : {msg}")
                    
                    progress.progress((i + 1) / len(uploaded))
                
                st.session_state['uploader_key'] += 1
                time.sleep(1)
                st.rerun()
    
    # ==========================================================================
    # TAB DEBUG
    # ==========================================================================
    with tab_debug:
        st.header("🐛 Debug - Données brutes")
        
        if not df.empty:
            st.subheader("DataFrame complet")
            st.dataframe(df, use_container_width=True)
            
            st.subheader("Références calculées")
            for art, ref_data in refs_articles.items():
                if ref_data:
                    with st.expander(f"{art} ({ref_data['famille']})"):
                        st.json(ref_data['reference'])
        else:
            st.info("Aucune donnée.")

# Fichier : vmc_calcul_debits.py
# Ce fichier sert à calculer les volumes d'air à extraire d'un logement.

def calculer_debits_vmc(nb_pieces_principales, nb_sdb, nb_wc):
    """
    Fonction principale pour calculer les débits réglementaires.
    Louis, c'est ici que toute la logique de l'Arrêté de 1982 est stockée.
    """
    
    # ---------------------------------------------------------
    # 1. DÉFINITION DES RÈGLES (Arrêté du 24 mars 1982)
    # ---------------------------------------------------------
    
    # Débit minimum TOTAL à extraire en cuisine (en m3/h) selon la taille du logement (T1, T2, etc.)
    # Si T1 (1 pièce) -> 75 m3/h, T2 -> 90 m3/h, etc.
    regle_debit_cuisine = {
        1: 75,
        2: 90,
        3: 105,
        4: 120,
        5: 135  # Pour T5 et plus
    }
    
    # Débits spécifiques pour les sanitaires (valeurs standards)
    debit_salle_de_bain = 30  # m3/h par salle de bain
    debit_wc = 30             # m3/h par WC (si wc unique)
    if nb_wc > 1:
        debit_wc = 15         # Si plusieurs WC, on peut réduire à 15 m3/h par WC (selon config)

    # ---------------------------------------------------------
    # 2. CALCULS
    # ---------------------------------------------------------

    # On récupère le débit cuisine requis. 
    # La fonction 'min' permet de plafonner à 5 (si c'est un T6, on prend la valeur du T5)
    taille_logement_cle = min(nb_pieces_principales, 5) 
    debit_cuisine_cible = regle_debit_cuisine[taille_logement_cle]

    # Calcul du débit total des sanitaires (SDB + WC)
    total_sanitaires = (nb_sdb * debit_salle_de_bain) + (nb_wc * debit_wc)

    # Le débit total à extraire du logement (Cuisine + Sanitaires)
    debit_total_a_extraire = debit_cuisine_cible + total_sanitaires

    # ---------------------------------------------------------
    # 3. AFFICHAGE DES RÉSULTATS (Pour vérifier)
    # ---------------------------------------------------------
    print(f"--- ÉTUDE VMC POUR UN T{nb_pieces_principales} ---")
    print(f"Logement : {nb_pieces_principales} pièces principales, {nb_sdb} SDB, {nb_wc} WC.")
    print(f"-> Débit Cuisine retenu : {debit_cuisine_cible} m3/h")
    print(f"-> Débit Sanitaires ({nb_sdb} SDB + {nb_wc} WC) : {total_sanitaires} m3/h")
    print(f"------------------------------------------------")
    print(f"TOTAL GÉNÉRAL À EXTRAIRE : {debit_total_a_extraire} m3/h")
    print(f"------------------------------------------------")
    
    # On renvoie ce chiffre pour pouvoir l'utiliser plus tard dans le choix du caisson
    return debit_total_a_extraire

# ---------------------------------------------------------
# ZONE DE TEST (Pour voir si ça marche)
# Louis, tu peux modifier les chiffres ci-dessous pour tester d'autres maisons
# ---------------------------------------------------------

# Exemple : Une maison T4 avec 1 SDB et 2 WC
calculer_debits_vmc(nb_pieces_principales=4, nb_sdb=1, nb_wc=2)

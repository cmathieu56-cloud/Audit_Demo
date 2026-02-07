-- FIX : Auto-détection des promos dans vue_brut_reference
-- Problème : quand un article est acheté une fois à prix forcé (remise=0, brut=net)
-- et d'autres fois avec remise, le prix forcé (souvent une promo) devient la référence
-- et pollue toute la détection d'anomalies.
--
-- Solution : exclure automatiquement les lignes sans remise quand le même article
-- a des achats avec remise chez le même user. Ces lignes sont des "prix forcés"
-- (promo, tarif spécial) et ne doivent pas servir de référence.

CREATE OR REPLACE VIEW vue_brut_reference AS
SELECT DISTINCT ON (user_id, article)
    user_id,
    article,
    designation,
    fournisseur,
    (prix_brut / NULLIF(base_facturation, 0)) AS brut_unitaire_ref,
    remise_val AS remise_ref,
    prix_net AS prix_net_ref,
    date_facture AS date_ref,
    remise AS remise_txt_ref
FROM lignes_factures l
WHERE
    prix_net > 0.01
    AND type_document IN ('FACTURE', 'CORRECTION')
    AND famille NOT IN ('TAXE', 'FRAIS GESTION', 'FRAIS PORT', 'CABLAGE')
    AND quantite > 0
    -- Exclusion manuelle : accords PROMO déjà marqués par l'utilisateur
    AND NOT EXISTS (
        SELECT 1 FROM accords_commerciaux ac
        WHERE ac.article = l.article
          AND ac.user_id = l.user_id
          AND ac.type_accord = 'PROMO'
          AND (
              (ac.unite = 'EUR' AND abs(l.prix_net::double precision - ac.valeur) < 0.02)
           OR (ac.unite = '%'  AND abs(l.remise_val::double precision - ac.valeur) < 0.02)
          )
    )
    -- AUTO-DETECT PROMO : si remise=0 mais que l'article a des achats avec remise,
    -- c'est un prix forcé (promo) → on l'exclut de la référence
    AND NOT (
        l.remise_val = 0
        AND EXISTS (
            SELECT 1 FROM lignes_factures lf2
            WHERE lf2.user_id = l.user_id
              AND lf2.article = l.article
              AND lf2.remise_val > 0
              AND lf2.quantite > 0
              AND lf2.prix_net > 0.01
        )
    )
ORDER BY user_id, article, prix_net, date_facture;

-- vue_brut_reference : référence de prix par article
-- Prend le prix_net le plus bas comme référence.
-- Les promos sont exclues MANUELLEMENT via le bouton "Ref = Promo"
-- (table accords_commerciaux, type_accord='PROMO')

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
    -- Exclusion manuelle : accords PROMO marqués par l'utilisateur
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
ORDER BY user_id, article, prix_net, date_facture;

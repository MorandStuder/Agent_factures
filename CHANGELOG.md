# Changelog

## v1.2.1 — 2026-03-19

### Corrections
- Suppression du corps orphelin de `pass2_erp_to_bank` resté dans `passes.py`
  après la suppression de la signature — provoquait un `NameError: UNPAID_STATUSES`
  à chaque exécution
- Docstring E501 corrigée dans `main.py`

### Documentation
- README : algorithme mis à jour (Passe 0 / Passe 2 supprimées, nouvelle structure
  deux passes Payées → Non-payées)
- README : compte de colonnes banque corrigé (6 → 7)
- README : `DATE_TOLERANCE_DAYS` ne réfère plus à la Passe 0 (supprimée)

---

## v1.2.0 — 2026-03-18

### Fonctionnalités
- Déduction d'avoirs dans les combos : `Σfactures − Σavoirs = crédit_banque`
  - Détection via le champ `Type = "Avoir"` (et non `Statut`) — 76 avoirs identifiés
  - Avoirs exclus du rapprochement principal (`matchable`) et traités séparément
  - Paramètre `ENABLE_AVOIRS_COMBO` dans `config.py` pour activer/désactiver
  - Paramètre `AVOIR_TYPE` pour configurer la valeur du champ `Type`
  - Notes dans l'output ERP : `"avoir déduit : AV-xxx"` et `"avoir déduit, groupé avec FA-xxx"`
  - Numéros d'avoirs inclus dans `bank_matches` (visible dans l'output banque)
- Encodage UTF-8 forcé sur la sortie console Windows (`sys.stdout.reconfigure`)

### Corrections
- `DATE_TOLERANCE_DAYS` : corrigé 5 → 7 dans la documentation (valeur réelle depuis v1.1.0)
- `CANCELLED_STATUSES` nettoyé : suppression de `"Avoir"` (inexistant comme statut ERP)

---

## v1.1.0 — 2026-03-18

### Améliorations
- Colonnes de sortie enrichies : score de confiance dans le fichier banque
- Montants formatés `#,##0.00` dans les deux fichiers de sortie
- Dates formatées `DD/MM/YYYY` dans les deux fichiers de sortie
- Nettoyage automatique des anciens fichiers de sortie (3 plus récents conservés)
- `DATE_TOLERANCE_DAYS` augmenté de 5 à 7 jours

### Refactoring
- Découpage de `reconcile.py` (monolithe ~1700 lignes) en package `reconcile/` :
  - `config.py` : constantes et paramètres
  - `loaders.py` : chargement et normalisation
  - `matching.py` : helpers de correspondance
  - `passes.py` : algorithme pass0 / pass1 / pass2
  - `writers.py` : génération XLSX
  - `reporting.py` : résumé console + diagnostic
- Extraction de `find_matching_combo()` : factorisation du pattern combo dupliqué 4 fois
- Déplacement des imports `defaultdict` inline vers les en-têtes de fichier
- Déplacement de `DATE_TOLERANCE_AMOUNT_DATE` dans `config.py`
- Déplacement des fichiers sources dans `data/`
- `BANK_FILE` déterminé automatiquement par glob sur `data/` (plus besoin de modifier le code à chaque nouveau relevé)
- Point d'entrée renommé `main.py`

### Documentation
- Docstrings Google style sur toutes les fonctions publiques
- README.md avec description de l'algorithme, du scoring et de la configuration
- CHANGELOG.md

---

## v1.0.0 — 2026-03-17

### Fonctionnalités
- Passe 0 : rapprochement des factures avec date de paiement ERP (±5 jours)
- Passe 0 combo : paiements groupés de N factures
- Passe 1 sous-passe A : numéro de facture explicite dans le libellé bancaire
- Passe 1 sous-passe B : montant exact + compte + similarité client
- Passe 1 sous-passe B2 : cross-account avec similarité client >= 80
- Passe 1 sous-passe B3 : montant exact + date paiement ERP ±3 jours (candidat unique)
- Passe 1 sous-passe C : combo N factures même client/compte
- Passe 1 sous-passe C2 : combo cross-account
- Passe 2 : factures non-payées → crédits restants
- Score de confiance 0-100 avec seuils auto (80) et à vérifier (40)
- `client_mapping.csv` : aliases banque + override compte par client
- `--diagnose` : outil de diagnostic des lignes bancaires non rapprochées
- Préservation des commentaires manuels entre runs
- Numéros de facture sans séparateur (ex: "20252518" → "2025-2518")
- Bypass contrainte de date quand le numéro est explicite dans le libellé
- Factures sans compte bancaire incluses dans les combos de leur client

---

## v0.1.0 — 2026-03-16

### Initial
- Script `reconcile.py` initial : deux passes de réconciliation
- Génération de deux fichiers XLSX enrichis horodatés


# Agent Factures — Réconciliation ERP ↔ Banque

Outil de rapprochement automatique entre les factures de l'ERP (app-eleven) et le relevé bancaire exporté depuis la banque.

## Usage

```bash
# Réconciliation complète
py main.py

# Diagnostic d'une ou plusieurs lignes bancaires non rapprochées
py main.py --diagnose 441 549 560
```

## Sorties

Dans `output/` :
- `ERP_reconcilie_YYYYMMDD_HHMMSS.xlsx` — ERP enrichi avec 6 colonnes ajoutées (tableau vert clair, volets figés en G2, colonnes B–E masquées) + onglet **"Factures à payer"**
- `Banque_reconciliee_YYYYMMDD_HHMMSS.xlsx` — relevé bancaire enrichi avec 7 colonnes ajoutées ; dates C/D et montant F reformatés (date `DD/MM/YYYY`, nombre `#,##0.00`)

L'onglet **Factures à payer** liste toutes les factures non-payées non rapprochées, triées par client, avec le total en bas. Il est recalculé à chaque run.

## Structure du projet

```
Agent_factures/
├── main.py                    # point d'entrée
├── reconcile/                 # package Python
│   ├── config.py              # constantes, chemins, paramètres
│   ├── loaders.py             # chargement fichiers + normalisation
│   ├── matching.py            # helpers de correspondance
│   ├── passes.py              # algorithme (deux passes : payées puis non-payées)
│   ├── writers.py             # génération XLSX
│   └── reporting.py          # résumé console + diagnostic
├── data/                      # fichiers sources (non commités)
│   ├── Export_invoices.xlsx
│   ├── Export des ecritures*.xlsx   # le plus récent est pris automatiquement
│   └── client_mapping.csv
├── output/                    # fichiers générés (non commités)
└── requirements.txt
```

La console affiche en fin de run un **résumé des nouvelles réconciliations** : factures nouvellement rapprochées (+) ou perdues (-) par rapport au run précédent.

## Algorithme

L'algorithme applique la même logique de rapprochement **deux fois** :

1. **Passe 1 — factures Payées** : les crédits bancaires sont d'abord mis en regard des factures déjà marquées payées dans l'ERP. On est quasi-certain du sens du virement.
2. **Passe 2 — factures Non-payées** : sur les crédits restants, on cherche les factures Emises / Envoyées / Non-émises. Priorité donnée aux payées pour éviter qu'un crédit soit capté par une facture en attente alors qu'il correspond à une payée.

Chaque passe parcourt les mêmes sous-passes dans l'ordre :

| Sous-passe | Critère principal | Sécurité |
|---|---|---|
| **A** | Numéro de facture explicite dans le libellé | Combo possible si plusieurs factures |
| **B** | Montant exact + même compte | Similarité client >= 40 |
| **B2** | Montant exact + cross-account | Similarité client >= 80 |
| **B3** | Montant exact + date paiement ERP ±7j | Candidat unique (pas d'ambiguïté) |
| **C** | Combo N factures ± avoirs même client/compte | Somme exacte |
| **C2** | Combo N factures ± avoirs cross-account | Similarité client >= 80 |

### Déduction d'avoirs
Les lignes ERP avec `Type = "Avoir"` (notes de crédit) sont automatiquement utilisées comme déductions dans les combos :

```
Σ(factures) − Σ(avoirs) = crédit_banque
```

Un avoir peut réduire un paiement même s'il est marqué "Payée" dans l'ERP (cas d'une compensation intégrée au virement). La note dans l'output indique `"avoir déduit : AV-xxx"`. Désactivable via `ENABLE_AVOIRS_COMBO = False` dans `config.py`.

## Score de confiance

| Critère | Points |
|---|---|
| Numéro de facture dans le libellé | +40 |
| Montant exact | +30 |
| Même compte bancaire | +15 |
| Similarité client >= 80 | +15 |
| Similarité client >= 60 | +10 |
| Similarité client >= 40 | +5 |

- **Score >= 80** → vert (rapproché automatiquement)
- **Score 40-79** → jaune (à vérifier manuellement)
- **Non rapproché** → rouge (factures non-payées uniquement)

## Configuration

### client_mapping.csv
Permet de définir des aliases banque pour les clients ERP et de forcer un compte bancaire :

```csv
erp_client,bank_name,bank_account
Emil Frey,Barrault,LCL
Emil Frey,PBO,LCL
Dior,CHRISTIAN DIOR,
```

- `bank_name` : alias utilisé pour la similarité (peut être répété pour plusieurs aliases)
- `bank_account` : force le compte bancaire (`LCL`, `BPRI`, `CA`) pour ce client

### config.py
- `ENABLE_AVOIRS_COMBO` (défaut : `True`) — activer la déduction d'avoirs dans les combos
- `DATE_TOLERANCE_DAYS` (défaut : 7) — tolérance date pour les sous-passes B et B3
- `DATE_TOLERANCE_AMOUNT_DATE` (défaut : 7) — tolérance date pour la sous-passe B3
- `THRESHOLD_AUTO` (défaut : 80) — seuil rapprochement automatique
- `THRESHOLD_REVIEW` (défaut : 40) — seuil à vérifier
- `AVOIR_TYPE` (défaut : `"Avoir"`) — valeur du champ `Type` pour les notes de crédit

## Installation

```bash
pip install -r requirements.txt
```

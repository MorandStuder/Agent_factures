"""Point d'entrée de la réconciliation factures ERP ↔ relevé bancaire.

Usage:
    py main.py                        # réconciliation complète
    py main.py --diagnose 441 549 560 # diagnostic de lignes bancaires

Algorithme en deux passes :
    Passe 1 — Payées : banque → factures Payées (premier accès aux crédits)
    Passe 2 — Non-payées : banque → factures Emise/Envoyée/Non émise
"""

import sys
from datetime import datetime

# Force l'encodage UTF-8 sur la sortie standard (Windows cp1252 par défaut)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from reconcile.config import (
    AVOIR_TYPE,
    BANK_FILE,
    CANCELLED_STATUSES,
    ENABLE_AVOIRS_COMBO,
    ERP_FILE,
    OUTPUT_DIR,
    UNPAID_STATUSES,
)
from reconcile.loaders import (
    load_client_mapping,
    load_previous_comments,
    load_previous_erp_matches,
    load_workbook_data,
    normalize_amount,
)
from reconcile.matching import get_bank_row_label
from reconcile.passes import pass1_bank_to_invoices
from reconcile.reporting import diagnose_bank_rows, print_summary
from reconcile.writers import write_bank_output, write_erp_output


def main() -> None:
    """Orchestre la réconciliation complète et génère les fichiers de
    sortie."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("Chargement des données...")
    print(f"  ERP    : {ERP_FILE}")
    print(f"  Banque : {BANK_FILE}")
    client_mapping, account_overrides = load_client_mapping()
    previous_comments = load_previous_comments()
    prev_matched = load_previous_erp_matches()
    _, all_invoices = load_workbook_data(ERP_FILE)
    _, bank_rows = load_workbook_data(BANK_FILE)
    print(f"  {len(all_invoices)} factures ERP chargées")
    print(f"  {len(bank_rows)} lignes bancaires chargées")
    if account_overrides:
        print(f"  Overrides compte : {account_overrides}")

    # Avoirs (Type="Avoir") : déductibles d'un virement (Σfactures − Σavoirs)
    credit_notes = (
        [inv for inv in all_invoices if inv.get("Type") == AVOIR_TYPE]
        if ENABLE_AVOIRS_COMBO
        else []
    )

    # Exclure du rapprochement : annulées (Statut) et avoirs (Type)
    matchable = [
        inv for inv in all_invoices
        if inv.get("Statut") not in CANCELLED_STATUSES
        and inv.get("Type") != AVOIR_TYPE
    ]
    cancelled_count = len(all_invoices) - len(matchable)
    if cancelled_count:
        print(
            f"  {cancelled_count} factures annulées/avoirs "
            f"exclus du rapprochement"
        )
    if credit_notes:
        print(f"  {len(credit_notes)} avoir(s) inclus dans les combos")

    # Séparation payées / non-payées
    payees = [
        inv for inv in matchable
        if inv.get("Statut") not in UNPAID_STATUSES
    ]
    non_payees = [
        inv for inv in matchable
        if inv.get("Statut") in UNPAID_STATUSES
    ]
    print(
        f"  {len(payees)} factures payées, "
        f"{len(non_payees)} non-payées"
    )

    # Structures partagées (modifiées en place par les deux passes)
    erp_matches: dict[int, dict] = {}
    bank_matches: dict[int, list[str]] = {}
    used_bank: set[int] = set()

    print(
        f"Passe 1 -- banque -> factures payées ({len(payees)})..."
    )
    pass1_bank_to_invoices(
        payees, bank_rows, client_mapping, account_overrides,
        previous_comments, erp_matches, bank_matches, used_bank,
        credit_notes=credit_notes,
    )
    print(
        f"  {len(bank_matches)} lignes bancaires rapprochées, "
        f"{len(erp_matches)} factures payées"
    )

    print(
        f"Passe 2 -- banque -> factures non-payées ({len(non_payees)})..."
    )
    pass1_bank_to_invoices(
        non_payees, bank_rows, client_mapping, account_overrides,
        previous_comments, erp_matches, bank_matches, used_bank,
        credit_notes=credit_notes,
    )
    print(
        f"  {len(bank_matches)} lignes bancaires rapprochées au total, "
        f"{len(erp_matches)} factures"
    )

    unpaid_unmatched = [
        inv for inv in non_payees
        if inv["_row_idx"] not in erp_matches
    ]
    print_summary(
        matchable, erp_matches, bank_matches,
        prev_matched=prev_matched,
    )

    erp_out = OUTPUT_DIR / f"ERP_reconcilie_{ts}.xlsx"
    bank_out = OUTPUT_DIR / f"Banque_reconciliee_{ts}.xlsx"

    # Index factures par numéro (inclut les avoirs pour le fichier banque)
    inv_by_num = {
        str(inv.get("Numéro de facture", "") or "").strip(): inv
        for inv in all_invoices
        if inv.get("Numéro de facture")
    }
    bank_row_to_invoices: dict[int, list[dict]] = {
        row_idx: [inv_by_num[n] for n in nums if n in inv_by_num]
        for row_idx, nums in bank_matches.items()
    }

    # Score de confiance par ligne bancaire (min des scores des items)
    bank_row_scores: dict[int, int] = {
        row_idx: min(
            erp_matches[inv["_row_idx"]]["score"]
            for inv in invs
            if inv["_row_idx"] in erp_matches
        )
        for row_idx, invs in bank_row_to_invoices.items()
        if any(inv["_row_idx"] in erp_matches for inv in invs)
    }

    # Lignes exclues mais étiquetées (CPAM, URSSAF, SIE, TRESO, etc.)
    labeled_exclusions: dict[int, str] = {}
    for row in bank_rows:
        amount = normalize_amount(row.get("Credit"))
        if not (amount and amount > 0):
            continue
        label = get_bank_row_label(row)
        if label:
            labeled_exclusions[row["_row_idx"]] = label

    print("Génération des fichiers de sortie...")
    write_erp_output(
        ERP_FILE, all_invoices, erp_matches, erp_out,
        unpaid_unmatched=unpaid_unmatched,
    )
    write_bank_output(
        BANK_FILE, bank_matches, bank_row_to_invoices,
        labeled_exclusions, bank_row_scores, bank_out,
    )
    print(f"  ERP    -> {erp_out}")
    print(f"  Banque -> {bank_out}")

    # Nettoyage : on ne garde que les 3 fichiers les plus récents
    MAX_OUTPUT_FILES = 3
    for pattern in ("ERP_reconcilie_*.xlsx", "Banque_reconciliee_*.xlsx"):
        old_files = sorted(
            OUTPUT_DIR.glob(pattern), reverse=True
        )[MAX_OUTPUT_FILES:]
        for f in old_files:
            try:
                f.unlink()
                print(f"  Supprimé : {f.name}")
            except PermissionError:
                print(f"  Ignoré (fichier ouvert) : {f.name}")

    print("Terminé.")


if __name__ == "__main__":
    if "--diagnose" in sys.argv:
        idx = sys.argv.index("--diagnose")
        rows = [int(x) for x in sys.argv[idx + 1:] if x.isdigit()]
        diagnose_bank_rows(rows)
    else:
        main()

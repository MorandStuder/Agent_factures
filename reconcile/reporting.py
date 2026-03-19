"""Résumé console et outil de diagnostic des lignes bancaires non rapprochées."""

from .config import (
    ACCOUNT_MAPPING,
    BANK_FILE,
    CANCELLED_STATUSES,
    ERP_FILE,
    THRESHOLD_AUTO,
    THRESHOLD_REVIEW,
    UNPAID_STATUSES,
)
from .loaders import (
    dates_close,
    load_client_mapping,
    load_previous_comments,
    load_workbook_data,
    normalize_amount,
    parse_date,
    payment_before_emission,
    resolve_bank_account,
    to_date,
)
from .matching import (
    bank_text,
    client_similarity,
    compute_score,
    extract_invoice_numbers,
    get_bank_row_label,
    is_excluded_bank_row,
)


def print_summary(
    all_invoices: list[dict],
    erp_matches: dict[int, dict],
    bank_matches: dict[int, list[str]],
) -> None:
    """Affiche le résumé de réconciliation dans la console.

    Affiche les compteurs globaux, les factures non rapprochées, et les
    correspondances à vérifier manuellement (score < THRESHOLD_AUTO).

    Args:
        all_invoices: toutes les factures ERP (hors annulées/avoirs).
        erp_matches: résultats de réconciliation par _row_idx.
        bank_matches: lignes bancaires rapprochées par _row_idx.
    """
    total_all = len(all_invoices)
    total_unpaid = sum(1 for inv in all_invoices if inv.get("Statut") in UNPAID_STATUSES)
    matched_all = len(erp_matches)
    matched_auto = sum(1 for m in erp_matches.values() if m["score"] >= THRESHOLD_AUTO)
    matched_review = sum(
        1 for m in erp_matches.values()
        if THRESHOLD_REVIEW <= m["score"] < THRESHOLD_AUTO
    )
    unpaid_matched = sum(
        1 for inv in all_invoices
        if inv.get("Statut") in UNPAID_STATUSES and inv["_row_idx"] in erp_matches
    )
    unpaid_unmatched = total_unpaid - unpaid_matched
    bank_filled = len(bank_matches)

    print("\n" + "=" * 65)
    print("  RÉSUMÉ RÉCONCILIATION")
    print("=" * 65)
    print(f"  Factures total ERP             : {total_all}")
    print(f"  Dont non-payées                : {total_unpaid}")
    print(f"  Lignes banque rapprochées      : {bank_filled}")
    print(f"  Factures ERP rapprochées total : {matched_all}")
    print(f"    dont auto (score >= {THRESHOLD_AUTO})       : {matched_auto}")
    print(f"    dont à vérifier ({THRESHOLD_REVIEW}-{THRESHOLD_AUTO - 1})        : {matched_review}")
    print(f"  Non-payées rapprochées         : {unpaid_matched} / {total_unpaid}")
    print(f"  Non-payées NON rapprochées     : {unpaid_unmatched}")
    print("=" * 65)

    if unpaid_unmatched > 0:
        print("\n  FACTURES NON-PAYÉES NON RAPPROCHÉES :")
        for inv in all_invoices:
            if inv.get("Statut") in UNPAID_STATUSES and inv["_row_idx"] not in erp_matches:
                num = str(inv.get("Numéro de facture", "") or "")
                client = str(inv.get("Client", "") or "")
                ttc = str(inv.get("Total à facturer (TTC)", "") or "")
                statut = str(inv.get("Statut", "") or "")
                compte = str(inv.get("Compte bancaire", "") or "")
                print(
                    f"    {num:<15} {client:<28} {ttc:>14} TTC"
                    f"  [{statut}] [{compte}]"
                )

    if matched_review > 0:
        print("\n  À VÉRIFIER :")
        for inv in all_invoices:
            m = erp_matches.get(inv["_row_idx"])
            if m and THRESHOLD_REVIEW <= m["score"] < THRESHOLD_AUTO:
                num = str(inv.get("Numéro de facture", "") or "")
                client = str(inv.get("Client", "") or "")
                note = m.get("note", "")
                print(
                    f"    {num:<15} {client:<28}"
                    f"  -> {m['date_constatee']}  score={m['score']}"
                    f"  {note}"
                )
    print()


def diagnose_bank_rows(row_indices: list[int]) -> None:
    """Affiche pourquoi les lignes bancaires spécifiées ne sont pas rapprochées.

    Recharge les données depuis le disque pour un usage en outil standalone.
    Pour chaque ligne :
    - Vérifie les causes d'exclusion (type op, libellé, commentaire)
    - Liste les factures candidates avec même compte + même montant
    - Affiche les numéros de facture extraits du texte bancaire
    - Pour le compte LCL : affiche le total de chaque client vs le crédit

    Args:
        row_indices: liste des _row_idx (numéros de ligne Excel, base 1, header=1).
    """
    client_mapping, account_overrides = load_client_mapping()
    previous_comments = load_previous_comments()
    _, all_invoices = load_workbook_data(ERP_FILE)
    _, bank_rows = load_workbook_data(BANK_FILE)

    matchable = [i for i in all_invoices if i.get("Statut") not in CANCELLED_STATUSES]
    inv_by_acct_amount: dict[tuple[str, float], list[dict]] = {}
    for inv in matchable:
        amount = normalize_amount(inv.get("Total à facturer (TTC)"))
        bank_acct = resolve_bank_account(inv, account_overrides)
        if amount and bank_acct:
            inv_by_acct_amount.setdefault((bank_acct, amount), []).append(inv)

    row_map = {r["_row_idx"]: r for r in bank_rows}
    SEP = "-" * 70

    for idx in row_indices:
        row = row_map.get(idx)
        print(SEP)
        if row is None:
            print(f"[ligne {idx}] INTROUVABLE dans le fichier banque")
            continue

        acct = str(row.get("Intitulé du compte", "") or "").strip()
        amount = normalize_amount(row.get("Credit"))
        libelle = str(row.get("Libellé", "") or "")
        info = str(row.get("Informations Complémentaires", "") or "")
        date = parse_date(row.get("Date comptable"))
        op_type = str(row.get("Type d'opération", "") or "")
        comment = str(row.get("Commentaire", "") or "")
        btext = bank_text(row)

        print(f"[ligne {idx}]  compte={acct!r}  crédit={amount}  date={date}")
        print(f"  op_type  : {op_type}")
        print(f"  libellé  : {libelle}")
        print(f"  info_comp: {info}")
        print(f"  commentaire: {comment!r}")

        # 1. Exclusion par type ou libellé
        if is_excluded_bank_row(row):
            label = get_bank_row_label(row)
            op_code = op_type.split("-")[0].strip()
            if op_code in {"12", "63", "77", "99"}:
                print(f"  → EXCLUE (type op {op_code} dans EXCLUDED_OPERATION_TYPES)")
            elif any(kw.upper() in (libelle + " " + info).upper() for kw in ["11 INVEST", "FACTOR"]):
                print("  → EXCLUE silencieusement (mot-clé SILENT_LIBELLE_KEYWORDS)")
            else:
                print(f"  → EXCLUE (étiquetée {label!r} via LABELED)")
            continue

        # 2. Commentaire dans le fichier source
        if comment:
            print("  → EXCLUE (Commentaire renseigné dans le source)")
            continue

        # 3. Commentaire issu d'un output précédent
        key = (str(row.get("Date comptable") or ""), libelle, str(row.get("Credit") or ""))
        if previous_comments.get(key):
            print(f"  → EXCLUE (commentaire output précédent : {previous_comments[key]!r})")
            continue

        if not amount:
            print("  → IGNORÉE (pas de crédit)")
            continue

        # 4. Factures candidates (même compte, même montant)
        candidates = inv_by_acct_amount.get((acct, amount), [])
        print(f"\n  Factures avec compte={acct!r} et montant={amount} : {len(candidates)}")
        for inv in candidates:
            inv_client = str(inv.get("Client", "") or "").strip()
            inv_num = str(inv.get("Numéro de facture", "") or "").strip()
            inv_status = str(inv.get("Statut", "") or "")
            pay_date = parse_date(inv.get("Date de paiement"))
            sim = client_similarity(inv_client, btext, client_mapping)
            score = compute_score(
                invoice_found=inv_num in extract_invoice_numbers(btext),
                amount_match=True,
                account_match=True,
                client_sim=sim,
            )
            dt_pay = to_date(pay_date)
            dt_bank = to_date(date)
            date_ok = not pay_date or (
                dt_pay is not None and dt_bank is not None
                and abs((dt_pay - dt_bank).days) <= 5
            )
            before = payment_before_emission(date, inv)
            print(
                f"    {inv_num:<16} {inv_client:<25} statut={inv_status:<12} "
                f"pay_date={pay_date or 'None':<12} date_ok={date_ok}  before={before}  sim={sim:.0f}  score={score}"
            )

        # 5. Numéros de facture extraits du libellé
        found_nums = extract_invoice_numbers(btext)
        print(f"\n  Numéros extraits du texte bancaire : {found_nums or '(aucun)'}")

        # 6. Vue clients LCL (compte le plus agrégé)
        lcl_acct = ACCOUNT_MAPPING.get("LCL", "")
        if acct == lcl_acct:
            ef_invs = [
                inv for inv in matchable
                if resolve_bank_account(inv, account_overrides) == acct
            ]
            ef_by_client: dict[str, list[dict]] = {}
            for inv in ef_invs:
                c = str(inv.get("Client", "") or "").strip()
                ef_by_client.setdefault(c, []).append(inv)
            print("\n  Clients LCL avec factures :")
            for client, invs in ef_by_client.items():
                amounts = [normalize_amount(i.get("Total à facturer (TTC)")) or 0 for i in invs]
                total = sum(amounts)
                print(
                    f"    {client:<25}  {len(invs)} factures  "
                    f"total={total:.2f}  diff_vs_crédit={abs(total - amount):.2f}"
                )
        print()

    print(SEP)

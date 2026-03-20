"""Algorithme de réconciliation en trois passes."""

from collections import defaultdict
from itertools import combinations
from typing import Optional

from .config import (
    DATE_TOLERANCE_AMOUNT_DATE,
    THRESHOLD_REVIEW,
)
from .loaders import (
    dates_close,
    normalize_amount,
    parse_date,
    payment_before_emission,
    resolve_bank_account,
    to_date,
)
from .matching import (
    bank_text,
    build_credits_by_account,
    client_similarity,
    compute_score,
    extract_invoice_numbers,
)


# ─── Helper combo ─────────────────────────────────────────────────────────────


def find_matching_combo(
    eligible: list[dict],
    target_amount: float,
    br: dict,
    br_date: Optional[str],
    btext: str,
    bank_acct: str,
    client_mapping: dict[str, list[str]],
    account_overrides: dict[str, str],
    erp_matches: dict[int, dict],
    bank_matches: dict[int, list[str]],
    used_bank: set[int],
    is_cross_account: bool = False,
    eligible_avoirs: list[dict] | None = None,
) -> bool:
    """Cherche une combinaison de factures (et optionnellement d'avoirs) dont
    la somme nette TTC = target_amount.

    Formule : Σfactures − Σavoirs = crédit_banque
    Parcourt d'abord les combos de factures seules (taille 2 à 10), puis si
    des avoirs sont fournis, les combos factures + avoirs (facture taille 1
    à 10, avoir taille 1 à 3). Si une combinaison est trouvée, enregistre
    les matches dans erp_matches, bank_matches et used_bank.

    Args:
        eligible: factures candidates (déjà filtrées par date/statut).
        target_amount: montant du crédit bancaire à atteindre.
        br: ligne bancaire source.
        br_date: date comptable du crédit (string).
        btext: texte libre de la ligne bancaire.
        bank_acct: intitulé du compte bancaire de la ligne.
        client_mapping: aliases banque des clients.
        account_overrides: overrides de compte par client.
        erp_matches: dict de résultats ERP (modifié en place).
        bank_matches: dict de résultats banque (modifié en place).
        used_bank: indices bancaires utilisés (modifié en place).
        is_cross_account: True si comptes ERP différents du compte banque.
        eligible_avoirs: avoirs du même client à déduire (sans date paiement).

    Returns:
        True si une combinaison a été trouvée et enregistrée.
    """
    br_idx = br["_row_idx"]
    ref_bancaire = str(
        br.get("Référence") or br.get("Numéro de piece") or ""
    )
    bank_libelle = str(br.get("Libellé", "") or "")
    bank_credit = normalize_amount(br.get("Credit"))

    # ── Combos factures seules ────────────────────────────────────────────────
    for size in range(2, min(10, len(eligible) + 1)):
        for combo in combinations(eligible, size):
            total = sum(
                normalize_amount(i.get("Total à facturer (TTC)")) or 0
                for i in combo
            )
            if abs(total - target_amount) < 0.02:
                nums = [
                    str(i.get("Numéro de facture", "") or "").strip()
                    for i in combo
                ]
                for inv in combo:
                    inv_idx = inv["_row_idx"]
                    inv_num = str(
                        inv.get("Numéro de facture", "") or ""
                    ).strip()
                    inv_client = str(inv.get("Client", "") or "").strip()
                    mapped_acct = resolve_bank_account(
                        inv, account_overrides
                    )
                    sim = client_similarity(
                        inv_client, btext, client_mapping
                    )
                    others = [n for n in nums if n != inv_num]
                    note = (
                        f"groupé avec {', '.join(others)}" if others else ""
                    )
                    if (
                        is_cross_account
                        and mapped_acct.lower() != bank_acct.lower()
                    ):
                        note += (
                            f" | compte ERP={mapped_acct or '?'}"
                            f" / banque={bank_acct}"
                        )
                    account_match = (
                        not is_cross_account
                        or mapped_acct.lower() == bank_acct.lower()
                    )
                    erp_matches[inv_idx] = {
                        "date_constatee": br_date,
                        "ref_bancaire": ref_bancaire,
                        "score": compute_score(
                            invoice_found=(
                                inv_num in extract_invoice_numbers(btext)
                            ),
                            amount_match=True,
                            account_match=account_match,
                            client_sim=sim,
                        ),
                        "note": note,
                        "bank_libelle": bank_libelle,
                        "bank_credit": bank_credit,
                    }
                bank_matches.setdefault(br_idx, []).extend(nums)
                used_bank.add(br_idx)
                return True

    # ── Combos factures + avoirs ──────────────────────────────────────────────
    # Σfactures − Σavoirs = target  →  Σfactures = target + Σavoirs
    avoirs = eligible_avoirs or []
    if not avoirs:
        return False
    for av_size in range(1, min(4, len(avoirs) + 1)):
        for av_combo in combinations(avoirs, av_size):
            av_total = sum(
                normalize_amount(a.get("Total à facturer (TTC)")) or 0
                for a in av_combo
            )
            adjusted = target_amount + av_total
            av_nums = [
                str(a.get("Numéro de facture", "") or "").strip()
                for a in av_combo
            ]
            avoir_tag = f"avoir déduit : {', '.join(av_nums)}"
            for size in range(1, min(10, len(eligible) + 1)):
                for combo in combinations(eligible, size):
                    total = sum(
                        normalize_amount(
                            i.get("Total à facturer (TTC)")
                        ) or 0
                        for i in combo
                    )
                    if abs(total - adjusted) < 0.02:
                        nums = [
                            str(
                                i.get("Numéro de facture", "") or ""
                            ).strip()
                            for i in combo
                        ]
                        for inv in combo:
                            inv_idx = inv["_row_idx"]
                            inv_num = str(
                                inv.get("Numéro de facture", "") or ""
                            ).strip()
                            inv_client = str(
                                inv.get("Client", "") or ""
                            ).strip()
                            mapped_acct = resolve_bank_account(
                                inv, account_overrides
                            )
                            sim = client_similarity(
                                inv_client, btext, client_mapping
                            )
                            others = [n for n in nums if n != inv_num]
                            note_parts: list[str] = []
                            if others:
                                note_parts.append(
                                    f"groupé avec {', '.join(others)}"
                                )
                            note_parts.append(avoir_tag)
                            note = " | ".join(note_parts)
                            if (
                                is_cross_account
                                and mapped_acct.lower() != bank_acct.lower()
                            ):
                                note += (
                                    f" | compte ERP={mapped_acct or '?'}"
                                    f" / banque={bank_acct}"
                                )
                            account_match = (
                                not is_cross_account
                                or mapped_acct.lower() == bank_acct.lower()
                            )
                            erp_matches[inv_idx] = {
                                "date_constatee": br_date,
                                "ref_bancaire": ref_bancaire,
                                "score": compute_score(
                                    invoice_found=(
                                        inv_num
                                        in extract_invoice_numbers(btext)
                                    ),
                                    amount_match=True,
                                    account_match=account_match,
                                    client_sim=sim,
                                ),
                                "note": note,
                                "bank_libelle": bank_libelle,
                                "bank_credit": bank_credit,
                            }
                        for av in av_combo:
                            av_idx = av["_row_idx"]
                            av_num = str(
                                av.get("Numéro de facture", "") or ""
                            ).strip()
                            av_client = str(
                                av.get("Client", "") or ""
                            ).strip()
                            sim = client_similarity(
                                av_client, btext, client_mapping
                            )
                            av_note = (
                                "avoir déduit, groupé avec "
                                f"{', '.join(nums)}"
                            )
                            erp_matches[av_idx] = {
                                "date_constatee": br_date,
                                "ref_bancaire": ref_bancaire,
                                "score": compute_score(
                                    invoice_found=(
                                        av_num
                                        in extract_invoice_numbers(btext)
                                    ),
                                    amount_match=True,
                                    account_match=True,
                                    client_sim=sim,
                                ),
                                "note": av_note,
                                "bank_libelle": bank_libelle,
                                "bank_credit": bank_credit,
                            }
                        bank_matches.setdefault(br_idx, []).extend(nums)
                        bank_matches[br_idx].extend(av_nums)
                        used_bank.add(br_idx)
                        return True
    return False


# ─── Passe 1 ──────────────────────────────────────────────────────────────────


def pass1_bank_to_invoices(
    all_invoices: list[dict],
    bank_rows: list[dict],
    client_mapping: dict[str, list[str]],
    account_overrides: dict[str, str],
    previous_comments: dict[tuple, str],
    erp_matches: dict[int, dict],
    bank_matches: dict[int, list[str]],
    used_bank: set[int],
    credit_notes: list[dict] | None = None,
) -> None:
    """Pour chaque crédit bancaire, cherche la ou les factures correspondantes.

    Parcourt TOUTES les factures (tous statuts). Modifie erp_matches,
    bank_matches et used_bank en place (initialisés par pass0).

    Sous-passes dans l'ordre de priorité :
    - A : numéro de facture explicite dans le texte bancaire
    - B : montant exact + même compte + similarité client
    - B2 : cross-account (client reconnu >= 80, compte différent)
    - B3 : montant exact + date paiement ERP ±3j, candidat unique
    - C : combo de N factures même client/compte (+ avoirs éventuels)
    - C2 : combo cross-account (client reconnu >= 80, + avoirs éventuels)

    Args:
        all_invoices: toutes les factures ERP (hors annulées/avoirs).
        bank_rows: toutes les lignes bancaires.
        client_mapping: aliases banque des clients.
        account_overrides: overrides de compte par client.
        previous_comments: commentaires manuels du dernier output.
        erp_matches: dict résultats ERP (modifié en place).
        bank_matches: dict résultats banque (modifié en place).
        used_bank: indices bancaires utilisés (modifié en place).
        credit_notes: avoirs sans date de paiement (déduction possible).
    """
    credits_by_acct = build_credits_by_account(bank_rows, previous_comments)

    # Index factures par numéro
    inv_by_num: dict[str, dict] = {}
    for inv in all_invoices:
        num = str(inv.get("Numéro de facture", "") or "").strip()
        if num:
            inv_by_num[num] = inv

    # Index factures par (compte_banque, montant) pour match rapide
    inv_by_acct_amount: dict[tuple[str, float], list[dict]] = {}
    for inv in all_invoices:
        amount = normalize_amount(inv.get("Total à facturer (TTC)"))
        bank_acct = resolve_bank_account(inv, account_overrides)
        if amount and bank_acct:
            inv_by_acct_amount.setdefault(
                (bank_acct, amount), []
            ).append(inv)

    # Index factures par montant seul (tous comptes) pour cross-account
    inv_by_amount: dict[float, list[dict]] = {}
    for inv in all_invoices:
        amount = normalize_amount(inv.get("Total à facturer (TTC)"))
        if amount:
            inv_by_amount.setdefault(amount, []).append(inv)

    # Index avoirs par client (pour les combos avec déduction)
    avoirs_by_client: dict[str, list[dict]] = {}
    for av in (credit_notes or []):
        client = str(av.get("Client", "") or "").strip()
        if client:
            avoirs_by_client.setdefault(client, []).append(av)

    # ── Sous-passe A : numéro de facture dans le texte → indice client ──────
    for bank_acct, br_list in credits_by_acct.items():
        for br in br_list:
            br_idx = br["_row_idx"]
            btext = bank_text(br)
            found_nums = extract_invoice_numbers(btext)
            matched = [inv_by_num[n] for n in found_nums if n in inv_by_num]
            if not matched:
                continue

            br_amount = normalize_amount(br.get("Credit"))
            br_date = parse_date(br.get("Date comptable"))

            anchor = matched[0]
            anchor_client = str(anchor.get("Client", "") or "").strip()
            anchor_acct = resolve_bank_account(anchor, account_overrides)

            # Toutes les factures du même client/compte, non utilisées.
            # Si le numéro est explicitement en banque → bypass date.
            same_client_invs = [
                inv for inv in all_invoices
                if str(inv.get("Client", "") or "").strip() == anchor_client
                and resolve_bank_account(
                    inv, account_overrides
                ) == anchor_acct
                and inv["_row_idx"] not in erp_matches
                and not payment_before_emission(br_date, inv)
                and normalize_amount(
                    inv.get("Total à facturer (TTC)")
                ) is not None
                and (
                    str(
                        inv.get("Numéro de facture", "") or ""
                    ).strip() in found_nums
                    or not inv.get("Date de paiement")
                    or dates_close(
                        parse_date(inv.get("Date de paiement")), br_date
                    )
                )
            ]

            assigned: list[dict] = []
            if br_amount:
                for size in range(1, min(10, len(same_client_invs) + 1)):
                    if assigned:
                        break
                    for combo in combinations(same_client_invs, size):
                        total = sum(
                            normalize_amount(
                                i.get("Total à facturer (TTC)")
                            ) or 0
                            for i in combo
                        )
                        if abs(total - br_amount) < 0.02:
                            assigned = list(combo)
                            break

            if not assigned:
                assigned = [
                    inv for inv in matched
                    if inv["_row_idx"] not in erp_matches
                    and not payment_before_emission(br_date, inv)
                ]

            if not assigned:
                continue

            nums_assigned = [
                str(i.get("Numéro de facture", "") or "").strip()
                for i in assigned
            ]
            found_num_set = {
                str(i.get("Numéro de facture", "") or "").strip()
                for i in matched
            }

            for inv in assigned:
                inv_idx = inv["_row_idx"]
                inv_amount = normalize_amount(
                    inv.get("Total à facturer (TTC)")
                )
                inv_client = str(inv.get("Client", "") or "").strip()
                mapped_acct = resolve_bank_account(inv, account_overrides)
                inv_num = str(
                    inv.get("Numéro de facture", "") or ""
                ).strip()
                account_match = mapped_acct.lower() == bank_acct.lower()
                # Combo : la somme totale correspond au crédit → amount_match True
                # même si le montant individuel diffère du crédit bancaire.
                amount_match = br_amount is not None and (
                    len(assigned) > 1
                    or (inv_amount is not None and abs(br_amount - inv_amount) < 0.01)
                )
                invoice_found = inv_num in found_num_set
                sim = client_similarity(inv_client, btext, client_mapping)
                others = [n for n in nums_assigned if n != inv_num]
                note = (
                    f"groupé avec {', '.join(others)}" if others else ""
                )
                erp_matches[inv_idx] = {
                    "date_constatee": br_date,
                    "ref_bancaire": (
                        br.get("Référence")
                        or br.get("Numéro de piece")
                        or ""
                    ),
                    "score": compute_score(
                        invoice_found=invoice_found,
                        amount_match=amount_match,
                        account_match=account_match,
                        client_sim=sim,
                    ),
                    "note": note,
                    "bank_libelle": str(br.get("Libellé", "") or ""),
                    "bank_credit": normalize_amount(br.get("Credit")),
                }
                bank_matches.setdefault(br_idx, []).append(inv_num)

            used_bank.add(br_idx)

    # ── Sous-passe B : montant exact + compte + similarité client ────────────
    for bank_acct, br_list in credits_by_acct.items():
        for br in br_list:
            br_idx = br["_row_idx"]
            if br_idx in used_bank:
                continue
            br_amount = normalize_amount(br.get("Credit"))
            if not br_amount:
                continue
            br_date = parse_date(br.get("Date comptable"))
            btext = bank_text(br)

            candidates = inv_by_acct_amount.get((bank_acct, br_amount), [])
            if not candidates:
                continue

            best: Optional[tuple[int, dict]] = None
            for inv in candidates:
                if inv["_row_idx"] in erp_matches:
                    continue
                if payment_before_emission(br_date, inv):
                    continue
                payment_date = parse_date(inv.get("Date de paiement"))
                if payment_date and not dates_close(payment_date, br_date):
                    continue
                inv_client = str(inv.get("Client", "") or "").strip()
                sim = client_similarity(inv_client, btext, client_mapping)
                score = compute_score(
                    invoice_found=False,
                    amount_match=True,
                    account_match=True,
                    client_sim=sim,
                )
                if score >= THRESHOLD_REVIEW and (
                    best is None or score > best[0]
                ):
                    best = (score, inv)

            if best:
                score, inv = best
                inv_idx = inv["_row_idx"]
                inv_num = str(
                    inv.get("Numéro de facture", "") or ""
                ).strip()
                erp_matches[inv_idx] = {
                    "date_constatee": br_date,
                    "ref_bancaire": (
                        br.get("Référence")
                        or br.get("Numéro de piece")
                        or ""
                    ),
                    "score": score,
                    "note": "",
                    "bank_libelle": str(br.get("Libellé", "") or ""),
                    "bank_credit": normalize_amount(br.get("Credit")),
                }
                bank_matches.setdefault(br_idx, []).append(inv_num)
                used_bank.add(br_idx)

    # ── Sous-passe B2 : cross-account — client reconnu, compte différent ─────
    # Quand aucun candidat n'existe sur le même compte, cherche sur tous les
    # comptes avec client_sim >= 80 (virement arrive sur un autre compte).
    for bank_acct, br_list in credits_by_acct.items():
        for br in br_list:
            br_idx = br["_row_idx"]
            if br_idx in used_bank:
                continue
            br_amount = normalize_amount(br.get("Credit"))
            if not br_amount:
                continue
            br_date = parse_date(br.get("Date comptable"))
            btext = bank_text(br)

            same_acct_candidates = inv_by_acct_amount.get(
                (bank_acct, br_amount), []
            )
            if any(
                i["_row_idx"] not in erp_matches
                for i in same_acct_candidates
            ):
                continue

            candidates = inv_by_amount.get(br_amount, [])
            if not candidates:
                continue

            best2: Optional[tuple[int, dict]] = None
            for inv in candidates:
                if inv["_row_idx"] in erp_matches:
                    continue
                if payment_before_emission(br_date, inv):
                    continue
                payment_date = parse_date(inv.get("Date de paiement"))
                if payment_date and not dates_close(payment_date, br_date):
                    continue
                inv_client = str(inv.get("Client", "") or "").strip()
                sim = client_similarity(inv_client, btext, client_mapping)
                if sim < 80:
                    continue
                inv_num = str(
                    inv.get("Numéro de facture", "") or ""
                ).strip()
                mapped_acct = resolve_bank_account(inv, account_overrides)
                score = compute_score(
                    invoice_found=inv_num in extract_invoice_numbers(btext),
                    amount_match=True,
                    account_match=mapped_acct.lower() == bank_acct.lower(),
                    client_sim=sim,
                )
                if best2 is None or score > best2[0]:
                    best2 = (score, inv)

            if best2:
                score, inv = best2
                inv_idx = inv["_row_idx"]
                inv_num = str(
                    inv.get("Numéro de facture", "") or ""
                ).strip()
                mapped_acct = resolve_bank_account(inv, account_overrides)
                erp_matches[inv_idx] = {
                    "date_constatee": br_date,
                    "ref_bancaire": (
                        br.get("Référence")
                        or br.get("Numéro de piece")
                        or ""
                    ),
                    "score": score,
                    "note": (
                        f"compte ERP={mapped_acct or '?'}"
                        f" / banque={bank_acct}"
                    ),
                    "bank_libelle": str(br.get("Libellé", "") or ""),
                    "bank_credit": normalize_amount(br.get("Credit")),
                }
                bank_matches.setdefault(br_idx, []).append(inv_num)
                used_bank.add(br_idx)

    # ── Sous-passe B3 : montant exact + date paiement ERP ±3j, compte libre ──
    # Pour les libellés génériques ("VIREMENT RECU") sans info client/compte.
    # Sécurité : ne matche que si exactement UN candidat correspond.
    for bank_acct, br_list in credits_by_acct.items():
        for br in br_list:
            br_idx = br["_row_idx"]
            if br_idx in used_bank:
                continue
            br_amount = normalize_amount(br.get("Credit"))
            if not br_amount:
                continue
            br_date = parse_date(br.get("Date comptable"))

            date_candidates = [
                inv for inv in inv_by_amount.get(br_amount, [])
                if inv["_row_idx"] not in erp_matches
                and not payment_before_emission(br_date, inv)
                and parse_date(inv.get("Date de paiement")) is not None
                and abs((
                    to_date(inv.get("Date de paiement"))
                    - to_date(br_date)  # type: ignore[operator]
                ).days) <= DATE_TOLERANCE_AMOUNT_DATE
            ]

            if len(date_candidates) != 1:
                continue  # ambiguïté ou aucun candidat → on ne matche pas

            inv = date_candidates[0]
            inv_idx = inv["_row_idx"]
            inv_num = str(inv.get("Numéro de facture", "") or "").strip()
            inv_client = str(inv.get("Client", "") or "").strip()
            mapped_acct = resolve_bank_account(inv, account_overrides)
            btext = bank_text(br)
            sim = client_similarity(inv_client, btext, client_mapping)
            note = "date paiement ERP ±3j"
            if mapped_acct.lower() != bank_acct.lower():
                note += (
                    f" | compte ERP={mapped_acct or '?'}"
                    f" / banque={bank_acct}"
                )
            erp_matches[inv_idx] = {
                "date_constatee": br_date,
                "ref_bancaire": (
                    br.get("Référence") or br.get("Numéro de piece") or ""
                ),
                "score": compute_score(
                    invoice_found=inv_num in extract_invoice_numbers(btext),
                    amount_match=True,
                    account_match=mapped_acct.lower() == bank_acct.lower(),
                    client_sim=sim,
                ),
                "note": note,
                "bank_libelle": str(br.get("Libellé", "") or ""),
                "bank_credit": normalize_amount(br.get("Credit")),
            }
            bank_matches.setdefault(br_idx, []).append(inv_num)
            used_bank.add(br_idx)

    # ── Sous-passe C : regroupement (1 crédit = N factures même client) ──────
    remaining = [
        inv for inv in all_invoices if inv["_row_idx"] not in erp_matches
    ]
    no_acct_by_client: dict[str, list[dict]] = {}
    remaining_by_acct_client: dict[tuple[str, str], list[dict]] = {}
    for inv in remaining:
        client = str(inv.get("Client", "") or "").strip()
        bank_acct = resolve_bank_account(inv, account_overrides)
        if bank_acct and client:
            remaining_by_acct_client.setdefault(
                (bank_acct, client), []
            ).append(inv)
        elif client:
            no_acct_by_client.setdefault(client, []).append(inv)
    # Inclut les factures sans compte dans le groupe de leur client
    for (bank_acct, client), inv_group in remaining_by_acct_client.items():
        inv_group.extend(no_acct_by_client.get(client, []))

    for (bank_acct, client), inv_group in remaining_by_acct_client.items():
        client_avoirs_all = [
            av for av in avoirs_by_client.get(client, [])
        ]
        if len(inv_group) < 2 and not client_avoirs_all:
            continue
        unused = [
            br for br in credits_by_acct.get(bank_acct, [])
            if br["_row_idx"] not in used_bank
        ]
        for br in unused:
            br_amount = normalize_amount(br.get("Credit"))
            if not br_amount:
                continue
            btext = bank_text(br)
            br_date = parse_date(br.get("Date comptable"))

            combo_eligible = [
                inv for inv in inv_group
                if inv["_row_idx"] not in erp_matches
                and not payment_before_emission(br_date, inv)
                and normalize_amount(
                    inv.get("Total à facturer (TTC)")
                ) is not None
                and (
                    not inv.get("Date de paiement")
                    or dates_close(
                        parse_date(inv.get("Date de paiement")), br_date
                    )
                )
            ]
            client_avoirs = [
                av for av in client_avoirs_all
                if av["_row_idx"] not in erp_matches
            ]

            if len(combo_eligible) >= 2 or (
                combo_eligible and client_avoirs
            ):
                find_matching_combo(
                    combo_eligible, br_amount, br, br_date,
                    btext, bank_acct,
                    client_mapping, account_overrides,
                    erp_matches, bank_matches, used_bank,
                    eligible_avoirs=client_avoirs,
                )

    # ── Sous-passe C2 : combos cross-account (client reconnu, compte diff.) ──
    remaining_cross: list[dict] = [
        inv for inv in all_invoices if inv["_row_idx"] not in erp_matches
    ]
    remaining_cross_by_client: dict[str, list[dict]] = {}
    for inv in remaining_cross:
        client = str(inv.get("Client", "") or "").strip()
        if client:
            remaining_cross_by_client.setdefault(client, []).append(inv)

    for bank_acct, br_list in credits_by_acct.items():
        for br in br_list:
            br_idx = br["_row_idx"]
            if br_idx in used_bank:
                continue
            br_amount = normalize_amount(br.get("Credit"))
            if not br_amount:
                continue
            btext = bank_text(br)
            br_date = parse_date(br.get("Date comptable"))

            for client, inv_group in remaining_cross_by_client.items():
                if br_idx in used_bank:
                    break
                sim = client_similarity(client, btext, client_mapping)
                if sim < 80:
                    continue
                combo_eligible = [
                    inv for inv in inv_group
                    if inv["_row_idx"] not in erp_matches
                    and not payment_before_emission(br_date, inv)
                    and normalize_amount(
                        inv.get("Total à facturer (TTC)")
                    ) is not None
                    and (
                        not inv.get("Date de paiement")
                        or dates_close(
                            parse_date(inv.get("Date de paiement")),
                            br_date,
                        )
                    )
                ]
                client_avoirs = [
                    av for av in avoirs_by_client.get(client, [])
                    if av["_row_idx"] not in erp_matches
                ]
                if len(combo_eligible) >= 2 or (
                    combo_eligible and client_avoirs
                ):
                    find_matching_combo(
                        combo_eligible, br_amount, br, br_date,
                        btext, bank_acct,
                        client_mapping, account_overrides,
                        erp_matches, bank_matches, used_bank,
                        is_cross_account=True,
                        eligible_avoirs=client_avoirs,
                    )

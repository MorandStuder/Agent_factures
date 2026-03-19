"""Fonctions utilitaires de correspondance factures ↔ lignes bancaires."""

import re
from typing import Optional

from rapidfuzz import fuzz

from .config import (
    ACCOUNT_MAPPING,
    EXCLUDED_OPERATION_TYPES,
    INVOICE_PATTERN,
    LABELED_LIBELLE_KEYWORDS,
    LABELED_OPERATION_TYPES,
    SCORE_ACCOUNT,
    SCORE_AMOUNT_EXACT,
    SCORE_CLIENT_MAX,
    SCORE_INVOICE_NUM,
    SILENT_LIBELLE_KEYWORDS,
    THRESHOLD_REVIEW,
)
from .loaders import normalize_amount, resolve_bank_account


def bank_text(row: dict) -> str:
    """Concatène les champs texte utiles d'une ligne bancaire.

    Args:
        row: ligne bancaire (dict).

    Returns:
        Texte libre combinant Libellé, Informations Complémentaires et Référence.
    """
    parts = [
        row.get("Libellé", "") or "",
        row.get("Informations Complémentaires", "") or "",
        row.get("Référence", "") or "",
    ]
    return " ".join(str(p) for p in parts)


def extract_invoice_numbers(text: str) -> set[str]:
    """Extrait les numéros de facture depuis un texte.

    Gère les formats :
    - NNNN-NNN, NNNN NNN (séparateur tiret ou espace)
    - NNN-NNN (préfixe 3 chiffres, complété avec "2" → ex: 512 → 2512)
    - 8 chiffres consécutifs sans séparateur (ex: "20252518" → "2025-2518")

    Args:
        text: texte libre bancaire.

    Returns:
        Ensemble de numéros de facture normalisés (ex: {"2025-2518", "2512-2547"}).
    """
    if not text:
        return set()
    result = set()
    for a, b in INVOICE_PATTERN.findall(str(text)):
        prefix = a if len(a) == 4 else f"2{a}"
        result.add(f"{prefix}-{b}")
    # Numéros collés sans séparateur : AAAANNNN (8 chiffres isolés)
    for m in re.finditer(r"(?<!\d)(\d{4})(\d{3,4})(?!\d)", str(text)):
        result.add(f"{m.group(1)}-{m.group(2)}")
    return result


def get_bank_row_label(row: dict) -> Optional[str]:
    """Retourne le label d'exclusion si la ligne correspond à un type ou mot-clé étiqueté.

    Args:
        row: ligne bancaire.

    Returns:
        Label (ex: "CPAM", "URSSAF", "TRESO") ou None si la ligne n'est pas étiquetée.
    """
    op_type = str(row.get("Type d'opération", "") or "")
    op_code = op_type.split("-")[0].strip()
    if op_code in LABELED_OPERATION_TYPES:
        return LABELED_OPERATION_TYPES[op_code]
    libelle = str(row.get("Libellé", "") or "").upper()
    info = str(row.get("Informations Complémentaires", "") or "").upper()
    text = libelle + " " + info
    for kw, label in LABELED_LIBELLE_KEYWORDS.items():
        if kw.upper() in text:
            return label
    return None


def is_excluded_bank_row(row: dict) -> bool:
    """Retourne True si la ligne bancaire doit être exclue du rapprochement.

    Exclusions :
    - Type d'opération dans EXCLUDED_OPERATION_TYPES (silencieux)
    - Libellé contenant un mot-clé SILENT_LIBELLE_KEYWORDS (silencieux)
    - Libellé contenant un mot-clé LABELED_LIBELLE_KEYWORDS (étiqueté)

    Args:
        row: ligne bancaire.

    Returns:
        True si la ligne doit être ignorée.
    """
    op_type = str(row.get("Type d'opération", "") or "")
    op_code = op_type.split("-")[0].strip()
    if op_code in EXCLUDED_OPERATION_TYPES:
        return True
    libelle = str(row.get("Libellé", "") or "").upper()
    info = str(row.get("Informations Complémentaires", "") or "").upper()
    text = libelle + " " + info
    if any(kw.upper() in text for kw in SILENT_LIBELLE_KEYWORDS):
        return True
    return get_bank_row_label(row) is not None


def client_similarity(
    erp_client: str,
    text: str,
    mapping: dict[str, list[str]],
) -> float:
    """Similarité 0-100 entre nom client ERP et texte libre bancaire.

    Teste tous les aliases banque définis dans client_mapping.csv et retourne
    le meilleur score entre partial_ratio et token_sort_ratio (rapidfuzz).

    Args:
        erp_client: nom du client dans l'ERP.
        text: texte libre de la ligne bancaire.
        mapping: dict erp_client -> [alias_banque, ...].

    Returns:
        Score de similarité entre 0 et 100.
    """
    if not erp_client or not text:
        return 0.0
    aliases = mapping.get(erp_client) or [erp_client]
    lower_text = text.lower()
    best = 0.0
    for name in aliases:
        lower_name = name.lower()
        score = float(max(
            fuzz.partial_ratio(lower_name, lower_text),
            fuzz.token_sort_ratio(lower_name, lower_text),
        ))
        if score > best:
            best = score
    return best


def compute_score(
    invoice_found: bool,
    amount_match: bool,
    account_match: bool,
    client_sim: float,
) -> int:
    """Calcule le score de confiance 0-100.

    Barème :
    - Numéro de facture trouvé : +40
    - Montant exact : +30
    - Même compte bancaire : +15
    - Similarité client >= 80 : +15, >= 60 : +10, >= 40 : +5

    Args:
        invoice_found: True si le numéro de facture est dans le libellé bancaire.
        amount_match: True si le montant correspond exactement.
        account_match: True si le compte bancaire correspond.
        client_sim: score de similarité client (0-100).

    Returns:
        Score de confiance entre 0 et 100.
    """
    score = 0
    if invoice_found:
        score += SCORE_INVOICE_NUM
    if amount_match:
        score += SCORE_AMOUNT_EXACT
    if account_match:
        score += SCORE_ACCOUNT
    if client_sim >= 80:
        score += SCORE_CLIENT_MAX
    elif client_sim >= 60:
        score += 10
    elif client_sim >= 40:
        score += 5
    return min(score, 100)


def build_credits_by_account(
    bank_rows: list[dict],
    previous_comments: dict[tuple, str],
) -> dict[str, list[dict]]:
    """Construit un index des crédits bancaires non commentés par intitulé de compte.

    Exclut :
    - Lignes sans crédit positif
    - Lignes avec commentaire dans le fichier source
    - Lignes avec commentaire dans le dernier output annoté manuellement
    - Lignes exclues par type d'opération ou mot-clé libellé

    Args:
        bank_rows: toutes les lignes bancaires.
        previous_comments: commentaires chargés depuis le dernier output.

    Returns:
        Dict {intitulé_compte -> [lignes bancaires eligibles]}.
    """
    index: dict[str, list[dict]] = {}
    for row in bank_rows:
        amount = normalize_amount(row.get("Credit"))
        if not (amount and amount > 0):
            continue
        if row.get("Commentaire"):
            continue
        key = (
            str(row.get("Date comptable") or ""),
            str(row.get("Libellé") or ""),
            str(row.get("Credit") or ""),
        )
        if previous_comments.get(key):
            continue
        if is_excluded_bank_row(row):
            continue
        acct = str(row.get("Intitulé du compte", "") or "").strip()
        index.setdefault(acct, []).append(row)
    return index

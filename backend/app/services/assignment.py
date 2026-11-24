def rank_candidates(candidates: list[dict], etas: list[float], limit: int = 3) -> list[dict]:
    """
    Rank candidates by estimated time of arrival (ETA).

    Args:
        candidates: List of candidate dicts (e.g., trucks with metadata)
        etas: Parallel list of ETA values in seconds, same length as candidates
        limit: Maximum number of candidates to return (default 3)

    Returns:
        List of candidates sorted by ETA ascending (fastest first), sliced to limit.
        Each candidate gets an added 'eta_seconds' key. Original candidates dicts
        are not mutated (shallow copies are made).
    """
    # Zip candidates with their ETAs
    zipped = zip(candidates, etas)

    # Create shallow copies with eta_seconds attached, then sort by eta ascending
    ranked = sorted(
        (dict(candidate, eta_seconds=eta) for candidate, eta in zipped),
        key=lambda c: c["eta_seconds"]
    )

    # Return sliced to limit
    return ranked[:limit]

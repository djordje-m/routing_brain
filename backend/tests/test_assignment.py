from app.services.assignment import rank_candidates


def test_sorts_ascending_by_eta_and_limits_to_3():
    candidates = [{"truck_id": 1}, {"truck_id": 2}, {"truck_id": 3}, {"truck_id": 4}]
    etas = [300.0, 100.0, 200.0, 50.0]
    result = rank_candidates(candidates, etas, limit=3)
    assert [c["truck_id"] for c in result] == [4, 2, 3]
    assert result[0]["eta_seconds"] == 50.0


def test_fewer_than_limit_candidates_returns_all():
    candidates = [{"truck_id": 1}]
    etas = [42.0]
    result = rank_candidates(candidates, etas, limit=3)
    assert len(result) == 1
    assert result[0]["eta_seconds"] == 42.0


def test_empty_candidates_returns_empty():
    assert rank_candidates([], [], limit=3) == []

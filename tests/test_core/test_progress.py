from core.progress import StageTracker


def test_stage_tracker_plain_prints_eta(capsys):
    tracker = StageTracker(total=2, mode="plain")

    tracker.start("Ingestion")
    tracker.advance("Ingestion")
    tracker.close()

    out = capsys.readouterr().out
    assert "Progress: 0/2 Ingestion started" in out
    assert "Progress: 1/2 Ingestion done" in out
    assert "elapsed" in out
    assert "ETA" in out

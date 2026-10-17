from app.services.readiness import check_storage


def test_storage_round_trip_leaves_no_files(tmp_path):
    assert check_storage(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_storage_rejects_file(tmp_path):
    path = tmp_path / "existing.txt"
    path.write_text("preserve")
    assert not check_storage(path)
    assert path.read_text() == "preserve"

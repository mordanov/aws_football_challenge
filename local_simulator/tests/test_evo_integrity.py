from football_lab.evolution.integrity import hash_tree, verify_unchanged


def test_hash_changes_on_content_or_name_and_ignores_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x")
    base = hash_tree(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"junk")
    assert hash_tree(tmp_path) == base
    (tmp_path / "a.py").write_text("y")
    assert hash_tree(tmp_path) != base


def test_verify_unchanged_reports_every_difference(tmp_path):
    d = tmp_path / "opp"
    d.mkdir()
    (d / "a.py").write_text("x")
    recorded = {"opp": hash_tree(d)}
    assert verify_unchanged({"opp": d}, recorded) == []
    (d / "a.py").write_text("tampered")
    assert verify_unchanged({"opp": d}, recorded) == ["opp changed since experiment init"]

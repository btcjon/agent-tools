from shunt.gate import decide, matches_block_glob, count_lines


def test_count_lines():
    assert count_lines("") == 0
    assert count_lines("a") == 1
    assert count_lines("a\n") == 1
    assert count_lines("a\nb") == 2


def test_block_secret_paths():
    assert matches_block_glob(".env")
    assert matches_block_glob("proj/.env.local")
    assert matches_block_glob("secrets/token.txt")
    r = decide(".env", content="x\n" * 400)
    assert r.allow is False
    assert r.reason == "blocked_secret_path"


def test_below_min_lines_blocked():
    body = "\n".join(f"line {i}" for i in range(100))
    r = decide("src/bigish.py", content=body)
    assert r.allow is False
    assert r.reason == "below_min_lines"
    assert r.lines == 100


def test_allow_large_enough_file():
    body = "\n".join(f"line {i}" for i in range(400))
    r = decide("src/large.py", content=body, min_lines=350)
    assert r.allow is True
    assert r.reason == "ok"
    assert r.lines == 400


def test_window_limit_blocks_bulk():
    body = "\n".join(f"line {i}" for i in range(400))
    r = decide("src/large.py", content=body, limit=50, min_lines=350)
    assert r.allow is False
    assert r.reason == "window_smaller_than_min_lines"


def test_exceeds_max_bytes():
    body = "x" * 1000
    r = decide("blob.bin", content=body, min_lines=1, max_bytes=100)
    assert r.allow is False
    assert r.reason == "exceeds_max_bytes"

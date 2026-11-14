"""Russian draughts rules and bounded, independent computer search."""
from concurrent.futures import ThreadPoolExecutor
import time

from fosgen_pets import draughts as d


def board(*pieces):
    cells = [0] * 64
    for row, col, piece in pieces:
        cells[row * 8 + col] = piece
    return tuple(cells)


def test_opening_and_perft():
    start = d.initial_board()
    assert start.count(1) == start.count(-1) == 12
    assert len(d.legal_moves(start, 1)) == 7
    assert sum(len(d.legal_moves(d.apply_move(start, m), -1)) for m in d.legal_moves(start, 1)) == 49
    assert sum(len(d.legal_moves(d.apply_move(d.apply_move(start, m), n), 1)) for m in d.legal_moves(start, 1) for n in d.legal_moves(d.apply_move(start, m), -1)) == 302


def test_mandatory_backward_capture_and_full_chain():
    position = board((3, 2, 1), (4, 3, -1), (4, 5, -1), (6, 7, 1))
    assert d.legal_moves(position, 1) == (d.Move((26, 44, 30), (35, 37)),)
    assert d.apply_move(position, d.legal_moves(position, 1)[0]) == board((3, 6, 1), (6, 7, 1))


def test_promotion_continues_as_flying_king():
    position = board((2, 1, 1), (1, 2, -1), (2, 5, -1))
    moves = d.legal_moves(position, 1)
    assert {move.path for move in moves} == {(17, 3, 30), (17, 3, 39)}
    assert all(d.apply_move(position, move)[move.path[-1]] == 2 for move in moves)


def test_flying_kings_and_captured_pieces_block_reversal():
    position = board((4, 3, 2), (3, 4, -1), (5, 2, -1))
    moves = d.legal_moves(position, 1)
    assert {move.path[-1] for move in moves} == {21, 14, 7, 49, 56}
    assert all(len(move.captured) == 1 for move in moves)


def test_capture_choice_does_not_require_maximum_length():
    position = board((5, 0, 1), (4, 1, -1), (2, 3, -1), (7, 6, 1), (6, 5, -1))
    assert {len(move.captured) for move in d.legal_moves(position, 1)} == {1, 2}


def test_king_quiet_moves_and_promotion():
    assert len(d.legal_moves(board((4, 3, 2)), 1)) == 13
    position = board((1, 2, 1))
    assert all(d.apply_move(position, move)[move.path[-1]] == 2 for move in d.legal_moves(position, 1))


def test_search_forced_capture_and_no_moves():
    position = board((3, 2, 1), (4, 3, -1), (4, 5, -1))
    assert d.choose_move(position, 1, time_limit=0.03) == d.Move((26, 44, 30), (35, 37))
    assert d.choose_move(board((0, 1, 1)), 1) is None


def test_search_avoids_immediate_recapture():
    position = board((5, 2, 1), (3, 0, -1))
    move = d.choose_move(position, 1, time_limit=0.1, max_depth=5)
    assert move.path == (42, 35)


def test_search_deadline_and_concurrent_deterministic_calls():
    position = d.initial_board()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        moves = list(executor.map(lambda _: d.choose_move(position, 1, time_limit=0.2, max_depth=2), range(4)))
    assert time.monotonic() - started < 1.5
    assert all(move in d.legal_moves(position, 1) for move in moves)
    assert len(set(moves)) == 1
    assert d.choose_move(position, 1, time_limit=0) in d.legal_moves(position, 1)


def test_deep_search_stops_at_time_budget():
    position = d.initial_board()
    started = time.monotonic()
    move = d.choose_move(position, 1, time_limit=0.025, max_depth=32)
    assert time.monotonic() - started < 0.4
    assert move in d.legal_moves(position, 1)


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(function) for name, function in globals().items() if name.startswith('test_'))


if __name__ == '__main__':
    import unittest
    unittest.main()

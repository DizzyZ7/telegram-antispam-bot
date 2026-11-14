"""Pure Russian draughts rules and a bounded, deterministic alpha-beta player.

Squares use row-major indices on an 8×8 board. White starts at the bottom.
Captured pieces block rays until a complete move ends (the Turkish strike rule).
Search state is local to each call, so games and worker threads remain isolated.
"""
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True, slots=True)
class Move:
    path: tuple[int, ...]
    captured: tuple[int, ...] = ()


_DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_MATE = 100_000
_NODE_LIMIT = 120_000
_TT_LIMIT = 40_000


def initial_board() -> tuple[int, ...]:
    return tuple((-1 if row < 3 else 1 if row > 4 else 0)
                 if (row + col) % 2 else 0
                 for row in range(8) for col in range(8))


def _promote(piece: int, square: int) -> int:
    if piece == 1 and square < 8:
        return 2
    if piece == -1 and square >= 56:
        return -2
    return piece


def legal_moves(board: tuple[int, ...], side: int) -> tuple[Move, ...]:
    """Return complete moves; all capture alternatives are legal, regardless of length."""
    if len(board) != 64 or side not in (-1, 1):
        raise ValueError("An 8×8 board and side +1 or -1 are required")
    captures: list[Move] = []
    cells = list(board)

    def extend(square: int, piece: int, path: tuple[int, ...], taken: tuple[int, ...]) -> None:
        row, col = divmod(square, 8)
        continued = False
        for dr, dc in _DIRECTIONS:
            r, c = row + dr, col + dc
            if abs(piece) == 1:
                rr, cc = r + dr, c + dc
                if not (0 <= rr < 8 and 0 <= cc < 8 and 0 <= r < 8 and 0 <= c < 8):
                    continue
                victim, landing = r * 8 + c, rr * 8 + cc
                if cells[victim] * piece >= 0 or victim in taken or cells[landing]:
                    continue
                options = ((victim, landing),)
            else:
                while 0 <= r < 8 and 0 <= c < 8 and cells[r * 8 + c] == 0:
                    r, c = r + dr, c + dc
                if not (0 <= r < 8 and 0 <= c < 8):
                    continue
                victim = r * 8 + c
                if cells[victim] * piece >= 0 or victim in taken:
                    continue
                r, c = r + dr, c + dc
                landings = []
                while 0 <= r < 8 and 0 <= c < 8 and cells[r * 8 + c] == 0:
                    landings.append((victim, r * 8 + c))
                    r, c = r + dr, c + dc
                options = landings
            for victim, landing in options:
                continued = True
                promoted = _promote(piece, landing)
                cells[square], cells[landing] = 0, promoted
                extend(landing, promoted, path + (landing,), taken + (victim,))
                cells[square], cells[landing] = piece, 0
        if taken and not continued:
            captures.append(Move(path, taken))

    for square, piece in enumerate(board):
        if piece * side > 0:
            extend(square, piece, (square,), ())
    if captures:
        return tuple(captures)
    quiet: list[Move] = []
    for square, piece in enumerate(board):
        if piece * side <= 0:
            continue
        row, col = divmod(square, 8)
        for dr, dc in _DIRECTIONS:
            if abs(piece) == 1 and dr != -side:
                continue
            r, c = row + dr, col + dc
            while 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == 0:
                quiet.append(Move((square, r * 8 + c)))
                if abs(piece) == 1:
                    break
                r, c = r + dr, c + dc
    return tuple(quiet)


def apply_move(board: tuple[int, ...], move: Move) -> tuple[int, ...]:
    """Apply a move obtained from legal_moves, leaving the input untouched."""
    cells = list(board)
    piece = cells[move.path[0]]
    cells[move.path[0]] = 0
    for square in move.captured:
        cells[square] = 0
    for square in move.path[1:]:
        piece = _promote(piece, square)
    cells[move.path[-1]] = piece
    return tuple(cells)


def _evaluate(board: tuple[int, ...], side: int, mobility: int) -> int:
    score = 0
    count = sum(bool(piece) for piece in board)
    for square, piece in enumerate(board):
        if not piece:
            continue
        row, col = divmod(square, 8)
        owner = 1 if piece > 0 else -1
        center = 7 - abs(2 * col - 7) + 7 - abs(2 * row - 7)
        if abs(piece) == 2:
            value = 340 + center * (3 if count < 10 else 2)
        else:
            advance = 7 - row if owner == 1 else row
            value = 100 + advance * (7 if count < 10 else 4) + center
            if advance == 0:
                value += 7  # Back rank guards promotion lanes.
            for dc in (-1, 1):
                rr, cc = row + owner, col + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and board[rr * 8 + cc] * owner > 0:
                    value += 4
        score += owner * value
    # Cheap geometric mobility for the opponent avoids another capture-tree expansion.
    opponent_mobility = 0
    for square, piece in enumerate(board):
        if piece * side >= 0:
            continue
        row, col = divmod(square, 8)
        for dr, dc in _DIRECTIONS:
            if abs(piece) == 1 and dr != side:
                continue
            r, c = row + dr, col + dc
            if 0 <= r < 8 and 0 <= c < 8 and not board[r * 8 + c]:
                opponent_mobility += 1
    return score * side + 2 * (min(mobility, 24) - opponent_mobility)


class _SearchExpired(Exception):
    pass


def choose_move(board: tuple[int, ...], side: int, time_limit: float = 0.7,
                max_depth: int = 12) -> Move | None:
    """Iterative deepening with forced-capture quiescence and per-call resources.

    The first legal move is an immediate fallback for zero budget. Completed
    iterations replace it atomically. A node cap bounds work even with a huge
    caller budget; this is a strong club-style player, not a solved-game oracle.
    """
    started = time.monotonic()
    moves = legal_moves(board, side)
    if not moves:
        return None
    if len(moves) == 1:
        return moves[0]
    budget = float(time_limit)
    if not math.isfinite(budget):
        budget = 0.7
    deadline = started + max(0.0, min(budget, 5.0))
    depth_limit = max(1, min(int(max_depth), 32))
    table: dict[tuple[tuple[int, ...], int], tuple[int, int, int, Move | None]] = {}
    nodes = 0
    repetitions = 0
    best = moves[0]

    def ordered(position: tuple[int, ...], options: tuple[Move, ...], hint: Move | None) -> list[Move]:
        def priority(move: Move) -> int:
            piece = position[move.path[0]]
            return (100_000 if move == hint else 0) + len(move.captured) * 100 + sum(
                abs(position[square]) * 10 for square in move.captured) + (20 if abs(piece) == 1 and (move.path[-1] < 8 or move.path[-1] >= 56) else 0)
        return sorted(options, key=priority, reverse=True)

    def search(position: tuple[int, ...], turn: int, depth: int, alpha: int,
               beta: int, ply: int, ancestors: frozenset) -> int:
        nonlocal nodes, repetitions
        nodes += 1
        if nodes >= _NODE_LIMIT or time.monotonic() >= deadline:
            raise _SearchExpired
        key = (position, turn)
        if key in ancestors:
            repetitions += 1
            return 0
        original_alpha = alpha
        entry = table.get(key)
        hint = entry[3] if entry else None
        if entry and entry[0] >= depth:
            _, value, bound, _ = entry
            if bound == 0:
                return value
            if bound == 1:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if alpha >= beta:
                return value
        options = legal_moves(position, turn)
        if not options:
            return -_MATE + ply
        capturing = bool(options[0].captured)
        if depth <= 0 and not capturing:
            return _evaluate(position, turn, len(options))
        repetition_stamp = repetitions
        value, winning = -_MATE * 2, None
        for move in ordered(position, options, hint):
            candidate = -search(apply_move(position, move), -turn, depth - 1,
                                -beta, -alpha, ply + 1, ancestors | {key})
            if candidate > value:
                value, winning = candidate, move
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        bound = -1 if value <= original_alpha else 1 if value >= beta else 0
        # A repetition anywhere below this node makes its result path-dependent.
        # Mate distances also depend on the root ply, so neither enters the TT.
        if repetitions == repetition_stamp and len(table) < _TT_LIMIT and abs(value) < _MATE - 128:
            table[key] = (depth, value, bound, winning)
        return value

    for depth in range(1, depth_limit + 1):
        try:
            iteration_best = best
            score = -_MATE * 2
            for move in ordered(board, moves, best):
                candidate = -search(apply_move(board, move), -side, depth - 1,
                                    -_MATE * 2, -score, 1, frozenset({(board, side)}))
                if candidate > score:
                    score, iteration_best = candidate, move
            best = iteration_best
            if score >= _MATE - 128:
                break
        except _SearchExpired:
            break
    return best

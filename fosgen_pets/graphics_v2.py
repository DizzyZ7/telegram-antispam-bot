"""Authored, deterministic SVG portraits for the twelve v2 Fosgen Pets.

Each anatomical part has its own silhouette and clip. Coat streams are derived
from a seed and a named namespace; neither global random state nor unrelated
appearance fields can move them. All SVG text comes from this module's finite
catalog or bounded numbers. This is illustration, not a uniqueness guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import cos, pi, sin
from types import MappingProxyType

from .core import Appearance, validate_appearance


@dataclass(frozen=True)
class SpeciesGeometry:
    body: str
    head: str
    legs: str
    tail: str
    ears: str
    eyes: tuple[tuple[int, int], tuple[int, int]]
    forehead: tuple[int, int]
    muzzle: tuple[int, int, int, int]
    bounds: tuple[tuple[str, tuple[int, int, int, int]], ...]
    feet: tuple[tuple[int, int], tuple[int, int]]
    eye_size: tuple[int, int] = (12, 16)

    def bounds_for(self, part: str) -> tuple[int, int, int, int]:
        return next(rect for name, rect in self.bounds if name == part)


def _bounds(body=(139, 279, 235, 144), head=(142, 156, 228, 174),
            legs=(176, 351, 160, 92), tail=(344, 290, 100, 133)):
    return (("body", body), ("head", head), ("legs", legs), ("tail", tail))


# Coordinates are deliberately authored per species. The eyes never inherit a
# pattern transform, and long-muzzled animals have their own face anchors.
SPECIES_GEOMETRY = MappingProxyType({
    "kitten": SpeciesGeometry(
        body="M149 299C160 263 201 254 254 262C308 251 354 274 367 322C381 367 358 415 316 425L189 426C144 416 128 369 149 299Z",
        head="M147 214L137 119Q138 96 158 109L214 151Q256 136 297 152L351 109Q374 96 373 121L363 219C374 277 331 317 256 319C178 319 136 279 147 214Z",
        legs="M179 357Q202 344 225 360L230 418Q230 442 207 443L182 441Q168 433 177 416ZM286 360Q309 345 333 358L335 419Q344 439 326 442L301 443Q280 441 281 421Z",
        tail="M353 353C415 385 440 342 420 302C409 279 388 290 393 311C403 330 395 342 365 329Z",
        ears="M153 129L161 193L201 166ZM311 167L351 130L348 194Z",
        eyes=((213, 238), (297, 238)), forehead=(255, 188), muzzle=(255, 275, 43, 24),
        bounds=_bounds(), feet=((202, 418), (309, 418)),
    ),
    "puppy": SpeciesGeometry(
        body="M153 302C161 267 202 258 256 264C309 256 351 278 363 320Q393 401 322 425L192 426Q124 407 153 302Z",
        head="M166 186C190 157 224 152 256 160Q312 153 345 184C373 176 395 204 387 251Q384 298 355 302L335 283Q306 325 255 324Q199 326 170 286L148 302Q119 294 120 250C113 210 136 179 166 186Z",
        legs="M180 357Q202 346 226 360L229 417Q234 441 211 444L181 442Q163 436 176 417ZM286 359Q310 347 333 360L337 418Q350 438 329 443L301 443Q280 439 283 419Z",
        tail="M355 335C397 341 425 318 416 291Q403 276 395 296Q398 314 357 310Z",
        ears="M153 195C128 205 129 251 146 276Q163 261 170 211ZM342 210Q349 263 367 277C383 251 380 209 357 195Z",
        eyes=((214, 243), (298, 243)), forehead=(256, 192), muzzle=(256, 282, 51, 28),
        bounds=_bounds(head=(121, 157, 267, 169)), feet=((202, 418), (310, 418)),
    ),
    "foal": SpeciesGeometry(
        body="M165 290Q182 262 221 264L284 257Q345 251 367 294L375 362Q374 407 329 419L197 420Q146 405 151 362Z",
        head="M184 174L173 114Q173 96 187 107L220 151Q252 141 281 151L305 105Q319 90 322 112L314 181Q337 205 337 243L329 279Q332 325 258 334Q181 329 177 289L169 240Q165 204 184 174Z",
        legs="M184 352Q202 341 221 355L223 423Q226 444 207 447L185 445Q173 442 179 425ZM292 355Q311 341 330 353L336 424Q344 443 324 446L302 446Q286 443 289 426Z",
        tail="M354 284Q413 285 409 356L394 387Q382 374 387 348Q391 314 355 312Z",
        ears="M185 119L194 166L211 159ZM290 160L311 118L306 176Z",
        eyes=((216, 226), (292, 226)), forehead=(254, 178), muzzle=(255, 290, 63, 36),
        bounds=_bounds(body=(150, 257, 226, 166), head=(169, 101, 168, 234)),
        feet=((202, 424), (312, 424)), eye_size=(11, 15),
    ),
    "calf": SpeciesGeometry(
        body="M149 302Q153 269 209 261Q257 251 305 266Q363 274 374 325L375 371Q363 422 318 428L188 427Q133 412 136 363Z",
        head="M173 185L153 157Q144 135 163 145L190 173Q255 145 320 173L347 145Q365 135 356 157L337 184L381 167Q412 158 401 185Q384 218 350 220Q372 277 329 308Q299 337 255 335Q207 337 176 307Q135 276 157 220Q124 217 108 185Q97 159 127 167Z",
        legs="M177 359Q200 346 225 362L228 422Q234 443 210 446L180 444Q164 437 175 419ZM289 362Q312 347 336 360L341 420Q352 440 330 445L301 445Q279 441 284 423Z",
        tail="M363 308Q405 306 400 367L413 385Q401 405 389 391Q378 378 384 354Q385 332 361 334Z",
        ears="M123 178Q139 173 169 198L159 209Q135 208 123 178ZM344 199Q374 174 387 178Q378 209 352 209Z",
        eyes=((207, 236), (303, 236)), forehead=(255, 190), muzzle=(255, 292, 73, 37),
        bounds=_bounds(body=(136, 260, 240, 169), head=(105, 146, 300, 192)),
        feet=((201, 422), (313, 422)),
    ),
    "bunny": SpeciesGeometry(
        body="M162 304Q174 273 220 269L287 267Q342 271 356 313Q392 402 323 427L190 427Q127 406 162 304Z",
        head="M179 183C163 129 165 66 188 63Q221 62 228 171Q255 163 283 173C286 88 302 57 321 67Q346 82 328 186Q365 214 367 254Q367 324 256 335Q145 327 145 255Q144 212 179 183Z",
        legs="M173 373Q195 356 222 371L231 421Q233 445 202 447L171 444Q147 437 164 419ZM291 370Q317 356 340 374L350 420Q365 440 340 445L306 447Q280 444 282 425Z",
        tail="M347 353C375 331 407 352 399 380Q391 406 362 394Q342 387 347 353Z",
        ears="M186 82C174 93 183 146 197 179L215 175Q211 111 196 86Q190 78 186 82ZM309 84Q296 102 296 176L313 181Q334 111 319 86Q315 79 309 84Z",
        eyes=((210, 246), (300, 246)), forehead=(255, 196), muzzle=(255, 287, 42, 24),
        bounds=_bounds(head=(145, 64, 222, 271)), feet=((200, 423), (317, 423)),
    ),
    "fox": SpeciesGeometry(
        body="M164 308Q178 272 222 268L287 269Q338 277 349 319Q370 406 318 427L195 428Q137 408 164 308Z",
        head="M154 200L135 119Q130 97 152 109L217 156Q255 145 294 156L357 108Q380 96 374 123L355 201L385 239Q369 290 255 332Q142 292 127 240Z",
        legs="M180 359Q202 348 225 363L232 421Q234 444 210 447L183 445Q167 438 176 420ZM287 364Q309 348 332 359L338 420Q349 440 328 445L302 447Q280 444 282 425Z",
        tail="M343 328C368 293 420 272 448 301C488 343 449 418 365 411L326 389Z",
        ears="M151 125L168 189L205 166ZM305 167L359 126L343 189Z",
        eyes=((213, 234), (297, 234)), forehead=(255, 181), muzzle=(255, 279, 50, 28),
        bounds=_bounds(head=(129, 106, 254, 227), tail=(328, 289, 139, 125)),
        feet=((203, 423), (311, 423)), eye_size=(12, 15),
    ),
    "badger": SpeciesGeometry(
        body="M143 313Q155 272 213 265L292 266Q351 271 371 318Q399 405 329 430L185 430Q117 405 143 313Z",
        head="M160 191Q142 165 158 148Q178 134 197 160Q255 142 313 160Q334 134 352 151Q365 168 348 192Q373 216 370 257Q368 317 255 337Q141 319 140 257Q135 218 160 191Z",
        legs="M173 366Q196 352 222 368L230 421Q233 443 207 446L178 444Q158 437 169 421ZM290 367Q314 352 341 367L345 421Q357 440 335 445L304 446Q280 443 284 425Z",
        tail="M361 353Q404 333 421 361Q416 388 374 396L351 380Z",
        ears="M164 157Q177 149 188 167L173 182Q156 175 164 157ZM322 167Q334 149 347 158Q354 174 338 182Z",
        eyes=((211, 244), (301, 244)), forehead=(255, 194), muzzle=(256, 290, 49, 27),
        bounds=_bounds(body=(137, 265, 242, 167), head=(140, 143, 231, 195)),
        feet=((201, 422), (316, 422)), eye_size=(11, 15),
    ),
    "fawn": SpeciesGeometry(
        body="M167 301Q179 272 220 263L284 260Q339 267 355 309Q381 400 322 422L195 423Q141 408 154 356Z",
        head="M181 189Q143 164 119 165Q98 171 117 196Q140 223 176 216Q161 245 176 280Q192 326 255 331Q318 326 335 280Q349 247 334 216Q370 223 393 195Q412 170 390 165Q366 164 329 189Q306 164 255 161Q204 164 181 189Z",
        legs="M184 351Q203 340 222 356L225 425Q230 444 210 447L187 445Q172 440 179 422ZM290 356Q310 340 329 353L334 423Q341 442 323 446L302 446Q285 443 286 426Z",
        tail="M345 332Q388 303 394 335Q387 363 354 368L339 353Z",
        ears="M125 177Q148 174 178 201L170 207Q140 211 125 177ZM332 201Q365 175 386 177Q373 210 341 207Z",
        eyes=((215, 239), (295, 239)), forehead=(255, 191), muzzle=(255, 285, 44, 27),
        bounds=_bounds(body=(153, 260, 213, 164), head=(110, 162, 291, 171)),
        feet=((204, 424), (312, 424)), eye_size=(11, 15),
    ),
    "raccoon": SpeciesGeometry(
        body="M147 311Q159 271 215 264L293 264Q350 272 369 316Q393 407 325 429L187 429Q121 406 147 311Z",
        head="M164 190Q143 161 161 145Q185 130 203 160Q256 147 309 160Q328 132 350 148Q368 163 349 192Q377 221 371 263Q366 321 256 338Q144 320 139 263Q134 220 164 190Z",
        legs="M174 366Q197 350 222 367L230 422Q235 443 208 446L176 443Q158 437 170 420ZM289 367Q314 352 338 366L344 420Q357 441 333 446L304 446Q279 443 283 424Z",
        tail="M348 329C396 312 454 320 463 351C474 400 418 430 355 410L337 384Z",
        ears="M167 154Q181 145 193 169L175 184Q158 174 167 154ZM320 169Q333 147 346 157Q354 174 337 185Z",
        eyes=((211, 244), (301, 244)), forehead=(255, 195), muzzle=(255, 290, 49, 27),
        bounds=_bounds(body=(141, 264, 235, 167), head=(139, 140, 234, 200), tail=(339, 320, 128, 101)),
        feet=((202, 422), (314, 422)), eye_size=(11, 15),
    ),
    "capybara": SpeciesGeometry(
        body="M144 306Q159 272 211 266L299 266Q359 277 377 323L382 367Q376 422 322 431L186 430Q125 416 130 366Z",
        head="M158 191Q145 168 163 154Q181 144 199 169L306 170Q328 144 348 158Q365 174 352 194Q370 213 370 249L366 285Q360 326 308 333L201 332Q149 324 144 283L140 245Q138 211 158 191Z",
        legs="M174 375Q196 363 221 376L228 423Q232 443 207 446L177 443Q158 437 169 421ZM292 377Q317 362 342 377L346 423Q358 441 334 446L304 445Q280 441 286 424Z",
        tail="M367 371Q391 360 395 380Q386 399 365 393Z",
        ears="M166 164Q178 155 188 176L176 190Q159 181 166 164ZM317 176Q330 157 342 169Q349 184 333 192Z",
        eyes=((205, 232), (305, 232)), forehead=(255, 184), muzzle=(255, 280, 72, 36),
        bounds=_bounds(body=(131, 266, 252, 166), head=(140, 153, 231, 182)),
        feet=((200, 423), (316, 423)), eye_size=(10, 13),
    ),
    "alpaca": SpeciesGeometry(
        body="M203 254Q255 237 308 256L312 311Q345 312 360 335Q382 354 368 380Q376 406 349 417Q338 437 309 427Q285 439 258 427Q225 440 202 426Q175 438 159 416Q133 408 146 382Q131 358 152 341Q165 316 199 315Z",
        head="M184 157L176 91Q177 67 193 76L226 135Q255 125 284 135L309 73Q324 62 329 84L323 158Q343 173 341 210Q341 266 294 281L217 281Q168 267 169 211Q164 178 184 157Z",
        legs="M181 362Q201 354 222 368L226 424Q232 445 211 448L188 447Q174 440 178 425ZM290 366Q312 352 333 365L338 426Q345 445 325 448L302 448Q285 444 287 428Z",
        tail="M352 338Q384 314 397 336Q396 357 367 370Z",
        ears="M187 91L198 146L215 140ZM296 141L318 88L313 153Z",
        eyes=((218, 211), (294, 211)), forehead=(256, 164), muzzle=(256, 251, 44, 25),
        bounds=_bounds(body=(143, 251, 230, 184), head=(169, 72, 173, 211)),
        feet=((203, 426), (314, 426)), eye_size=(11, 15),
    ),
    "otter": SpeciesGeometry(
        body="M173 308Q185 275 222 269L288 268Q331 276 344 312L365 377Q371 416 324 431L191 431Q143 417 148 379Z",
        head="M173 201Q154 180 169 164Q185 153 204 175Q254 157 308 176Q326 154 343 168Q356 184 339 202Q362 223 360 260Q360 321 256 336Q151 323 151 261Q149 224 173 201Z",
        legs="M174 365Q196 351 222 368L230 421Q233 442 207 445L176 442Q157 434 168 418ZM290 369Q316 353 339 366L346 419Q357 438 334 443L304 445Q280 443 284 425Z",
        tail="M344 361C397 362 433 393 461 413Q439 443 394 426L331 401Z",
        ears="M173 173Q186 164 195 183L181 195Q166 186 173 173ZM317 184Q328 168 339 179Q344 190 330 198Z",
        eyes=((213, 246), (297, 246)), forehead=(255, 196), muzzle=(255, 288, 52, 29),
        bounds=_bounds(body=(148, 269, 218, 163), head=(151, 163, 211, 175), tail=(332, 361, 130, 73)),
        feet=((201, 420), (316, 420)),
    ),
})

COLORS = MappingProxyType({
    "white": "#f5efe4", "cream": "#ead9b7", "ginger": "#df985d",
    "silver": "#b9bec9", "black": "#55525f", "chocolate": "#916950",
    "blue": "#8b9db8", "chestnut": "#b77b58", "bay": "#9e6d50",
    "palomino": "#d9b77f", "grey": "#b0b2b7", "brown": "#ae856b",
    "golden": "#d9ac68", "red": "#d98b51", "arctic": "#e3e7e8", "tan": "#c7a17b",
})
EYES = MappingProxyType({
    "brown": "#85604a", "amber": "#ce9946", "hazel": "#96945d",
    "green": "#79a187", "blue": "#75a7c3",
})
INK = "#655554"
CREAM = "#fff5df"
DARK = "#66565b"


class _Stream:
    """Small counter-based stream: no mutable state exists outside one render."""

    def __init__(self, species: str, seed: int, namespace: str):
        self.prefix = f"fosgen-pets-art-v2:{species}:{seed}:{namespace}:".encode("ascii")
        self.counter = 0

    def number(self, low: float, high: float) -> float:
        digest = sha256(self.prefix + str(self.counter).encode("ascii")).digest()
        self.counter += 1
        unit = int.from_bytes(digest[:8], "big") / (2**64 - 1)
        return low + (high - low) * unit


def _n(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _mix(color: str, target: str, fraction: float) -> str:
    components = [round(int(color[index:index + 2], 16) * (1 - fraction)
                        + int(target[index:index + 2], 16) * fraction)
                  for index in (1, 3, 5)]
    return "#" + "".join(f"{component:02x}" for component in components)


def _ellipse(x, y, rx, ry, fill, extra="") -> str:
    return f'<ellipse cx="{_n(x)}" cy="{_n(y)}" rx="{_n(rx)}" ry="{_n(ry)}" fill="{fill}"{extra}/>'


def _blob(stream: _Stream, x: float, y: float, rx: float, ry: float) -> str:
    # A closed, curved eight-lobe patch rather than repeated perfect circles.
    points = [(x + cos(index * pi / 4) * rx * stream.number(.77, 1.16),
               y + sin(index * pi / 4) * ry * stream.number(.77, 1.16))
              for index in range(8)]
    mids = [((points[i][0] + points[(i + 1) % 8][0]) / 2,
             (points[i][1] + points[(i + 1) % 8][1]) / 2) for i in range(8)]
    path = f"M{_n(mids[-1][0])} {_n(mids[-1][1])}"
    for point, mid in zip(points, mids):
        path += f"Q{_n(point[0])} {_n(point[1])} {_n(mid[0])} {_n(mid[1])}"
    return path + "Z"


def _pattern(appearance: Appearance, geom: SpeciesGeometry, part: str) -> str:
    stream = _Stream(appearance.species, appearance.pattern_seed, f"pattern:{part}")
    x, y, width, height = geom.bounds_for(part)
    kind = appearance.pattern
    species = appearance.species
    shapes: list[str] = []
    contrast = _mix(COLORS[appearance.palette], "#453b42", .50)

    if species == "fawn" and kind == "spotted":
        # Fawn spots are small ivory flecks on the shoulders and flanks,
        # deliberately distinct from a calf's broad, dark piebald patches.
        if part == "body":
            anchors = ((179, 309), (195, 313), (172, 326), (188, 332), (204, 337),
                       (165, 345), (179, 351), (164, 365), (173, 381), (185, 395),
                       (315, 313), (331, 308), (310, 333), (326, 330), (341, 324),
                       (332, 350), (346, 344), (348, 365), (340, 382), (326, 397))
            for cx, cy in anchors:
                shapes.append(_ellipse(cx + stream.number(-2, 2), cy + stream.number(-2, 2),
                                       stream.number(3.4, 5.2), stream.number(3.2, 4.8), CREAM))
        return "".join(shapes)
    if kind in {"bicolor", "pinto", "piebald", "broken", "calico", "tortoiseshell", "spotted"}:
        count = 2 if kind == "bicolor" else (6 if kind in {"calico", "tortoiseshell", "spotted"} else 4)
        colors = {"calico": ("#e2a469", "#64555a", CREAM),
                  "tortoiseshell": ("#b8804f", "#57484b"), "spotted": (contrast,)}.get(kind, (CREAM,))
        for index in range(count):
            cx, cy = stream.number(x, x + width), stream.number(y, y + height)
            scale = .12 if kind == "spotted" else .23
            rx = stream.number(width * scale * .6, width * scale * 1.3)
            ry = stream.number(height * scale * .7, height * scale * 1.6)
            shapes.append(f'<path d="{_blob(stream, cx, cy, rx, ry)}" fill="{colors[index % len(colors)]}"/>')
    elif kind in {"tabby", "stripe"}:
        if part == "head":
            if species == "badger":
                shapes.append('<path d="M229 145Q213 209 224 259L245 295L268 295L287 254Q298 201 281 145Z" fill="#f7efdf"/>')
                for ex, ey in geom.eyes:
                    shapes.append(_ellipse(ex, ey - 5, 31, 54, DARK,
                                           f' transform="rotate({-22 if ex < 256 else 22} {ex} {ey})"'))
            elif species == "raccoon":
                shapes.append('<path d="M157 207Q255 179 355 209L343 256Q255 284 169 254Z" fill="#665a5e"/>')
            else:
                for index in range(5):
                    cx = x + width * (.18 + index * .16)
                    top = y + height * .12 + stream.number(-6, 8)
                    shapes.append(f'<path d="M{_n(cx - 7)} {_n(top)}Q{_n(cx + 6)} {_n(top + 27)} {_n(cx)} {_n(top + 48)}Q{_n(cx + 22)} {_n(top + 22)} {_n(cx + 8)} {_n(top - 3)}Z" fill="{contrast}"/>')
        else:
            for index in range(6):
                cx = x + width * (index / 5) + stream.number(-9, 9)
                shapes.append(f'<path d="M{_n(cx - 10)} {_n(y - 8)}Q{_n(cx + 15)} {_n(y + height / 2)} {_n(cx - 8)} {_n(y + height + 9)}L{_n(cx + 13)} {_n(y + height + 12)}Q{_n(cx + 40)} {_n(y + height / 2)} {_n(cx + 11)} {_n(y - 8)}Z" fill="{contrast}" opacity=".7"/>')
    elif kind == "roan":
        for _ in range(78 if part == "body" else 36):
            cx, cy = stream.number(x, x + width), stream.number(y, y + height)
            shapes.append(f'<path d="M{_n(cx)} {_n(cy)}l1.8 -3.2" stroke="{CREAM}" stroke-width="2.5" stroke-linecap="round" opacity=".72"/>')
    elif kind == "dapple":
        for _ in range(13 if part == "body" else 7):
            cx, cy = stream.number(x, x + width), stream.number(y, y + height)
            d = _blob(stream, cx, cy, stream.number(7, 16), stream.number(7, 14))
            shapes.append(f'<path d="{d}" fill="none" stroke="{CREAM}" stroke-width="4.5" opacity=".65"/>')
    elif kind in {"sable", "shaded"}:
        cy = y + height * stream.number(.25, .6)
        shapes.append(f'<path d="M{x-15} {y-15}H{x+width+15}V{_n(cy)}Q{_n(x+width*.5)} {_n(cy+height*.5)} {x-15} {_n(cy)}Z" fill="{contrast}" opacity=".42"/>')
    elif kind == "pointed":
        if part == "head":
            shapes.extend((_ellipse(x + width * .24, y + height * .24, width * .20, height * .37, contrast),
                           _ellipse(x + width * .77, y + height * .24, width * .20, height * .37, contrast),
                           _ellipse(*geom.muzzle[:2], 34, 27, contrast)))
        elif part in {"legs", "tail"}:
            shapes.append(f'<rect x="{x-5}" y="{_n(y+height*.45)}" width="{width+10}" height="{height}" fill="{contrast}"/>')
    elif kind == "socks":
        if part == "legs":
            shapes.append('<path d="M150 400Q205 384 256 400Q313 382 355 400V460H150Z" fill="#fff2db"/>')
        elif part == "tail":
            shapes.append('<path d="M412 270Q392 343 431 405L490 440V260Z" fill="#fff2db"/>')
    elif kind == "mask":
        if part == "head":
            for ex, ey in geom.eyes:
                shapes.append(_ellipse(ex, ey - 2, 39, 28, contrast,
                                       f' transform="rotate({-15 if ex < 256 else 15} {ex} {ey})"'))
            if species == "badger":
                shapes.append('<path d="M239 160Q231 208 245 248L266 248Q281 205 271 159Z" fill="#f9efda"/>')
        elif part == "body":
            shapes.append(_ellipse(256, 322, 69, 53, CREAM, ' opacity=".65"'))
    elif kind == "bib":
        if part == "body":
            shapes.append('<path d="M199 279Q256 263 313 279L305 345Q285 378 256 390Q226 376 207 346Z" fill="#f9e8c8"/>')
    return "".join(shapes)


def _coat_light(appearance: Appearance, geom: SpeciesGeometry, part: str) -> str:
    stream = _Stream(appearance.species, appearance.pattern_seed, f"coat-light:{part}")
    x, y, width, height = geom.bounds_for(part)
    shapes = []
    # Large, soft value patches make seed changes visible even on solid coats.
    # A dark patch remains visible on white fur, and a light patch on black fur.
    for index in range(3):
        cx = stream.number(x + width * .07, x + width * .93)
        cy = stream.number(y + height * .08, y + height * .92)
        rx = stream.number(width * .20, width * .44)
        ry = stream.number(height * .22, height * .55)
        shapes.append(_ellipse(cx, cy, rx, ry, "url(#soft-light)" if index == 0 else "url(#soft-shade)"))
    for _ in range(9):
        cx, cy = stream.number(x, x + width), stream.number(y, y + height)
        shapes.append(f'<path d="M{_n(cx-3)} {_n(cy+2)}q3 -5 6 -1" fill="none" stroke="#fff9ea" stroke-width="1.7" stroke-linecap="round" opacity=".29"/>')
    return "".join(shapes)


def _native(appearance: Appearance, geom: SpeciesGeometry, part: str) -> str:
    species = appearance.species
    if part == "tail":
        if species == "fox":
            return '<path d="M431 289Q409 332 439 362L465 366L491 310Z" fill="#fff2db"/>'
        if species == "raccoon":
            return '<g fill="#665a5e" opacity=".83"><path d="M365 315L387 314L408 426L384 426Z"/><path d="M407 312L428 316L449 415L426 424Z"/><path d="M449 328L473 348L469 389L454 405Z"/></g>'
        return ""
    if part != "head":
        return ""
    if species == "fox":
        return '<path d="M137 239Q192 253 230 290L255 319L283 288Q328 252 376 238Q363 299 255 333Q148 299 137 239Z" fill="#fff0d8"/>'
    if species == "raccoon":
        return '<path d="M160 233Q194 206 229 221L242 254Q207 277 168 259ZM270 254L282 221Q320 205 352 233L345 261Q304 278 270 254Z" fill="#5e545c"/>'
    if species == "badger" and appearance.pattern == "mask":
        return '<path d="M151 198Q179 177 212 193L230 213Q183 210 150 229ZM280 213L300 193Q335 176 360 200L362 230Q327 208 280 213Z" fill="#f8ecd8"/>'
    if species == "fawn":
        return '<path d="M172 262Q197 264 218 292L238 322Q186 317 172 280ZM275 322L293 293Q317 265 339 261L333 282Q317 318 275 322Z" fill="#efdbba"/>'
    return ""


def _muzzle(appearance: Appearance, geom: SpeciesGeometry) -> str:
    species = appearance.species
    x, y, rx, ry = geom.muzzle
    color = "#edc0b0" if species == "calf" else _mix(COLORS[appearance.palette], CREAM, .63)
    if species in {"fox", "raccoon", "otter", "badger", "fawn"}:
        color = "#f4e6ca"
    shapes = [_ellipse(x, y, rx, ry, color, ' stroke="#826d63" stroke-opacity=".22" stroke-width="2"')]
    if species in {"foal", "calf", "capybara"}:
        nose_space = 25 if species == "calf" else 24
        shapes += [_ellipse(x - nose_space, y - 4, 6, 4.5, "#8c6b65"),
                   _ellipse(x + nose_space, y - 4, 6, 4.5, "#8c6b65"),
                   f'<path d="M{x-14} {y+13}Q{x} {y+21} {x+14} {y+13}" fill="none" stroke="#8b6962" stroke-width="3" stroke-linecap="round"/>']
    else:
        nose = "#b27a76" if species in {"kitten", "bunny", "alpaca"} else "#655250"
        shapes += [f'<path d="M{x-9} {y-9}Q{x} {y-14} {x+9} {y-9}Q{x+9} {y-3} {x} {y+2}Q{x-9} {y-3} {x-9} {y-9}Z" fill="{nose}"/>',
                   f'<path d="M{x} {y+1}V{y+9}M{x-16} {y+8}Q{x-8} {y+17} {x} {y+9}Q{x+8} {y+17} {x+16} {y+8}" fill="none" stroke="#81635b" stroke-width="2.8" stroke-linecap="round"/>']
    if species in {"kitten", "otter", "bunny"}:
        for side in (-1, 1):
            for offset in (-3, 7):
                shapes.append(f'<path d="M{x+side*(rx-6)} {y+offset}l{side*26} {-4 if offset < 0 else 3}" fill="none" stroke="#8d7970" stroke-width="2" stroke-linecap="round" opacity=".65"/>')
    return '<g data-layer="muzzle">' + "".join(shapes) + '</g>'


def _marking(appearance: Appearance, geom: SpeciesGeometry) -> tuple[str, str]:
    kind = appearance.marking
    variant = appearance.marking_variant
    if kind == "none":
        return "head", ""
    if kind in {"sock_left", "sock_right"}:
        x, y = geom.feet[0 if kind == "sock_left" else 1]
        top = y - 19 - (variant % 9)
        return "legs", f'<path d="M{x-25} {top}Q{x-8} {top-5} {x} {top}Q{x+12} {top+5} {x+25} {top-2}V465H{x-25}Z" fill="{CREAM}"/>'
    x, y = geom.forehead
    x += (variant % 7) - 3
    y += ((variant // 7) % 5) - 2
    radius = 11 + (variant % 5)
    if kind == "star":
        points = [(x + sin(index * pi / 5) * (radius if index % 2 == 0 else radius * .45),
                   y - cos(index * pi / 5) * (radius if index % 2 == 0 else radius * .45))
                  for index in range(10)]
        d = "M" + "L".join(f"{_n(px)} {_n(py)}" for px, py in points) + "Z"
    elif kind == "heart":
        d = f"M{x} {y+radius}C{x-radius*2} {y-radius//2} {x-radius} {y-radius*2} {x} {y-radius//2}C{x+radius} {y-radius*2} {x+radius*2} {y-radius//2} {x} {y+radius}Z"
    elif kind == "blaze":
        width = 7 + (variant % 5)
        d = f"M{x-width} {y-23}Q{x+width} {y-7} {x+width-3} {y+23}L{x-6} {y+27}Q{x-3} {y+2} {x-width} {y-23}Z"
    else:
        ex, ey = geom.eyes[0]
        ink = _mix(COLORS[appearance.palette], "#674d48", .63)
        return "head", "".join(_ellipse(ex - 15 + i * 8, ey + 27 + (i % 2) * 5,
                                         2.6 + (variant % 4) * .2, 2.7, ink)
                               for i in range(3))
    return "head", f'<path d="{d}" fill="{CREAM}" stroke="#bba78e" stroke-opacity=".2" stroke-width="1"/>'


def _eyes(appearance: Appearance, geom: SpeciesGeometry) -> str:
    rx, ry = geom.eye_size
    shapes = []
    for x, y in geom.eyes:
        shapes += [_ellipse(x, y, rx + 2, ry + 2, "#554748"),
                   _ellipse(x, y, rx, ry, EYES[appearance.eyes]),
                   _ellipse(x, y + 2, rx * .50, ry * .77, "#3f383d"),
                   _ellipse(x - 3, y - 6, 4.5, 4.5, "#fffdf5"),
                   _ellipse(x + 4, y + 7, 2, 2, "#fff7dc", ' opacity=".82"'),
                   f'<path d="M{x-rx+2} {y-ry-9}q{rx-2} -5 {rx*2-4} 0" fill="none" stroke="#76615a" stroke-width="2.5" stroke-linecap="round" opacity=".55"/>']
    return '<g data-layer="eyes">' + "".join(shapes) + '</g>'


def _details(appearance: Appearance, geom: SpeciesGeometry) -> str:
    species = appearance.species
    base = COLORS[appearance.palette]
    if species == "foal":
        return '<path d="M224 153Q220 130 238 138Q249 117 260 140Q281 132 289 161L272 180L258 163L246 178L241 159Z" fill="#71594f" stroke="#655554" stroke-width="3" stroke-linejoin="round"/>'
    if species == "alpaca":
        return f'<path d="M182 170Q174 152 190 145Q192 128 210 132Q221 115 236 127Q255 111 270 127Q289 116 301 134Q321 130 328 150Q342 159 327 176" fill="{_mix(base, CREAM, .25)}" stroke="#8d7c70" stroke-width="3" stroke-linecap="round"/>'
    if species in {"puppy", "fawn", "calf"}:
        return '<path d="M239 165q5 -14 12 -5q8 -16 15 -1q10 -9 15 7" fill="none" stroke="#8b7164" stroke-width="3" stroke-linecap="round" opacity=".5"/>'
    return ""


def render_v2(appearance: Appearance) -> str:
    """Return a safe, standalone 512 px v2 portrait of a validated recipe."""
    validate_appearance(appearance)
    if appearance.asset_version != 2:
        raise ValueError("Invalid asset version")
    species = appearance.species
    if species not in SPECIES_GEOMETRY:
        raise ValueError("No v2 art template for species")
    geom = SPECIES_GEOMETRY[species]
    base = COLORS[appearance.palette]
    light = _mix(base, "#fff9e8", .25)
    shadow = _mix(base, "#67515a", .19)
    outline = _mix(base, INK, .70)
    parts = ("tail", "body", "legs", "head")
    defs = [f'<clipPath id="fur-{part}"><path d="{getattr(geom, part)}"/></clipPath>' for part in parts]
    defs += [f'<linearGradient id="coat" x1="0" y1="0" x2=".3" y2="1"><stop stop-color="{light}"/><stop offset=".57" stop-color="{base}"/><stop offset="1" stop-color="{shadow}"/></linearGradient>',
             '<radialGradient id="soft-light"><stop stop-color="#fff9e8" stop-opacity=".58"/><stop offset="1" stop-color="#fff9e8" stop-opacity="0"/></radialGradient>',
             '<radialGradient id="soft-shade"><stop stop-color="#664d59" stop-opacity=".27"/><stop offset="1" stop-color="#664d59" stop-opacity="0"/></radialGradient>',
             '<radialGradient id="paper-glow"><stop stop-color="#fffcf1"/><stop offset="1" stop-color="#f8eae5"/></radialGradient>']
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" data-asset-version="2">',
           '<defs>' + "".join(defs) + '</defs>',
           '<rect width="512" height="512" rx="40" fill="url(#paper-glow)"/>',
           '<circle cx="255" cy="238" r="187" fill="#fffcf4" opacity=".64"/>',
           '<g fill="#dfc9bc" opacity=".65"><path d="M86 226l4 -10l4 10l10 4l-10 4l-4 10l-4 -10l-10 -4Z"/><circle cx="412" cy="188" r="4"/><circle cx="116" cy="350" r="3"/><path d="M398 127l3 -7l3 7l7 3l-7 3l-3 7l-3 -7l-7 -3Z"/></g>',
           '<ellipse cx="256" cy="446" rx="152" ry="17" fill="#ddc7b7" opacity=".5"/>',
           '<ellipse cx="256" cy="444" rx="118" ry="9" fill="#bba896" opacity=".16"/>']
    marking_part, marking = _marking(appearance, geom)
    for part in parts:
        path = getattr(geom, part)
        svg += [f'<path data-layer="base" data-part="{part}" d="{path}" fill="url(#coat)" stroke="{outline}" stroke-width="5.5" stroke-linejoin="round"/>',
                f'<g data-layer="pattern" data-part="{part}" clip-path="url(#fur-{part})">{_pattern(appearance, geom, part)}</g>',
                f'<g data-layer="coat-light" data-part="{part}" clip-path="url(#fur-{part})">{_coat_light(appearance, geom, part)}</g>',
                f'<g clip-path="url(#fur-{part})">{_native(appearance, geom, part)}</g>']
        if part == "legs" and marking_part == "legs" and marking:
            svg.append(f'<g data-layer="marking" data-part="legs" clip-path="url(#fur-legs)">{marking}</g>')
        svg.append(f'<path d="{path}" fill="none" stroke="{outline}" stroke-width="4" stroke-linejoin="round" opacity=".65"/>')
    svg += [f'<path d="{geom.ears}" fill="#dca8a0" stroke="#b38a82" stroke-width="2" stroke-linejoin="round" opacity=".8"/>',
            _details(appearance, geom), _muzzle(appearance, geom)]
    if marking_part == "head" and marking:
        svg.append(f'<g data-layer="marking" data-part="head" clip-path="url(#fur-head)">{marking}</g>')
    # Facial paint and long muzzles always precede the final, anchored eyes.
    svg += [_eyes(appearance, geom), '</svg>']
    return "".join(svg)

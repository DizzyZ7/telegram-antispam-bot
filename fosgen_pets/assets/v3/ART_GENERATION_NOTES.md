# Graphics v3 — twelve original plush masters

Session date: 2026-11-15.

## Provenance and use

These twelve original images were generated specifically for this project using the built-in `image_gen.imagegen` tool (`tools.image_gen__imagegen`) with `transparent_background: true`. No input/reference image, existing character design, external stock image, logo, or third-party visual asset was supplied. Each species was generated once using a separate tool call. All twelve first outputs passed the art review; no generative edits or alternatives were used.

The built-in tool does not expose its underlying image model name or version, so neither is invented here. Use is subject to the service terms applicable to the generation. This is a provenance statement, not a separate public-domain dedication or a guarantee of copyright exclusivity.

The source PNGs remain in the generation output directory outside the repository: `/workspace/scratch/166b491e62f3/generated_images/`. They are 1254×1254 RGBA. Each shipped master is a 512×512 lossless WebP. Twelve final masters total 1,411,712 bytes. No large source PNG is intended for Git.

All source files were inspected with `view_image` before packaging. Packaging resized the complete source square to 432×432 with Lanczos and centered it at (40,40) on an otherwise transparent 512×512 canvas. This adds a consistent practical margin without changing anatomy, colors, pose, or background. The generated alpha channel was retained in resampling, and the packaged alpha was verified byte-for-byte after lossless WebP decode. No manual painting, recoloring, retouching, cutout, or generative edit was applied.

## Files

| Species | Final master | Original source PNG basename |
|---|---|---|
| kitten | kitten-master.webp | exec-73aa4e2f-6354-4644-acee-41fa439ccfb3.png |
| puppy | puppy-master.webp | exec-28d0cd75-42c7-457e-8c7a-cd6d844b6f8e.png |
| fox | fox-master.webp | exec-06003053-e100-4492-8c12-5edd0fa03064.png |
| badger | badger-master.webp | exec-8ef6e6ac-b67c-4c6d-9477-977e118c9812.png |
| capybara | capybara-master.webp | exec-a42f3bba-8655-42b4-9005-6a300c394ade.png |
| otter | otter-master.webp | exec-f1535bff-a32a-46a0-8146-05d75215d703.png |
| foal | foal-master.webp | exec-5db0ac8c-4a37-4749-98f8-ce3befb6ac4d.png |
| calf | calf-master.webp | exec-832c6c67-d031-4e70-8e84-d007de11f02d.png |
| bunny | bunny-master.webp | exec-99d2d9a0-a071-42ad-bee2-3df94c77ba21.png |
| fawn | fawn-master.webp | exec-6bfd61fc-8f66-4f12-8bd5-f8515ba2cffc.png |
| raccoon | raccoon-master.webp | exec-9fc1eec8-e3c3-4aa0-bbf0-a7afe4fcda98.png |
| alpaca | alpaca-master.webp | exec-81e1964b-d520-48cf-aeb5-74028515807e.png |

Final SHA-256 checksums, sizes, alpha bounds, and approximate semantic coordinates are in `anchor-data.json`.

## Runtime coordinate notes

All boxes in `anchor-data.json` use the final 512×512 coordinate space and `[left, top, right, bottom]`; right/bottom are exclusive.

- `head` includes the ear silhouette; `face` excludes long or side-projecting ears.
- `body`, `tail`, `eye_boxes`, `nose_boxes`, and `feetpoints` are manually read semantic guides, not segmentation masks. Intersect them with the master alpha and use soft masks.
- Head and body boxes overlap at the neck. Do not paint hard rectangles into the art.
- Eye boxes surround the rendered eye, so preserve dark pupils and white specular highlights when recoloring the brown iris.
- Capybara, calf, foal, and alpaca have separate nostrils; retain both and the existing mouth. Do not invent a central button nose.
- Capybara has no tail region; `tail` is null.
- `alpha_bbox` is the bounding box of any nonzero alpha. `bbox` uses alpha >= 16/255 to ignore nearly transparent edge residue. Neither means the sprite has been cropped.

## Complete generation prompts

The text below is exactly the complete prompt passed for each master. Every call also set `transparent_background: true`. No reference image parameters were provided.

### kitten

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one small plush KITTEN, unmistakably feline: soft triangular ears, gently rounded feline forehead, small short cat muzzle, fine whiskers, compact seated haunches and neat cat forepaws, a visible softly fluffy curved cat tail beside its body. Ears triangular rather than teddy round ears, no lynx tufts, no dog muzzle. Maintain natural kitten features with a collectible plush interpretation.
```

### puppy

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one small plush PUPPY, unmistakably canine: soft floppy drooping ears, clear gentle projecting dog muzzle with little nose, broad puppy brow, short velvety fur, compact seated haunches and neat dog forepaws, short relaxed tail visible. The head must read as a puppy, not a teddy bear or a kitten. Natural dog anatomy simplified into an irresistibly premium collectible plush.
```

### fox

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one small plush FOX, unmistakably foxlike: soft tall triangular fox ears, a narrow cheek-and-forehead shape, distinctly tapered fox muzzle with little dark-brown nose, fine whiskers, slender neat fox forepaws and rounded compact seated body. A long substantial plush bushy fox tail is visibly curled beside the body without hiding the front paws. Fox's WHOLE coat and tail are ivory/off-white without orange, black ear tips, dark legs, white-tip contrast or any color pattern. Premium collectible plush with clear fox silhouette, not kitten or teddy bear.
```

### badger

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original plush BADGER. Essential silhouette: low, stout, heavy compact haunches, short broad-set forelegs, small round ears on the sides of the head, and a noticeably elongated wedge-shaped badger face tapering to a long low muzzle and small dark-brown nose. Calm expressive dark-brown toy eyes, delicate whiskers. Its body is low and broad in a grounded seated/resting pose with the short forepaws planted, not upright like a teddy bear. Include the little thick short badger tail only if visible naturally. Fur is ENTIRELY off-white/ivory, no black facial mask or eye-stripes; badger pattern will be added later in game. Species form must still read as badger through the long wedge face, tiny ears, and low stout anatomy.
```

### capybara

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original plush CAPYBARA. Anatomically unmistakable capybara, deliberately different from any teddy bear: a large horizontally elongated boxy head, broad squared rectangular barrel muzzle with a flattened vertical front, long straight flat bridge from forehead to snout, two subtle small dark-brown nostrils near the front of the broad muzzle rather than a round button nose. Tiny rounded ears positioned high at the upper lateral back corners of its head; small gently expressive brown/black eyes set high and widely spaced. Smooth low rounded barrel body, short stubby capybara forelegs and slightly webbed toe-shaped feet planted under it; no round bear paws, no exposed pad symbols. Grounded quiet resting haunches/front-quarter pose, not tall upright teddy sitting. Capybara has no visible long tail, no whisker-cheek balls, no round bear muzzle. Short velvety fur all ivory, including muzzle and ears, with delicate natural fur shading. Premium collectible plush interpretation, recognizable squared capybara profile with both eyes still visible.
```

### otter

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original plush OTTER. Anatomically unmistakable otter, not a teddy bear: an elongated gently pear-shaped body, subtle narrow shoulders, tiny rounded ears tight to the sides of a smooth low oval head, a distinct flattened little whiskered otter muzzle with two subtle whisker cushions and a small broad dark-brown nose. Soft visible fine whiskers, calm natural glossy brown/black toy eyes. Short forelegs with neat elongated otter forefeet held compactly planted close together; rounded elongated haunches with small natural otter hindfeet, not round bear paws. Essential prominent long THICK tapered otter tail lies curved beside the body, tapering smoothly from a wide muscular base to a narrow tip; tail must be fully visible. Gently upright grounded front-quarter seated otter pose, full body shown. Entire fur coat and tail ivory/off-white, no contrasting muzzle, belly, black feet or markings. Premium sculpted collectible plush with an elongated otter silhouette.
```

### foal

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush FOAL, a baby horse, unmistakably equine: elongated gentle horse face with a wide tapering horse muzzle and two subtle dark-brown nostrils, upright soft horse ears rather than cat triangles, a short wispy ivory mane and a small ivory horse tail. Compact rounded baby-horse body with four slender but softly padded equine legs, clear little smooth ivory hooves rather than rounded paws, no claws or paw pads. Natural innocent dark-brown toy eyes with small iris and highlights, not exaggerated cartoon eyes. Gently standing grounded in a front three-quarter view keeping both eyes visible, all four hooves fully visible and close to the body footprint. A slightly oversized soft equine head, not a big dog, deer, unicorn, pony brand character, or rocking horse. Entire coat, mane, tail, and hooves ivory/off-white, no brown patches, blaze, socks, spots, saddle, horseshoes, horn, or accessories. Entire animal must fit in the square with transparent margin.
```

### calf

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush CALF, a baby cow, unmistakably bovine: a broad gentle calf forehead, large soft ears extending sideways and slightly drooping, a broad rounded-rectangular bovine muzzle with a flattened soft front, paired small subtle dark-brown nostrils and a little relaxed mouth. No central dog-like button nose. Short compact rounded calf body with four short stout bovine legs and clearly divided/cloven little ivory hooves, never round bear paws. Small soft short cow tail with a little ivory tuft visible naturally. A grounded gentle front-quarter standing pose with all four hooves fully visible close beneath the body, both eyes visible. Entire animal including muzzle, inside ears, coat, hooves and tail is ivory/off-white, no black or brown patches, spots, pink muzzle, horns, nose ring, bell, collar, or accessories. Cute but believable calf proportions rendered as a premium soft plush collectible. Do not turn it into a sheep, dog, deer, or teddy bear.
```

### bunny

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush BUNNY, unmistakably a rabbit: two long soft upright rabbit ears with rounded tips, compact rounded rabbit forehead and cheeks, a very short delicate rabbit muzzle with a small dark warm-brown Y-shaped rabbit nose and fine whiskers, natural dark-brown glass toy eyes. A compact rounded seated rabbit body, tiny neat front paws together, longer broad rabbit hindfeet planted to either side, and a small spherical fluffy cottontail visibly peeking beside the haunch. The ears and all paws/tail are fully visible with clear transparent space above the ears; keep head compact enough that the long ears fit comfortably. Entire fur is ivory/off-white including inside ears, muzzle, underside, paws and tail, no colored ear linings, patches or gray spots. Soft short velvet plush body with a slightly fluffier cottontail, no clothes, no carrots, no flowers, no props. No anime face and no teddy bear paws or nose.
```

### fawn

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush FAWN, a baby deer, unmistakably cervid: big soft leaf-shaped deer ears spreading upward-outward, a delicate narrow deer muzzle with a small dark warm-brown nose, a slender soft neck and a compact rounded fawn torso. Four graceful slender but softly plush deer legs with small smooth divided ivory hooves, no round bear paws. Short little deer tail visible naturally. Gentle large but natural dark-brown glass toy eyes, innocent calm gaze, both eyes visible. Grounded relaxed front three-quarter standing pose, all four hooves entirely visible and grouped near the body footprint. Entire fur coat and ears, muzzle, belly, tail and hooves ivory/off-white, NO white-spotted fawn pattern because color markings will be added later, no brown coat, no antlers, no horns, no collar or props. Anatomy should read as a distinct fawn, not a horse, calf, dog, kangaroo, or teddy bear. Premium semi-realistic plush sculpt with believable proportions.
```

### raccoon

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush RACCOON. Essential raccoon anatomy without its later color markings: a wide rounded yet gently tapering raccoon head with broad plush cheek planes and a distinct short pointed raccoon snout, small close rounded ears near the upper head sides, a small warm dark-brown nose, natural dark-brown glossy toy eyes. Low compact rounded seated raccoon body with short forelegs and small dexterous raccoon forefeet showing neat little finger-like toes, not human hands and not round teddy bear paws. A substantial medium-long thick bushy raccoon tail is fully visible curved beside the haunch, tapering to a rounded tip. Body has a low grounded curious front-quarter seated posture. ENTIRE coat and tail must be neutral ivory/off-white, including eye area, muzzle, ears and paws: NO black eye mask, NO dark socks, NO tail rings or stripes; runtime will add species coloration. Species silhouette should still be raccoonlike through broad cheeks, little rounded ears, pointed snout, nimble forefeet and thick tail. No teddy, no fox ears, no red-panda markings, no props.
```

### alpaca

```text
Use case: stylized-concept
Asset type: original premium collectible plush animal master for an offline pet game, one isolated whole animal, square 1024 x 1024 canvas.
Scene/backdrop: genuinely transparent background with alpha, absolutely no floor, ground cast shadow, scene, vignette or checkerboard painted into image.
Style/medium: exceptional semi-realistic sculpted 3D plush toy illustration, polished collectible product rendering. Rounded compact body, oversized but anatomically believable head, naturally sculpted volume, delicate short soft velvety ivory fur, visible high-quality fibers at edges, fine whiskers where appropriate. Gentle diffuse neutral studio lighting, soft ambient occlusion inside the toy's form, calm affectionate expression. No graphic outlines, no flat vector look.
Color palette: the ENTIRE fur coat must be neutral off-white / ivory, including ears, tail, muzzle, feet and underside. No colored markings, black masks, patches, spots, stripes, gray socks or other coat colors. Species-correct small dark warm-brown toy nose. Two glossy near-black/dark-brown toy eyes with small subtle brown irises and natural tiny white studio highlights; not anime eyes, no exaggerated iris size.
Composition/framing: entire animal fully visible from ear tips to paw tips and tail; a simple grounded seated pose with compact forefeet, looking at the viewer, front or very gentle three-quarter view keeping both eyes visible. Centered in a square, clear silhouette inside approximately x=65..945 and y=65..945, roughly 10 percent transparent margin on all sides. Do not crop any anatomy. Anatomical animal paws without exposed teddy-bear paw-pad symbols.
Constraints: original design, no imitation of any branded character or toy. No props, collar, clothing, bow, jewelry, tags, logos, text, watermark, accessories, platform, pedestal, or frame. No hard border or halo. Exactly one animal. This is a neutral fur master to recolor later; retain all volumetric fur shading in ivory.
Subject: one original collectible plush ALPACA, unmistakably alpaca: a long gracefully upright slender neck joining a compact softly rounded body, a small soft alpaca head with a slight woolly forehead tuft, two upright elongated leaf-shaped alpaca ears with subtly inward-curving tips, and a gentle blunt split-upper-lip alpaca muzzle with tiny paired dark-brown nostrils and a visible delicate mouth seam. Natural glossy dark-brown glass toy eyes with small subtle irises, calm gaze. Four slender straight plush alpaca legs ending in small natural divided ivory camelid toes/hooves, never round dog or bear paws. Little short alpaca tail visible naturally. A grounded relaxed front-quarter standing pose with all four feet visible, both eyes visible and entire neck/ears fitting comfortably in square with transparent margin. Ivory/off-white fur everywhere including muzzle, ears, legs and hooves, soft premium velvety fleece lightly fluffy around forehead/body but not exaggerated tightly wound sheep curls. No brown patches, black hooves, pink muzzle, blanket, saddle, collar, yarn, clothing or props. Do not make a sheep, goat, horse, or giraffe.
```


## Visual quality review

- Twelve distinct original whole-body silhouettes with neutral ivory fur and softly modeled 3D volume.
- Kitten: triangular ears, compact cat paws, fine whiskers, curved fluffy tail.
- Puppy: drooping ears, canine muzzle, compact dog forepaws.
- Fox: narrow cheek/forehead, tall ears, tapered muzzle, large bushy tail.
- Badger: low broad stance, elongated wedge muzzle; black facial stripes deferred to runtime.
- Capybara: broad boxy barrel muzzle, tiny high lateral ears, separate nostrils, low barrel body.
- Otter: elongated pear body, tiny lateral ears, whiskered muzzle, substantial tapered tail.
- Foal: equine face, short mane, four horse legs and hooves, fine horse tail.
- Calf: lateral ears, broad bovine muzzle, paired nostrils, cloven hooves, short tufted tail.
- Bunny: long upright ears, rabbit face/feet, spherical cottontail.
- Fawn: leaf-shaped deer ears, delicate muzzle, slim four-legged stance, little tail; coat spots deferred to runtime.
- Raccoon: rounded broad cheek planes, pointed short snout, dexterous small forefeet, thick curved tail; eye mask/tail rings deferred to runtime.
- Alpaca: long upright neck, elongated camelid ears, split-lip muzzle, four small divided feet, short tail.
- Transparent backgrounds contain no floor, cast shadow, text, props, branding, clothing, pedestal, or frame.
- Full-resolution source previews can show very faint RGB residue in almost-transparent edge pixels. No high-saturation pixels with alpha greater than 10/255 were found. These were left unchanged to retain generated alpha and fine fur/whisker edges.

The all-twelve contact sheet is an analysis preview outside the repository: `/workspace/scratch/166b491e62f3/generated_images/v3-all-twelve-contact-sheet.png`. It composites the masters onto neutral cards only for visual inspection; cards and labels are not in any master.

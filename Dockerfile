FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000

RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core ffmpeg libcairo2 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . ./
# Exercise the actual Cyrillic RGB renderer inside the built production image,
# not just dependency installation. Avoid silent broken owner mockups.
RUN python -c "from io import BytesIO; from PIL import Image; from writers_submission.promo import render_palette_png; data=render_palette_png(['#5B67F1','#EF86AC','#78CFBC','#FFC777'], title='я люблю пуушукппу'); im=Image.open(BytesIO(data)); im.verify(); assert im.size == (1200,450)"
RUN python -c "from fosgen_pets.core import SPECIES, build_appearance; from fosgen_pets.portrait import render_portrait; assert all(render_portrait(build_appearance(s, 'docker-smoke')).png.startswith(bytes.fromhex('89504e470d0a1a0a')) for s in SPECIES)"
RUN python -c "from fosgen_pets.core import V3_SPECIES, build_appearance; from fosgen_pets.portrait import render_portrait; assert all(render_portrait(build_appearance(s, 'docker-plush-smoke', asset_version=3)).renderer_version == 'fosgen-plush-v3' for s in V3_SPECIES)"
RUN mkdir -p /app/data

EXPOSE 3000
CMD ["python", "main.py"]

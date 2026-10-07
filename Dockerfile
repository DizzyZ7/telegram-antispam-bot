FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    WRITERS_SUBMISSION_PORT=3000

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN mkdir -p /app/data

EXPOSE 3000
CMD ["python", "main.py"]

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

# Seed demo heat-map data so /heatmap/* returns something on first deploy.
# Render's free tier has ephemeral disk, so real tracking vanishes on redeploy
# anyway. A deterministic seeded snapshot baked into the image is the simplest
# way to make the heat-map render on the live site.
RUN python scripts/seed_fake_transitions.py

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

# Repo-root Dockerfile (not backend/Dockerfile): app/nodes/office_skill.py and
# app/nodes/validation.py resolve paths as `Path(__file__).resolve().parents[3]` and
# expect mnt/skills/* as a sibling of backend/ -- the image needs both directories in
# the same relative layout as this repo, not just backend/ alone.
#
# NOTE: mnt/skills/public/* carries an Anthropic proprietary license restricting
# redistribution/copying outside Anthropic's own Services (see each skill's
# LICENSE.txt) -- baking it into an image you distribute/deploy carries the same
# consideration already made explicit for this repo's git history.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY mnt/ mnt/

WORKDIR /app/backend

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# No --reload: containers should run a stable single process, not a dev auto-restart
# loop. Configure ANTHROPIC_API_KEY / SERVICE_API_KEY via `docker run -e` or
# `--env-file`, never baked into the image -- backend/.env is excluded (.dockerignore).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

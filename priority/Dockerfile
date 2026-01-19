FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scheduler.py /app/scheduler.py
COPY worker.py /app/worker.py
COPY submit_runs.py /app/submit_runs.py

ENV DATA_DIR=/data
ENV RESULTS_DIR=/results

EXPOSE 8000

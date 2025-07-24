# Dockerfile

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libffi-dev libssl-dev libjpeg-dev zlib1g-dev git

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

EXPOSE 4040

CMD ["python", "app.py"]

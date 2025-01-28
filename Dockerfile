FROM python:3.12-slim

WORKDIR /Users/brianpearson/charging_automation/docker

COPY charging_automation/* ./

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-u", "./main.py"]

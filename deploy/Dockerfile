FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py load_results.py ./
COPY static ./static
EXPOSE 8000
CMD ["gunicorn","app:app","-k","uvicorn.workers.UvicornWorker","-w","4","-b","0.0.0.0:8000","--timeout","30","--access-logfile","-"]

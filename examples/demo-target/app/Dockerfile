FROM python:latest
ENV API_TOKEN=supersecretvalue123
ADD https://example.com/install.sh /tmp/install.sh
RUN curl -sSL https://example.com/bootstrap.sh | bash
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
CMD ["python", "app.py"]

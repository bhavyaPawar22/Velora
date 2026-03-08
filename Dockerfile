FROM python:3.13-slim

WORKDIR /

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

COPY --chown=user requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

EXPOSE 7860

CMD ["gunicorn", "--bind", "0.0.0.0:7860", "app:app"]